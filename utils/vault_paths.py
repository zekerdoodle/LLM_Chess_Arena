"""
Vault Path Utilities

Provide canonical vault root discovery and legacy-path migration helpers.
Theo stores every piece of user data under the project-local ``./vault``
directory. Older builds wrote to ``layer3_longterm/vault``; this module
centralizes the logic that keeps callers pointed at the canonical location
while surfacing explicit warnings when legacy paths are encountered.

Usage highlights
----------------
- ``get_vault_root`` resolves the vault root using config/env overrides.
- ``rebase_legacy_vault_path`` rewrites an absolute legacy path so code can
  continue operating on the canonical vault while emitting a migration warning.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Union
import os
import shutil

from utils.logger import get_logger

logger = get_logger(__name__)

LEGACY_VAULT_PATH = Path("layer3_longterm") / "vault"
BACKUPS_SUBDIR = "backups"
CLONES_SUBDIR = "clones"
UPLOADS_SUBDIR = "uploads"
PathInput = Union[str, Path]


def get_vault_root(config: Optional[dict] = None) -> Path:
    """Return the canonical vault root directory.

    Resolution order:
        1) ``THEO_VAULT_ROOT`` environment override.
        2) ``config['vault_root']`` when provided.
        3) Project-local ``./vault`` (created when missing).

    If a legacy ``layer3_longterm/vault`` folder is detected, a warning is
    emitted so operators know to migrate the data and remove the directory.
    Callers always receive the canonical ``./vault`` path.
    """
    override = _resolve_env_override()
    if override is not None:
        return override

    cfg = _load_config_dict(config)
    explicit = _resolve_config_override(cfg)
    if explicit is not None:
        return explicit

    preferred = Path("vault")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Creation failing here will bubble up on use; we still warn for legacy.
        pass

    _warn_if_legacy_vault_present(preferred)
    return preferred


def get_backups_root(config: Optional[dict] = None) -> Path:
    """Return the canonical directory for rotating backups.

    The backups root always lives inside the vault (``./vault/backups`` by
    default) and is guaranteed to exist alongside a local ``.gitignore`` that
    keeps rotating artifacts out of version control. Any legacy top-level
    ``./backups`` directory is migrated automatically the first time this
    helper runs.
    """

    vault_root = get_vault_root(config)
    vault_root = vault_root if vault_root.is_absolute() else (Path.cwd() / vault_root)

    backups_root = (vault_root / BACKUPS_SUBDIR).resolve()
    try:
        backups_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    _ensure_backups_gitignore(backups_root)
    _migrate_legacy_backups(backups_root)
    return backups_root


def get_clone_root(config: Optional[dict] = None) -> Path:
    """Return the root directory that holds all self-patching clones.

    Clones are stored under ``<vault>/backups/clones`` so they are covered by
    the backups rotation policy while remaining outside of Git control. The
    helper also migrates any legacy ``<vault>/clones`` directories that still
    exist from earlier builds.
    """

    backups_root = get_backups_root(config)
    clones_root = (backups_root / CLONES_SUBDIR).resolve()
    try:
        clones_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    _migrate_legacy_clones(clones_root)
    return clones_root


def get_uploads_root(room_id: Optional[str] = None, config: Optional[dict] = None) -> Path:
    """Return the canonical directory for uploaded files, optionally scoped to a room."""

    vault_root = get_vault_root(config)
    vault_root = vault_root if vault_root.is_absolute() else (Path.cwd() / vault_root)

    uploads_root = (vault_root / UPLOADS_SUBDIR).resolve()
    try:
        uploads_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if room_id is None:
        return uploads_root

    room_dir = (uploads_root / str(room_id)).resolve()
    try:
        room_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return room_dir


def rebase_legacy_vault_path(path: PathInput, create_parents: bool = True) -> Path:
    """Map an absolute legacy vault path into the canonical vault tree.

    Args:
        path: Path (string or ``Path``) that might live under
            ``layer3_longterm/vault``.
        create_parents: When ``True`` (default), ensure the rebased path's
            parent directories exist.

    Returns:
        ``Path`` pointing to the analogous location under ``./vault``. If the
        provided path is not within the legacy tree, it is returned unchanged.
    """
    candidate = Path(path)
    try:
        resolved_candidate = candidate.resolve(strict=False)
    except Exception:
        resolved_candidate = (Path.cwd() / candidate).resolve(strict=False)

    try:
        legacy_root = (Path.cwd() / LEGACY_VAULT_PATH).resolve(strict=False)
    except Exception:
        return candidate

    try:
        relative = resolved_candidate.relative_to(legacy_root)
    except Exception:
        return candidate

    canonical_root = get_vault_root()
    canonical_root = canonical_root if canonical_root.is_absolute() else (Path.cwd() / canonical_root)
    rebased = (canonical_root / relative).resolve(strict=False)

    if create_parents:
        try:
            rebased.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    _emit_legacy_warning(resolved_candidate, rebased)
    return rebased


def _resolve_env_override() -> Optional[Path]:
    try:
        override = os.environ.get("THEO_VAULT_ROOT")
        if isinstance(override, str) and override.strip():
            root = Path(override.strip())
            root.mkdir(parents=True, exist_ok=True)
            return root
    except Exception:
        pass
    return None


def _load_config_dict(config: Optional[dict]) -> Optional[dict]:
    if config is not None:
        return config
    try:
        from utils.config_loader import load_config as _load  # type: ignore
    except Exception:
        return None
    try:
        cfg = _load()
    except Exception:
        cfg = None
    return cfg if isinstance(cfg, dict) else None


def _resolve_config_override(config: Optional[dict]) -> Optional[Path]:
    if not isinstance(config, dict):
        return None
    try:
        from utils.config_loader import get_config_value as _get  # type: ignore
    except Exception:
        _get = None
    value = None
    if _get is not None:
        try:
            value = _get(config, "vault_root")
        except Exception:
            value = None
    if isinstance(value, str) and value.strip():
        try:
            root = Path(value.strip())
            root.mkdir(parents=True, exist_ok=True)
            return root
        except Exception:
            return None
    return None


@lru_cache(maxsize=8)
def _warn_if_legacy_vault_present(preferred: Path) -> None:
    try:
        legacy = (Path.cwd() / LEGACY_VAULT_PATH).resolve(strict=False)
    except Exception:
        return

    if not legacy.exists():
        return

    try:
        canonical = preferred
        if not canonical.is_absolute():
            canonical = (Path.cwd() / canonical).resolve(strict=False)
    except Exception:
        canonical = preferred

    _emit_legacy_warning(legacy, canonical)


@lru_cache(maxsize=32)
def _emit_legacy_warning(src: Path, dst: Path) -> None:
    try:
        logger.warning(
            "Detected legacy vault usage at '%s'; rebasing to '%s'. Remove the legacy directory once data is migrated.",
            str(src),
            str(dst),
        )
    except Exception:
        pass


def _ensure_backups_gitignore(backups_root: Path) -> None:
    """Write a minimal .gitignore so rotating artifacts stay out of Git."""

    gitignore_path = backups_root / ".gitignore"
    try:
        if gitignore_path.exists():
            return
        gitignore_path.write_text("*\n!.gitignore\n", encoding="utf-8")
    except Exception:
        pass


def _migrate_legacy_backups(backups_root: Path) -> None:
    """Move any legacy top-level backups into the managed vault directory."""

    legacy_root = Path.cwd() / BACKUPS_SUBDIR
    if legacy_root.resolve() == backups_root.resolve():
        return
    if not legacy_root.exists() or not legacy_root.is_dir():
        return

    try:
        for entry in legacy_root.iterdir():
            target = backups_root / entry.name
            if target.exists():
                continue
            shutil.move(str(entry), str(target))
    except Exception:
        # Best effort; failures are logged for operators via debug logs.
        logger.debug("Unable to fully migrate legacy backups directory", exc_info=True)

    try:
        if not any(legacy_root.iterdir()):
            legacy_root.rmdir()
    except Exception:
        pass


def _migrate_legacy_clones(clones_root: Path) -> None:
    """Relocate legacy ``vault/clones`` directories into ``vault/backups``."""

    try:
        vault_root = clones_root.parent
        while vault_root.name != BACKUPS_SUBDIR and vault_root != vault_root.parent:
            vault_root = vault_root.parent
        if vault_root.name != BACKUPS_SUBDIR:
            return
        # ``vault`` directory sits one level above ``backups``
        canonical_vault = vault_root.parent
    except Exception:
        return

    legacy_clones = canonical_vault / CLONES_SUBDIR
    if not legacy_clones.exists() or not legacy_clones.is_dir():
        return

    try:
        for entry in legacy_clones.iterdir():
            if not entry.is_dir() or not entry.name.startswith("clone_"):
                continue
            target = clones_root / entry.name
            if target.exists():
                continue
            try:
                shutil.move(str(entry), str(target))
            except Exception:
                logger.debug("Failed to migrate legacy clone '%s'", entry, exc_info=True)
    finally:
        try:
            if not any(legacy_clones.iterdir()):
                legacy_clones.rmdir()
        except Exception:
            pass


__all__ = [
    "get_vault_root",
    "get_backups_root",
    "get_clone_root",
    "rebase_legacy_vault_path",
]
