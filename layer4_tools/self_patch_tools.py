"""
Layer 4: Self-Patching Tools - Backup & Cloning Mechanism

Implements backup and cloning functionality for Theo's self-improvement system:
- create_backup: Git push for code + separate vault zip backup
- create_clone: Copy entire source code (exclude vault/backups) to subfolder
- Ensures vault never pulled from Git (code/vault separation)

Dependencies: gitpython, zipfile, shutil, time
Exports: create_backup, create_clone
"""

import errno
import fnmatch
import os
import py_compile
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Callable, List, Tuple, Set, Iterable

from git import GitCommandError, InvalidGitRepositoryError, Repo

# Resilient logger import: if utils.logger is broken due to a bad patch,
# install a minimal fallback logger module so this file can still import
# and execute rollback logic.
try:  # pragma: no cover - behavior verified via dedicated test
    from utils.logger import get_logger  # type: ignore
except Exception:  # pragma: no cover
    import logging
    import types as _types
    # Minimal, safe logger factory
    def get_logger(name: str) -> logging.Logger:  # type: ignore
        logger = logging.getLogger(name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            handler.setFormatter(fmt)
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return logger

    # Register a stub utils.logger so downstream imports succeed
    try:
        import sys as _sys
        _stub = _types.ModuleType('utils.logger')
        setattr(_stub, 'get_logger', get_logger)
        _sys.modules['utils.logger'] = _stub
    except Exception:
        pass

# Import after logger fallback so diff_utils can safely import its logger
logger = get_logger(__name__)


_VAULT_ZIP_IGNORE_GLOBS: Tuple[str, ...] = (
    ".chats_meta_tmp_*.json",
    "*.swp",
    "*.tmp",
    ".DS_Store",
    "._*",
)

_VAULT_EXCLUDED_DIRS = {"clones", "backups"}


def create_clone() -> Dict[str, Any]:
    """Create a workspace clone under the managed ``clones/`` workspace."""

    def _safe_relative(path: Path, base: Path) -> Path:
        """Return ``path`` relative to ``base`` if possible, ``Path('.')`` otherwise."""

        try:
            return path.relative_to(base)
        except Exception:
            try:
                return path.resolve().relative_to(base.resolve())
            except Exception:
                return Path(".")

    start_ts = time.perf_counter()

    try:
        logger.info("L4.tools [tool:create_clone] - Starting source code cloning process")

        project_root = Path(__file__).parent.parent
        try:
            repo = Repo(project_root)
        except Exception:
            repo = None

        def _is_git_ignored(abs_path: Path) -> bool:
            """Return True when Git metadata marks ``abs_path`` as ignored."""

            if repo is None:
                return False
            try:
                return bool(repo.ignored([str(abs_path)]))
            except Exception:
                return False
        try:
            from utils.vault_paths import get_clone_root, get_vault_root

            vault_root = Path(get_vault_root())
            clones_dir = Path(get_clone_root())
        except Exception:
            vault_root = project_root / "vault"
            clones_dir = vault_root / "backups" / "clones"

        try:
            clones_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            abs_project = project_root.resolve()
        except Exception:
            abs_project = project_root

        try:
            abs_vault = (
                vault_root if vault_root.is_absolute() else (project_root / vault_root)
            ).resolve()
        except Exception:
            abs_vault = project_root / "vault"

        vault_root = abs_vault

        try:
            vault_rel_prefix = (
                str(abs_vault.relative_to(abs_project)) if hasattr(abs_vault, "relative_to") else None
            )
        except Exception:
            vault_rel_prefix = None

        vault_prefix_parts: Tuple[str, ...] = (
            tuple(Path(vault_rel_prefix).parts) if vault_rel_prefix else tuple()
        )

        if clones_dir.exists():
            for old_clone in [
                d for d in clones_dir.iterdir() if d.is_dir() and d.name.startswith("clone_")
            ]:
                logger.info(
                    "L4.tools [tool:create_clone] - Removing existing clone: %s",
                    old_clone.name,
                )
                shutil.rmtree(old_clone, ignore_errors=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clone_dir_name = f"clone_{timestamp}"
        clone_path = clones_dir / clone_dir_name

        if clone_path.exists():
            shutil.rmtree(clone_path, ignore_errors=True)

        clone_results = {
            "success": False,
            "clone_path": f"clones/{clone_dir_name}",
            "files_copied": 0,
            "error": None,
            "timestamp": datetime.now().isoformat(),
        }

        exclude_patterns = {
            "backups",
            "__pycache__",
            ".git",
            ".pytest_cache",
            "logs",
            ".cursor",
            "venv",
            ".venv",
            ".env",
            "*.pyc",
            "*.pyo",
            "*.log",
            "clone_*",
        }

        if vault_rel_prefix:
            try:
                exclude_patterns.add(str(Path(vault_rel_prefix) / "clones"))
                exclude_patterns.add(str(Path(vault_rel_prefix) / "backups"))
            except Exception:
                pass

        vault_exclude_patterns = {
            "chat_history.json",
            "human_memories.json",
            "theo_memories.json",
            "verbatim_memories.json",
            "embeddings_index.json",
            "faiss_index.bin",
            "texts.json",
            "metadata.json",
            "clones",
            "backups",
        }

        files_copied = 0
        templates_to_create: Dict[Path, str] = {}

        def needs_vault_template(rel_path: Path) -> bool:
            """Return True if ``rel_path`` should be replaced with a template file."""

            if not vault_prefix_parts:
                return False
            if rel_path.parts[: len(vault_prefix_parts)] != vault_prefix_parts:
                return False
            if rel_path.name in vault_exclude_patterns:
                templates_to_create[clone_path / rel_path] = rel_path.name
                return True
            return False

        def copy_rel_path(rel_path: Path) -> None:
            """Copy a single relative path into the clone, applying filters."""

            nonlocal files_copied

            if _should_exclude_path(rel_path, exclude_patterns):
                return
            if needs_vault_template(rel_path):
                return

            source = project_root / rel_path
            if not source.exists() or not source.is_file():
                return

            destination = clone_path / rel_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
                files_copied += 1
            except Exception as copy_err:
                logger.warning(
                    "L4.tools [tool:create_clone] - Failed to copy %%s → %%s (%s)",
                    str(rel_path),
                    destination,
                    copy_err,
                )

        use_git_listing = False
        git_paths: List[Path] = []
        try:
            repo = Repo(project_root)
            if not repo.bare:
                git = repo.git
                tracked_raw = git.ls_files(z=True)
                other_raw = git.ls_files("--others", "--exclude-standard", z=True)
                entries: List[str] = []
                if tracked_raw:
                    entries.extend([p for p in tracked_raw.split("\0") if p])
                if other_raw:
                    entries.extend([p for p in other_raw.split("\0") if p])
                seen: Set[str] = set()
                git_paths = [
                    Path(p)
                    for p in entries
                    if p and not (p in seen or seen.add(p))  # deduplicate while preserving order
                ]
                use_git_listing = True
        except (InvalidGitRepositoryError, GitCommandError):
            use_git_listing = False
        except Exception:
            use_git_listing = False

        if use_git_listing and git_paths:
            for rel_path in git_paths:
                copy_rel_path(rel_path)
        else:
            for root, dirs, files in os.walk(project_root):
                current_root = Path(root)
                rel_dir = _safe_relative(current_root, project_root)

                if rel_dir != Path(".") and _should_exclude_path(rel_dir, exclude_patterns):
                    dirs[:] = []
                    continue

                dirs[:] = [
                    d
                    for d in dirs
                    if not _should_exclude_path(rel_dir / d, exclude_patterns)
                ]

                for file_name in files:
                    rel_path = rel_dir / file_name if rel_dir != Path(".") else Path(file_name)
                    copy_rel_path(rel_path)

        for template_path, template_name in templates_to_create.items():
            _create_vault_template_file(template_path, template_name)
            files_copied += 1

        elapsed = time.perf_counter() - start_ts
        clone_results["files_copied"] = files_copied
        clone_results["success"] = True

        logger.info(
            "L4.tools [tool:create_clone] - Cloned to %s (%d files copied in %.2fs)",
            clone_dir_name,
            files_copied,
            elapsed,
        )
        logger.debug(
            "L4.tools [tool:create_clone] - Clone path: %s",
            clone_path,
        )

        return clone_results

    except Exception as e:
        error_msg = str(e)
        logger.error(
            "L4.tools [tool:create_clone] - Clone creation failed: %s",
            e,
            exc_info=True,
        )
        return {
            "success": False,
            "clone_path": None,
            "files_copied": 0,
            "error": error_msg,
            "timestamp": datetime.now().isoformat(),
        }


def _create_vault_template_file(file_path: Path, filename: str) -> None:
    """
    Create template/empty versions of vault files for testing.

    Args:
        file_path: Path where template file should be created
        filename: Name of the file to determine template content
    """
    try:
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Create appropriate template content based on file type
        if filename.endswith(".json"):
            if filename in ["chat_history.json"]:
                # Empty chat history structure
                template_content = "{}"
            elif filename in ["human_memories.json", "verbatim_memories.json"]:
                # Empty array for sensitive memory files
                template_content = "[]"
            elif filename == "theo_memories.json":
                # Include sample Theo memories for testing functionality
                template_content = """[
  {
    "content": "I am Theo, an AI assistant designed to help with various tasks and provide intelligent responses.",
    "timestamp": 1640995200.0,
    "importance": 100,
    "memory_id": "theo_sample_001",
    "embedding_id": null,
    "last_modified": 1640995200.0
  },
  {
    "content": "My primary functions include chatting, managing projects, analyzing data, and helping with code development.",
    "timestamp": 1640995260.0,
    "importance": 90,
    "memory_id": "theo_sample_002",
    "embedding_id": null,
    "last_modified": 1640995260.0
  },
  {
    "content": "I can manage working memory notes, plan tasks, and help organize work efficiently using my Layer 4 tools.",
    "timestamp": 1640995320.0,
    "importance": 85,
    "memory_id": "theo_sample_003",
    "embedding_id": null,
    "last_modified": 1640995320.0
  }
]"""
            elif filename in [
                "projects.json",
                "tasks.json",
                "notifications.json",
            ]:
                # These should not be in vault_exclude_patterns, but just in
                # case
                return  # Don't create template, should be copied normally
            else:
                # Generic empty JSON for other files
                template_content = "{}"
        else:
            # For non-JSON files, create empty file
            template_content = ""

        # Write template content
        file_path.write_text(template_content)
        logger.debug(
            f"L4.tools [tool:create_clone] [template] - Created {filename} template"
        )

    except Exception as e:
        logger.warning(
            f"L4.tools [tool:create_clone] [template] - Failed to create template {filename}: {e}"
        )


def _should_exclude_path(path: Path, exclude_patterns: set) -> bool:
    """
    Check if a path should be excluded based on exclusion patterns.

    Args:
        path: Path to check (relative to project root)
        exclude_patterns: Set of exclusion patterns

    Returns:
        True if path should be excluded, False otherwise
    """
    path_str = str(path)
    path_parts = path.parts

    for pattern in exclude_patterns:
        # Direct string match
        if path_str == pattern:
            return True

        # Check if any part of the path matches the pattern
        if pattern in path_parts:
            return True

        # Check wildcard patterns
        if "*" in pattern:

            if fnmatch.fnmatch(path_str, pattern):
                return True
            # Also check individual filename
            if fnmatch.fnmatch(path.name, pattern):
                return True

        # Check if path starts with pattern (for directory exclusions)
        if path_str.startswith(pattern + "/") or path_str.startswith(
            pattern + os.sep
        ):
            return True

    return False


def _should_ignore_vault_entry(rel_path: Path, ignore_globs: Tuple[str, ...]) -> bool:
    """Return True when the archive entry matches any ignore glob."""

    if not ignore_globs:
        return False

    rel_posix = rel_path.as_posix()
    filename = rel_path.name
    for pattern in ignore_globs:
        if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    return False


def _iter_vault_files(
    vault_root: Path,
    ignore_globs: Optional[Iterable[str]] = None,
) -> Iterable[Tuple[Path, Path]]:
    """Yield (absolute_path, relative_arcname) pairs for vault files."""

    ignore_tuple: Tuple[str, ...] = tuple(ignore_globs or ())
    for root, dirs, files in os.walk(vault_root):
        removed_dirs: List[str] = []
        for name in list(dirs):
            if name in _VAULT_EXCLUDED_DIRS:
                dirs.remove(name)
                removed_dirs.append(name)
        for name in removed_dirs:
            logger.debug(
                "L4.tools [tool:create_backup] [vault] - Excluded %s directory from backup",
                name,
            )

        base = Path(root)
        for filename in files:
            file_path = base / filename
            arcname = file_path.relative_to(vault_root)
            if _should_ignore_vault_entry(arcname, ignore_tuple):
                logger.debug(
                    "L4.tools [tool:create_backup] [vault] - Ignored ephemeral file: %s",
                    arcname.as_posix(),
                )
                continue
            yield file_path, arcname


def _log_vault_skip(arcname: Path, exc: BaseException) -> None:
    logger.warning(
        "L4.tools [tool:create_backup] [vault] - Skipping file during zip: %s (%s)",
        arcname.as_posix(),
        exc,
    )


def _safe_zip_add(zipf: zipfile.ZipFile, file_path: Path, arcname: Path) -> bool:
    """Attempt to add ``file_path`` to the archive, tolerating TOCTOU races."""

    arcname_str = arcname.as_posix()
    try:
        file_path.stat()
    except (FileNotFoundError, PermissionError) as exc:
        _log_vault_skip(arcname, exc)
        return False
    except OSError as exc:  # pragma: no cover - exercised via subclasses
        if exc.errno in (errno.ENOENT, errno.EACCES, errno.EPERM):
            _log_vault_skip(arcname, exc)
            return False
        raise

    try:
        zipf.write(file_path, arcname_str)
        return True
    except (FileNotFoundError, PermissionError) as exc:
        _log_vault_skip(arcname, exc)
        return False
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.EACCES, errno.EPERM):
            _log_vault_skip(arcname, exc)
            return False
        raise


def _get_index_lock_path(repo_root: Path) -> Path:
    """Return the path to the Git index.lock file for a repository root."""
    return repo_root / ".git" / "index.lock"


def is_git_index_lock_present(repo_root: Path) -> bool:
    """Check if the Git index.lock file exists for the given repository root."""
    try:
        return _get_index_lock_path(repo_root).exists()
    except Exception:
        return False


def ensure_git_index_lock_cleared(
    repo_root: Path,
    stale_seconds: int = 120,
    max_wait_seconds: int = 5,
    sleep_interval: float = 0.5,
) -> bool:
    """
    Ensure that the Git index.lock file is not blocking operations.

    Strategy:
    1) If no lock: return True
    2) Wait briefly for an active Git process to finish (up to max_wait_seconds)
    3) If still locked and lock file is older than stale_seconds, remove it as stale

    Returns:
        True if it's safe to proceed (no lock present after handling), else False.
    """
    try:
        lock_path = _get_index_lock_path(repo_root)
        if not lock_path.exists():
            return True

        # Short wait loop in case a legitimate concurrent git just finishes
        deadline = time.time() + max_wait_seconds
        while lock_path.exists() and time.time() < deadline:
            time.sleep(sleep_interval)

        if not lock_path.exists():
            return True

        # Consider the lock stale if older than threshold
        try:
            lock_age_seconds = time.time() - lock_path.stat().st_mtime
        except Exception:
            lock_age_seconds = stale_seconds + 1  # Treat as stale if we cannot stat

        if lock_age_seconds >= stale_seconds:
            try:
                lock_path.unlink(missing_ok=True)
                logger.warning(
                    "L4.tools [git] - Removed stale Git index.lock (age=%.1fs)",
                    lock_age_seconds,
                )
                return True
            except Exception as e:
                logger.error(
                    f"L4.tools [git] - Failed to remove stale index.lock: {e}",
                    exc_info=True,
                )
                return False

        # Lock is still present and considered fresh
        return False

    except Exception as e:
        logger.error(f"L4.tools [git] - Error ensuring index.lock cleared: {e}", exc_info=True)
        return False


def retry_with_git_lock_clear(
    operation: Callable[[], Any],
    repo_root: Path,
    max_attempts: int = 4,
    base_sleep_seconds: float = 0.5,
) -> Any:
    """
    Execute a Git operation with retries, clearing a stale index.lock if encountered.

    Retries on exceptions whose message suggests an index.lock issue. Uses
    exponential backoff between attempts. Re-raises the last exception if all
    attempts fail.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except (GitCommandError, Exception) as e:  # GitPython raises GitCommandError
            message = str(e).lower()
            last_error = e
            if "index.lock" in message or "unable to create '" in message and "index.lock" in message:
                # Try to clear lock aggressively on retry
                ensure_git_index_lock_cleared(
                    repo_root, stale_seconds=0, max_wait_seconds=1, sleep_interval=0.2
                )
                sleep_time = base_sleep_seconds * (2 ** (attempt - 1))
                logger.warning(
                    f"L4.tools [git] - index.lock encountered; retrying attempt {attempt}/{max_attempts} after {sleep_time:.1f}s"
                )
                time.sleep(sleep_time)
                continue
            # Not a lock-related error; stop retrying
            raise

    # Exhausted attempts
    if last_error:
        raise last_error
    return None


def _run_git_backup_push(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Run the scheduled backup push with upstream awareness.

    Args:
        repo_root: Optional repo root override. Defaults to project root.

    Returns:
        Dict describing success/failure, stdout/stderr, branch, commit, and
        whether we had to set the upstream.
    """

    root = Path(repo_root) if repo_root is not None else Path(__file__).parent.parent
    result: Dict[str, Any] = {
        "success": False,
        "branch": None,
        "commit": None,
        "upstream_before_push": False,
        "set_upstream": False,
        "stdout": "",
        "stderr": "",
        "returncode": None,
        "command": None,
        "error": None,
    }

    if not root.exists():
        result["error"] = f"Repository root not found: {root}"
        return result

    def _run_git(cmd: List[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
        )

    try:
        branch_proc = _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if branch_proc.returncode != 0:
            result["stderr"] = branch_proc.stderr.strip()
            result["returncode"] = branch_proc.returncode
            result["error"] = "Unable to determine current branch"
            return result

        branch = branch_proc.stdout.strip() or None
        result["branch"] = branch

        if branch in (None, "HEAD"):
            result["error"] = "Detached HEAD; cannot run scheduled backup push"
            return result

        commit_proc = _run_git(["git", "rev-parse", "--short", "HEAD"])
        if commit_proc.returncode == 0:
            result["commit"] = commit_proc.stdout.strip() or None
        else:
            result["stderr"] = commit_proc.stderr.strip()
            result["returncode"] = commit_proc.returncode
            result["error"] = "Unable to determine commit hash"
            return result

        upstream_proc = _run_git(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        )
        upstream_before = upstream_proc.returncode == 0
        result["upstream_before_push"] = upstream_before

        if not upstream_before:
            push_cmd = ["git", "push", "-u", "origin", "HEAD:main"]
            result["set_upstream"] = True
        else:
            push_cmd = ["git", "push", "origin", "HEAD:main"]

        result["command"] = " ".join(push_cmd)

        push_proc = _run_git(push_cmd)
        result["stdout"] = push_proc.stdout.strip()
        result["stderr"] = push_proc.stderr.strip()
        result["returncode"] = push_proc.returncode
        result["success"] = push_proc.returncode == 0

        if not result["success"] and not result.get("error"):
            result["error"] = result["stderr"] or "Git push failed"

    except FileNotFoundError as exc:
        result["error"] = f"Git executable not found: {exc}"
    except Exception as exc:  # pragma: no cover - defensive guardrail
        result["error"] = str(exc)

    return result


def create_backup(
    git_push_runner: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Create comprehensive backup of Theo's system:
    0. Store current commit hash for rollback priority
    1. Git push for source code (excludes vault)
    2. Separate ZIP backup for vault (memories/projects)

    Returns:
        Dict with backup status, paths, and pre-patch commit hash
    """
    try:
        logger.info("L4.tools [tool:create_backup] - Starting backup process")

        # Get project root directory
        project_root = Path(__file__).parent.parent
        try:
            from utils.vault_paths import get_backups_root, get_vault_root

            raw_vault = Path(get_vault_root())
            vault_path = (
                raw_vault
                if raw_vault.is_absolute()
                else (project_root / raw_vault)
            )
            backups_dir = Path(get_backups_root())
        except Exception:
            vault_path = project_root / "vault"
            backups_dir = vault_path / "backups"

        try:
            vault_path = vault_path.resolve()
        except Exception:
            pass

        # Ensure backups directory exists (includes parents for vault/backups)
        backups_dir.mkdir(parents=True, exist_ok=True)

        backup_results = {
            "success": False,
            "git_pushed": False,
            "vault_backed_up": False,
            "git_error": None,
            "vault_error": None,
            "vault_backup_path": None,
            "vault_skipped_missing": 0,
            "timestamp": datetime.now().isoformat(),
        }

        # Step 0: Store pre-patch commit for rollback priority (initial HEAD)
        pre_patch_commit = _store_pre_patch_commit()
        backup_results["pre_patch_commit"] = pre_patch_commit
        
        # Step 1: Git push for code backup
        try:
            logger.debug(
                "L4.tools [tool:create_backup] - Attempting Git push for code backup"
            )

            # Initialize or get existing repository
            try:
                repo = Repo(project_root)
                logger.debug(
                    "L4.tools [tool:create_backup] - Found existing Git repository"
                )
            except InvalidGitRepositoryError:
                logger.warning(
                    "L4.tools [tool:create_backup] - No Git repository found, initializing..."
                )
                repo = Repo.init(project_root)

                # Add remote if it doesn't exist (using config URL if
                # available)
                try:
                    from utils.config_loader import get_config_value, load_config

                    config = load_config()
                    remote_url = get_config_value(
                        config,
                        "git_repo_url",
                        "https://github.com/zekerdoodle/Theo.git",
                    )

                    # Check if remote exists
                    if not repo.remotes:
                        repo.create_remote("origin", remote_url)
                        logger.debug(
                            f"L4.tools [tool:create_backup] - Added remote origin: {remote_url}"
                        )
                except Exception as e:
                    logger.warning(
                        f"L4.tools [tool:create_backup] - Could not set up remote: {e}"
                    )

            # Check if there are any changes to commit
            if repo.is_dirty() or repo.untracked_files:
                logger.debug(
                    "L4.tools [tool:create_backup] - Found changes to commit"
                )

                # Ensure any stale index.lock is cleared before staging
                ensure_git_index_lock_cleared(project_root)

                # Add all changes (excluding vault) with retry-on-lock
                def _git_add_all() -> None:
                    repo.git.add(A=True)

                retry_with_git_lock_clear(_git_add_all, project_root)

                # Remove vault from staging if it was added
                try:
                    # Compute a project-relative vault path if possible, handling
                    # both absolute and relative vault_root values gracefully.
                    try:
                        abs_project = project_root.resolve()
                    except Exception:
                        abs_project = project_root
                    try:
                        abs_vault = (vault_path if vault_path.is_absolute() else (project_root / vault_path)).resolve()
                    except Exception:
                        abs_vault = project_root / "vault"
                    try:
                        if hasattr(abs_vault, "relative_to") and abs_vault.exists():
                            rel_vault = str(abs_vault.relative_to(abs_project))
                        else:
                            rel_vault = None
                    except Exception:
                        rel_vault = None

                    if rel_vault:
                        repo.git.rm(
                            "--cached",
                            "-r",
                            rel_vault,
                            ignore_errors=True,
                        )
                        logger.debug(
                            "L4.tools [tool:create_backup] - Excluded vault from Git staging"
                        )
                except GitCommandError:
                    # Vault might not be tracked, which is fine
                    pass

                # Create commit (retry on lock as well)
                commit_message = f"Automated backup - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

                def _git_commit() -> None:
                    repo.index.commit(commit_message)

                retry_with_git_lock_clear(_git_commit, project_root)
                logger.debug(
                    f"L4.tools [tool:create_backup] - Created commit: {commit_message}"
                )
                # Update the pre-patch commit reference to the new HEAD so rollback
                # returns to the exact backup commit created immediately before
                # patch application.
                try:
                    updated = _store_pre_patch_commit()
                    if updated:
                        backup_results["pre_patch_commit"] = updated
                except Exception:
                    pass
            else:
                logger.debug(
                    "L4.tools [tool:create_backup] - No changes to commit"
                )

            # Push to remote if available
            if repo.remotes:
                try:
                    repo.remote("origin")
                    push_callable = (
                        git_push_runner
                        or (lambda: _run_git_backup_push(project_root))
                    )
                    push_result: Dict[str, Any]
                    try:
                        push_result = push_callable() or {}
                    except Exception as push_exc:
                        push_result = {"success": False, "error": str(push_exc)}

                    backup_results["git_push_result"] = push_result

                    if push_result.get("success"):
                        backup_results["git_pushed"] = True
                        logger.info(
                            "L4.tools [tool:create_backup] - Code pushed to Git successfully"
                        )
                    else:
                        error_detail = (
                            push_result.get("error")
                            or push_result.get("stderr")
                            or "Unknown git push failure"
                        )
                        backup_results["git_error"] = error_detail
                        logger.error(
                            "L4.tools [tool:create_backup] - Git push failed: %s",
                            error_detail,
                        )

                except GitCommandError as e:
                    backup_results["git_error"] = f"Push failed: {str(e)}"
                    logger.error(
                        f"L4.tools [tool:create_backup] - Git push failed: {e}"
                    )
                except Exception as e:
                    backup_results["git_error"] = (
                        backup_results.get("git_error")
                        or f"Git push error: {str(e)}"
                    )
                    logger.error(
                        f"L4.tools [tool:create_backup] - Git push error: {e}",
                        exc_info=True,
                    )
            else:
                backup_results["git_error"] = "No remote repository configured"
                logger.warning(
                    "L4.tools [tool:create_backup] - No remote repository to push to"
                )

        except Exception as e:
            backup_results["git_error"] = str(e)
            logger.error(
                f"L4.tools [tool:create_backup] - Git backup failed: {e}",
                exc_info=True,
            )

        # Step 2: Create vault ZIP backup
        try:
            logger.debug(
                "L4.tools [tool:create_backup] - Starting vault backup"
            )

            if vault_path.exists():
                # Create timestamped backup filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                vault_backup_filename = f"vault_{timestamp}.zip"
                vault_backup_path = backups_dir / vault_backup_filename

                skipped_missing = 0
                with zipfile.ZipFile(
                    vault_backup_path, "w", zipfile.ZIP_DEFLATED
                ) as zipf:
                    for file_path, arcname in _iter_vault_files(
                        vault_path, _VAULT_ZIP_IGNORE_GLOBS
                    ):
                        added = _safe_zip_add(zipf, file_path, arcname)
                        if not added:
                            skipped_missing += 1
                        else:
                            logger.debug(
                                "L4.tools [tool:create_backup] [vault] - Added %s to backup",
                                arcname.as_posix(),
                            )

                backup_results["vault_backed_up"] = True
                backup_results["vault_backup_path"] = str(vault_backup_path)
                backup_results["vault_skipped_missing"] = skipped_missing

                # Get backup size for logging
                backup_size = (
                    vault_backup_path.stat().st_size / 1024 / 1024
                )  # MB
                logger.info(
                    "L4.tools [tool:create_backup] [vault] - Vault backup created: %s (%.1fMB, skipped_missing=%d)",
                    vault_backup_filename,
                    backup_size,
                    skipped_missing,
                )

                # Clean up old backups (keep last 10)
                _cleanup_old_backups(backups_dir, "vault_*.zip", keep_count=10)

            else:
                backup_results["vault_error"] = "Vault directory not found"
                logger.warning(
                    "L4.tools [tool:create_backup] - Vault directory not found, skipping vault backup"
                )

        except Exception as e:
            backup_results["vault_error"] = str(e)
            logger.error(
                f"L4.tools [tool:create_backup] - Vault backup failed: {e}",
                exc_info=True,
            )

        # Determine overall success - REQUIRE BOTH Git push AND vault backup for safety
        # This ensures we can rollback code AND preserve user data if patch fails
        backup_results["success"] = backup_results["git_pushed"] and backup_results["vault_backed_up"]

        if backup_results["git_pushed"] and backup_results["vault_backed_up"]:
            logger.info(
                "L4.tools [tool:create_backup] - Backup completed successfully: Code pushed to Git, vault zipped"
            )
        elif backup_results["git_pushed"] and not backup_results["vault_backed_up"]:
            logger.error(
                f"L4.tools [tool:create_backup] - CRITICAL: Git backup succeeded but vault backup failed: {backup_results.get('vault_error', 'unknown error')} - CANNOT SAFELY PROCEED WITH PATCH"
            )
        elif not backup_results["git_pushed"] and backup_results["vault_backed_up"]:
            logger.error(
                f"L4.tools [tool:create_backup] - CRITICAL: Vault backup succeeded but Git push failed: {backup_results.get('git_error', 'unknown error')} - CANNOT SAFELY PROCEED WITH PATCH"
            )
        else:
            # Both failed
            logger.error(
                f"L4.tools [tool:create_backup] - CRITICAL: Both backups failed - Git push failed ({backup_results.get('git_error', 'unknown error')}), vault backup failed ({backup_results.get('vault_error', 'unknown error')})"
            )

        return backup_results

    except Exception as e:
        logger.error(
            f"L4.tools [tool:create_backup] - Critical backup failure: {e}",
            exc_info=True,
        )
        return {
            "success": False,
            "git_pushed": False,
            "vault_backed_up": False,
            "git_error": str(e),
            "vault_error": str(e),
            "vault_backup_path": None,
            "timestamp": datetime.now().isoformat(),
        }


def _cleanup_old_backups(
    backups_dir: Path, pattern: str, keep_count: int = 10
) -> None:
    """
    Clean up old backup files, keeping only the most recent ones.

    Args:
        backups_dir: Directory containing backups
        pattern: Glob pattern for backup files
        keep_count: Number of recent backups to keep
    """
    try:
        backup_files = list(backups_dir.glob(pattern))
        if len(backup_files) <= keep_count:
            return

        # Sort by modification time (newest first)
        backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Remove oldest files
        files_to_remove = backup_files[keep_count:]
        for file_path in files_to_remove:
            file_path.unlink()
            logger.debug(
                f"L4.tools [tool:create_backup] [cleanup] - Removed old backup: {file_path.name}"
            )

        logger.info(
            f"L4.tools [tool:create_backup] [cleanup] - Cleaned up {len(files_to_remove)} old backups"
        )

    except Exception as e:
        logger.warning(
            f"L4.tools [tool:create_backup] [cleanup] - Backup cleanup failed: {e}"
        )


# Global convenience functions for tool integration
def backup_now() -> Dict[str, Any]:
    """
    Convenience function for immediate backup execution.
    Alias for create_backup() for better tool naming.
    """
    return create_backup()


def clone_self() -> Dict[str, Any]:
    """
    Convenience function for immediate clone creation.
    Alias for create_clone() for better tool naming.
    """
    return create_clone()


# Since clones are now in vault, we no longer need complex patching state management
# Theo can access clones naturally through normal file tools


# Patching state functions removed - no longer needed with vault-based clones


def _resolve_clone_path(clone_path: str) -> Path:
    """Resolve a clone path to an absolute Path inside the managed clones root.

    Supports relative inputs (``clones/<id>`` or ``<id>``) and absolute paths.
    Legacy ``layer3_longterm/vault`` locations are automatically rebased onto the
    canonical vault with a migration warning.
    """
    project_root = Path(__file__).parent.parent

    rebase_fn: Optional[Callable[[Path, bool], Path]] = None
    try:
        from utils.vault_paths import (
            get_clone_root,
            get_vault_root,
            rebase_legacy_vault_path,
        )  # type: ignore

        vr = Path(get_vault_root())
        vault_root = (vr if vr.is_absolute() else (project_root / vr)).resolve()
        clones_root = Path(get_clone_root()).resolve()
        rebase_fn = rebase_legacy_vault_path  # type: ignore[assignment]
    except Exception:
        vault_root = (project_root / "vault").resolve()
        clones_root = (vault_root / "backups" / "clones").resolve()
        rebase_fn = None

    def _apply_rebase(candidate: Path) -> Path:
        if rebase_fn is None:
            return candidate
        try:
            rebased = rebase_fn(candidate, create_parents=False)  # type: ignore[misc]
        except Exception:
            return candidate
        try:
            return Path(rebased)
        except Exception:
            return candidate

    cp = Path(clone_path)

    if cp.is_absolute():
        candidate = _apply_rebase(cp).resolve(strict=False)
    else:
        sanitized = clone_path.replace("\\", "/").strip()
        sanitized = sanitized.lstrip("/")
        while sanitized.startswith("./"):
            sanitized = sanitized[2:]

        if not sanitized:
            return clones_root / "__invalid_outside_clones__"

        legacy_prefix = "layer3_longterm/vault/clones/"
        legacy_backups_prefix = "layer3_longterm/vault/backups/clones/"
        new_prefix = "vault/backups/clones/"
        direct_prefix = "vault/clones/"

        if sanitized.startswith(legacy_backups_prefix):
            tail = sanitized[len(legacy_backups_prefix) :]
            candidate = (clones_root / Path(tail)).resolve(strict=False)
        elif sanitized.startswith(legacy_prefix):
            tail = sanitized[len(legacy_prefix) :]
            # Legacy paths map to current clones_root (vault/backups/clones)
            candidate = (clones_root / Path(tail)).resolve(strict=False)
        elif sanitized.startswith(new_prefix):
            tail = sanitized[len(new_prefix) :]
            candidate = (clones_root / Path(tail)).resolve(strict=False)
        elif sanitized.startswith(direct_prefix):
            tail = sanitized[len(direct_prefix) :]
            # Direct vault/clones also maps to current clones_root
            candidate = (clones_root / Path(tail)).resolve(strict=False)
        elif sanitized.startswith("clones/"):
            tail = sanitized[len("clones/") :]
            candidate = (clones_root / Path(tail)).resolve(strict=False)
        else:
            candidate = (clones_root / Path(sanitized)).resolve(strict=False)

    try:
        candidate.relative_to(clones_root)
        return candidate
    except Exception:
        return clones_root / "__invalid_outside_clones__"


SUPPORTED_PATCH_EXTENSIONS = {
    # Code
    ".py", ".pyi", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".kt", ".swift", ".rb", ".php", ".go",
    ".rs", ".c", ".h", ".cpp", ".hpp",
    # Docs
    ".md", ".rst",
    # Config
    ".json", ".yaml", ".yml", ".ini", ".toml", ".cfg", ".conf", ".lock",
    # Shell / scripts
    ".sh", ".bash",
    # Data-like formats that are commonly code artifacts
    ".sql",
    # VCS/meta
    ".gitignore",
}


def _is_supported_text_file(path: Path, allowed_exts: Optional[set] = None) -> bool:
    # Treat files with supported extensions as text we can diff/patch
    try:
        ext_set = allowed_exts if allowed_exts is not None else SUPPORTED_PATCH_EXTENSIONS
        return path.suffix in ext_set
    except Exception:
        return False


def preview_patch(
    clone_path: str,
    max_diffs: int = 200,
    include_deletions: bool = True,
    extensions: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Preview diffs between a clone and the live codebase without applying changes.

    Args:
        clone_path: Path to the clone directory (e.g. 'clones/clone_YYYYMMDD_HHMMSS')
        max_diffs: Maximum number of file diffs to include
        include_deletions: Include files that would be deleted (exist in live but not in clone)
        extensions: Optional list of file extensions to consider; defaults to SUPPORTED_PATCH_EXTENSIONS

    Returns:
        Dict with summary and list of file-level diffs
    """
    start_ts = time.perf_counter()

    def _stat_signature(stat_result: os.stat_result) -> Tuple[int, int, int]:
        """Return a quick comparison signature for a Stat result."""

        try:
            mtime_ns = stat_result.st_mtime_ns  # type: ignore[attr-defined]
        except AttributeError:
            mtime_ns = int(stat_result.st_mtime * 1_000_000_000)
        return stat_result.st_size, mtime_ns, stat_result.st_mode

    try:
        from utils.diff_utils import create_diff  # type: ignore

        clone_full_path = _resolve_clone_path(clone_path)
        if not clone_full_path.exists() or not clone_full_path.is_dir():
            return {
                "success": False,
                "error": f"Invalid clone path (must point to an existing clones/ workspace): {clone_path}",
            }

        project_root = Path(__file__).parent.parent
        try:
            repo = Repo(project_root)
        except Exception:
            repo = None

        def _is_git_ignored(abs_path: Path) -> bool:
            """Return True when Git metadata marks ``abs_path`` as ignored."""

            if repo is None:
                return False
            try:
                return bool(repo.ignored([str(abs_path)]))
            except Exception:
                return False
        if extensions:
            try:
                normalized = {
                    (e.lower() if isinstance(e, str) else str(e).lower()) for e in extensions
                }
                allowed_exts = {e if e.startswith(".") else f".{e}" for e in normalized}
            except Exception:
                allowed_exts = SUPPORTED_PATCH_EXTENSIONS
        else:
            allowed_exts = SUPPORTED_PATCH_EXTENSIONS

        changes: List[Dict[str, Any]] = []
        pending_dirs: List[Path] = [Path(".")]

        while pending_dirs and len(changes) < max_diffs:
            rel_dir = pending_dirs.pop()
            clone_dir = clone_full_path / rel_dir
            live_dir = project_root / rel_dir

            try:
                with os.scandir(clone_dir) as clone_iter:
                    clone_entries = list(clone_iter)
            except FileNotFoundError:
                clone_entries = []

            live_entries: Dict[str, os.DirEntry] = {}
            if live_dir.exists():
                try:
                    with os.scandir(live_dir) as live_iter:
                        live_entries = {entry.name: entry for entry in live_iter}
                except FileNotFoundError:
                    live_entries = {}

            for entry in clone_entries:
                rel_path = rel_dir / entry.name
                if _should_exclude_from_patch(rel_path):
                    live_entries.pop(entry.name, None)
                    continue

                if entry.is_dir(follow_symlinks=False):
                    pending_dirs.append(rel_path)
                    live_entries.pop(entry.name, None)
                    continue

                if not _is_supported_text_file(rel_path, allowed_exts):
                    live_entries.pop(entry.name, None)
                    continue

                live_entries.pop(entry.name, None)
                live_file = project_root / rel_path
                is_live_file = live_file.exists() and live_file.is_file()

                try:
                    clone_sig = _stat_signature(entry.stat(follow_symlinks=False))
                except FileNotFoundError:
                    clone_sig = None

                live_sig = None
                if is_live_file:
                    try:
                        live_sig = _stat_signature(live_file.stat())
                    except FileNotFoundError:
                        live_sig = None

                if clone_sig is not None and live_sig is not None and clone_sig == live_sig:
                    # Identical metadata → treat as unchanged without reading file contents
                    continue

                try:
                    clone_content = Path(entry.path).read_text(encoding="utf-8")
                except Exception:
                    continue

                diff_text, _ = create_diff(str(live_file), clone_content)
                changes.append(
                    {
                        "path": str(rel_path),
                        "status": "modified" if is_live_file else "added",
                        "diff": diff_text[:10000],
                    }
                )
                if len(changes) >= max_diffs:
                    break

            if include_deletions and len(changes) < max_diffs and live_entries:
                for remaining in list(live_entries.values()):
                    rel_path = rel_dir / remaining.name
                    if _should_exclude_from_patch(rel_path):
                        continue

                    live_file_path = project_root / rel_path
                    if _is_git_ignored(live_file_path):
                        continue
                    if remaining.is_dir(follow_symlinks=False):
                        stack: List[Path] = [Path(remaining.path)]
                        while stack and len(changes) < max_diffs:
                            current = stack.pop()
                            rel_current = current.relative_to(project_root)
                            if _is_git_ignored(current):
                                continue
                            if _should_exclude_from_patch(rel_current):
                                continue
                            try:
                                with os.scandir(current) as current_iter:
                                    current_entries = list(current_iter)
                            except FileNotFoundError:
                                continue

                                for child in current_entries:
                                    child_rel = rel_current / child.name
                                    if _should_exclude_from_patch(child_rel):
                                        continue
                                    child_abs = project_root / child_rel
                                    if _is_git_ignored(child_abs):
                                        continue
                                    if child.is_dir(follow_symlinks=False):
                                        stack.append(Path(child.path))
                                        continue
                                    if not _is_supported_text_file(child_rel, allowed_exts):
                                        continue
                                diff_text, _ = create_diff(str(project_root / child_rel), "")
                                changes.append(
                                    {
                                        "path": str(child_rel),
                                        "status": "deleted",
                                        "diff": diff_text[:10000],
                                    }
                                )
                                if len(changes) >= max_diffs:
                                    break
                        if len(changes) >= max_diffs:
                            break
                    else:
                        if not _is_supported_text_file(rel_path, allowed_exts):
                            continue
                        if _is_git_ignored(live_file_path):
                            continue
                        diff_text, _ = create_diff(str(live_file_path), "")
                        changes.append(
                            {
                                "path": str(rel_path),
                                "status": "deleted",
                                "diff": diff_text[:10000],
                            }
                        )
                        if len(changes) >= max_diffs:
                            break

        elapsed = time.perf_counter() - start_ts
        summary = {
            "success": True,
            "changes_count": len(changes),
            "clone_path": str(clone_full_path),
            "max_diffs": max_diffs,
            "include_deletions": include_deletions,
            "elapsed_seconds": elapsed,
        }
        logger.info(
            "L4.tools [tool:preview_patch] - Generated %d diff(s) in %.2fs",
            len(changes),
            elapsed,
        )
        return {**summary, "changes": changes}

    except Exception as e:
        logger.error("L4.tools [preview_patch] - Error generating diffs: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


def apply_patch(clone_path: str, silent_continuation: Optional[bool] = None, target_room: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply patch from clone to live code and reboot system.

    Args:
        clone_path: Path to the clone directory (e.g., ``clones/clone_YYYYMMDD_HHMMSS``)

    This function:
    1. Creates backup first (git push + vault zip)
    2. Generates and applies diff from clone to live code
    3. Reboots the system (os._exit or subprocess restart)

    Post-reboot logic in main.py checks for patch flag and sends status message.

    Returns:
        Dict with patch application status (only if backup fails)
    """
    try:
        logger.info(
            "L4.tools [tool:apply_patch] - Starting patch application process"
        )

        # Validate clone path
        if not clone_path:
            error_msg = "Clone path is required"
            logger.error(f"L4.tools [tool:apply_patch] - {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "backup_created": False,
                "patch_applied": False,
            }

        clone_full_path = _resolve_clone_path(clone_path)

        # Validate clone exists and is a directory
        if not clone_full_path.exists():
            error_msg = f"Clone path not found or outside managed clones/: {clone_path}"
            logger.error(f"L4.tools [tool:apply_patch] - {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "backup_created": False,
                "patch_applied": False,
            }
        
        if not clone_full_path.is_dir():
            error_msg = f"Clone path is not a directory: {clone_full_path}"
            logger.error(f"L4.tools [tool:apply_patch] - {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "backup_created": False,
                "patch_applied": False,
            }
        
        # Validate clone has essential files (basic sanity check)
        # Use actual entrypoints present in this repo
        essential_files = ["theo.py", "server/app.py", "config.yaml"]
        missing_files = []
        for file in essential_files:
            if not (clone_full_path / file).exists():
                missing_files.append(file)
        
        if missing_files:
            error_msg = f"Clone appears incomplete - missing essential files: {missing_files}"
            logger.error(f"L4.tools [tool:apply_patch] - {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "backup_created": False,
                "patch_applied": False,
            }

        logger.debug(
            f"L4.tools [tool:apply_patch] - Applying patch from clone: {clone_full_path.name}"
        )

        # Step 1: Create backup first (critical safety measure)
        logger.info(
            "L4.tools [tool:apply_patch] - Creating backup before patch application"
        )
        backup_result = create_backup()

        if not backup_result.get("success"):
            error_msg = f"Backup failed: {backup_result.get('git_error', 'Unknown')} / {backup_result.get('vault_error', 'Unknown')}"
            logger.critical(
                f"L4.tools [tool:apply_patch] - CRITICAL: {error_msg}"
            )
            return {
                "success": False,
                "error": error_msg,
                "backup_created": False,
                "patch_applied": False,
                "backup_details": backup_result,
            }

        logger.info(
            "L4.tools [tool:apply_patch] - Backup completed successfully"
        )

        # Step 2: Generate and apply diff from clone to live code
        project_root = Path(__file__).parent.parent
        clone_root = clone_full_path

        logger.info(
            "L4.tools [tool:apply_patch] - Generating and applying diffs from clone"
        )

        # Set patch flags before copying files
        _set_patch_flags(success=None)  # Indicates patch in progress

        # Find all supported text files that differ between clone and live
        files_modified = 0
        diff_results = []

        # Lazy import to avoid module import-time dependency on utils.logger
        from utils.diff_utils import create_diff  # type: ignore

        # Allow patching ANY file per updated policy. Safety is ensured via
        # robust rollback/restore mechanisms. No protected path skips.
        protected_paths = set()
        force_include_protected = True

        # Walk through clone directory and compare files
        for clone_file in clone_root.rglob("*"):
            # Skip excluded directories and files
            relative_path = clone_file.relative_to(clone_root)
            if not clone_file.is_file():
                continue
            live_file = project_root / relative_path

            # Skip if this is a clone directory or excluded pattern
            if _should_exclude_from_patch(relative_path):
                continue
            # No protected file skip (policy change)
            if not _is_supported_text_file(relative_path):
                continue

            try:
                # Read clone file content
                clone_content = clone_file.read_text(encoding="utf-8")

                # Read live file content (or empty if doesn't exist)
                if live_file.exists():
                    live_content = live_file.read_text(encoding="utf-8")
                else:
                    live_content = ""
                    # Ensure parent directory exists
                    live_file.parent.mkdir(parents=True, exist_ok=True)

                # Check if files differ
                if clone_content != live_content:
                    # Generate diff for logging
                    diff_text, diff_output = create_diff(
                        str(live_file), clone_content
                    )
                    diff_results.append(
                        {
                            "file": str(relative_path),
                            "diff": diff_text,
                            "live_exists": live_file.exists(),
                        }
                    )

                    # Copy file from clone to live
                    try:
                        # Ensure target directory exists
                        live_file.parent.mkdir(parents=True, exist_ok=True)
                        
                        # Verify clone file is readable
                        if not clone_file.is_file():
                            logger.warning(f"L4.tools [tool:apply_patch] - Skipping non-file: {relative_path}")
                            continue
                            
                        # Copy with error handling
                        shutil.copy2(clone_file, live_file)
                        files_modified += 1

                        logger.debug(
                            f"L4.tools [tool:apply_patch] - Applied changes to: {relative_path}"
                        )
                    except PermissionError as e:
                        logger.error(f"L4.tools [tool:apply_patch] - Permission denied copying {relative_path}: {e}")
                        continue
                    except shutil.SameFileError:
                        logger.debug(f"L4.tools [tool:apply_patch] - Skipping identical file: {relative_path}")
                        continue
                    except Exception as e:
                        logger.error(f"L4.tools [tool:apply_patch] - Failed to copy {relative_path}: {e}")
                        continue

            except Exception as e:
                logger.warning(
                    f"L4.tools [tool:apply_patch] - Failed to process {relative_path}: {e}"
                )

        logger.info(
            f"L4.tools [tool:apply_patch] - Applied changes to {files_modified} files"
        )

        # Step 3: Validate that changes were actually applied
        if files_modified == 0:
            _set_patch_flags(success=False)
            logger.error(
                "L4.tools [tool:apply_patch] - PATCH FAILED: No files were modified (empty diff)"
            )
            
            # Enhanced error message with troubleshooting tips
            error_msg = (
                "No files were modified - patch contained no differences between clone and live code.\n\n"
                "This usually means:\n"
                "  1. The code agent didn't make any changes (check code_agent output)\n"
                "  2. Changes were already present in live code\n"
                "  3. The wrong clone was specified\n\n"
                "Tip: Use preview_patch to verify clone changes before applying."
            )
            
            return {
                "success": False,
                "error": error_msg,
                "files_modified": files_modified,
                "diff_results": diff_results,
            }

        # Step 4: Record wake-up continuation preferences and set in-progress flag
        try:
            # Default: NOT silent per product spec (visible assistant continuation)
            silent = False if silent_continuation is None else bool(silent_continuation)
            # Capture current room from log context if available
            try:
                from utils.logger import get_current_room_id as _get_room
                inferred_room = _get_room()
            except Exception:
                inferred_room = None
            room_hint = target_room or inferred_room
            try:
                from utils.patch_state import set_patch_options as _set_opts
                # Persist wake-up routing hints for post-reboot continuation
                _set_opts(silent=silent, room_id=str(room_hint) if room_hint else None)
                logger.debug(
                    f"L4.tools [tool:apply_patch] - Stored continuation options (silent={silent}, room={room_hint or '-'})"
                )
            except Exception as _poe:
                logger.debug(f"L4.tools [tool:apply_patch] - Could not persist patch options: {_poe}")
        except Exception:
            pass

        # NEW: Before reboot, append a hidden progress marker into chat history so
        # the continuation run understands that the patch succeeded and a reboot
        # is imminent. This prevents the agent from re-planning the same steps.
        try:
            progress_room = str(room_hint or "web")
            progress_text = (
                f"PATCH PROGRESS — backup OK; {files_modified} file(s) updated from {clone_root.name}. "
                f"Restarting now to load new code…"
            )
            # Prefer lightweight direct write via ChatHistory to ensure flush before exit
            try:
                from layer2_shortterm.chat_history import ChatHistory as _CH
                ch = _CH()  # defaults resolve vault path automatically
                ch.add_message("system", progress_text, progress_room, hidden=True)
                # Force a synchronous save for this room to avoid losing the marker on exit
                try:
                    ch._save_history(overwrite_rooms={progress_room})  # type: ignore[attr-defined]
                except Exception:
                    pass
                logger.info(
                    f"L4.patch [pre-restart] - Wrote hidden progress marker to room '{progress_room}'"
                )
            except Exception as _che:
                # Fallback: write directly to per-room JSON (same format as web tools)
                try:
                    import json, time as _t
                    from utils.vault_paths import get_vault_root as _gvr
                    vr = _gvr()
                except Exception:
                    from pathlib import Path as _P
                    vr = _P("vault")
                try:
                    import os as _os, uuid as _uuid
                    rooms_dir = (vr / "chats") if hasattr(vr, 'joinpath') else os.path.join(str(vr), "chats")
                    if hasattr(rooms_dir, 'mkdir'):
                        rooms_dir.mkdir(parents=True, exist_ok=True)
                        path = rooms_dir / f"{progress_room}.json"
                        msgs = []
                        if path.exists():
                            try:
                                msgs = json.loads(path.read_text(encoding="utf-8")) or []
                            except Exception:
                                msgs = []
                        msgs.append({
                            "role": "system",
                            "content": progress_text,
                            "message_id": str(_uuid.uuid4()),
                            "timestamp": _t.time(),
                            "reactions": {},
                            "hidden": True,
                        })
                        tmp = path.with_suffix('.json.tmp')
                        tmp.write_text(json.dumps(msgs, indent=2, ensure_ascii=False), encoding="utf-8")
                        try:
                            import os as _os2
                            _os2.replace(str(tmp), str(path))
                        except Exception:
                            path.write_text(json.dumps(msgs, indent=2, ensure_ascii=False), encoding="utf-8")
                    else:
                        # string path fallback
                        os.makedirs(rooms_dir, exist_ok=True)
                        path = os.path.join(str(rooms_dir), f"{progress_room}.json")
                        try:
                            with open(path, 'r', encoding='utf-8') as rf:
                                msgs = json.load(rf) or []
                        except Exception:
                            msgs = []
                        msgs.append({
                            "role": "system",
                            "content": progress_text,
                            "message_id": __import__('uuid').uuid4().hex,
                            "timestamp": __import__('time').time(),
                            "reactions": {},
                            "hidden": True,
                        })
                        with open(path, 'w', encoding='utf-8') as wf:
                            json.dump(msgs, wf, indent=2, ensure_ascii=False)
                    logger.info(
                        f"L4.patch [pre-restart] - (fallback) wrote hidden progress marker to room '{progress_room}'"
                    )
                except Exception as _raw:
                    logger.debug(f"L4.patch [pre-restart] - Failed to persist progress marker: {_raw} (original: {_che})")
        except Exception:
            # Never let progress logging block patch/restart
            pass

        # Set in-progress flag and prepare for reboot
        _set_patch_flags(success=None)

        logger.critical(
            "L4.tools [tool:apply_patch] - PATCH APPLIED SUCCESSFULLY - SYSTEM REBOOTING"
        )

        # Step 4: Reboot the system
        # Use theo.py restart for proper patch monitoring and rollback
        try:
            logger.info(
                "L4.tools [tool:apply_patch] - Attempting restart via theo.py"
            )

            # Resolve Python interpreter with strong preference for project venv
            venv_python = _detect_python_interpreter(project_root)
            theo_script = str(project_root / "theo.py")

            if venv_python == "python3":
                logger.warning(
                    "L4.tools [tool:apply_patch] - No project venv detected; using system python3"
                )
            else:
                logger.debug(
                    f"L4.tools [tool:apply_patch] - Using interpreter: {venv_python}"
                )

            # Log the restart command for debugging
            logger.info(
                f"L4.tools [tool:apply_patch] - Executing restart: {venv_python} {theo_script} restart"
            )

            # Try creating a restart script and executing it (template avoids Python f-string capture)
            restart_script_path = project_root / "restart_temp.sh"
            restart_script_template = """#!/bin/bash
set -e
cd "__ROOT__"
# Clean up this script after execution
trap 'rm -f "__SCRIPT__"' EXIT

# 1) Request restart (stop + start in background)
"__VPY__" "__THEO__" restart || true

# 2) Probe the running instance for quick viability (8-12s)
"__VPY__" - << 'PY'
import time
from urllib.request import urlopen
from urllib.error import URLError

def probe(url: str, timeout_s: int = 120) -> bool:
    deadline = time.time() + max(2, timeout_s)
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as resp:
                code = getattr(resp, 'status', 200)
                if 200 <= code < 500:
                    print('OK')
                    raise SystemExit(0)
        except URLError:
            pass
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit(2)

probe('http://127.0.0.1:8000/')
PY
probe_rc=$?

if [ $probe_rc -ne 0 ]; then
# 3) If probe failed, attempt automated rollback (import-safe), else fall back to minimal restorer
  echo "Probe failed, attempting rollback..." >&2
  "__VPY__" - << 'PY'
import sys
try:
    from layer4_tools.self_patch_tools import rollback_on_failure
    res = rollback_on_failure()
    print(res)
    ok = bool(res.get('success'))
except Exception as e:
    print('rollback_on_failure import failed:', e)
    ok = False
sys.exit(0 if ok else 2)
PY
  rb_rc=$?
  if [ $rb_rc -ne 0 ]; then
    echo "Falling back to scripts/restore_min.py" >&2
    "__VPY__" "scripts/restore_min.py" || true
    # 3b) Last-resort inline minimal restore if restore_min.py is unavailable
    echo "Attempting inline minimal restore" >&2
    "__VPY__" - << 'PY'
import os, sys, subprocess, time
from pathlib import Path

def run(cmd, cwd, timeout=20):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return p.returncode
    except Exception:
        return 1

def write_flag(root: Path, status: str):
    try:
        (root / ".patch_status").write_text(f"{status}|{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", encoding='utf-8')
    except Exception:
        pass

root = Path(__file__).resolve().parent
# Prefer .pre_patch_commit
pre = (root / ".pre_patch_commit").read_text(encoding='utf-8').strip() if (root/".pre_patch_commit").exists() else ''
if pre and run(["git", "reset", "--hard", pre], root) == 0:
    write_flag(root, "ROLLBACK_COMPLETE")
else:
    # Try HEAD~1..3 then remote
    ok = False
    for i in range(1,4):
        if run(["git", "reset", "--hard", f"HEAD~{i}"], root) == 0:
            write_flag(root, "ROLLBACK_COMPLETE")
            ok = True
            break
    if not ok:
        if run(["git", "fetch", "origin"], root, timeout=30) == 0:
            for br in ("origin/main", "origin/master"):
                if run(["git", "reset", "--hard", br], root) == 0:
                    write_flag(root, "ROLLBACK_COMPLETE")
                    ok = True
                    break
        if not ok:
            write_flag(root, "FAILURE")
            sys.exit(2)

# Start Theo after inline restore
py = sys.executable or "python3"
subprocess.Popen([py, "theo.py", "start"], cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy())
PY
  fi
  # 4) Start again on rolled back or restored code
  "__VPY__" "__THEO__" start || true
fi
"""

            restart_script_content = (
                restart_script_template
                .replace("__ROOT__", str(project_root))
                .replace("__SCRIPT__", str(restart_script_path))
                .replace("__VPY__", venv_python)
                .replace("__THEO__", theo_script)
            )
            
            # Write the restart script
            with open(restart_script_path, 'w') as f:
                f.write(restart_script_content)
            
            # Make it executable
            os.chmod(restart_script_path, 0o755)
            
            logger.info(
                f"L4.tools [tool:apply_patch] - Created restart script: {restart_script_path}"
            )

            # Execute the restart script
            restart_process = subprocess.Popen(
                ["/bin/bash", str(restart_script_path)],
                cwd=str(project_root),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
            
            # Log the process ID for debugging
            logger.info(
                f"L4.tools [tool:apply_patch] - Started restart process via script (PID: {restart_process.pid})"
            )

            # In tests, avoid hard exit to let assertions run
            try:
                if os.environ.get("THEO_TEST_MODE") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
                    return {"success": True, "patch_applied": True, "files_modified": files_modified, "test_mode": True}
            except Exception:
                pass

            # Give restart script time to initiate
            time.sleep(1)

            # Exit current process
            os._exit(0)

        except Exception as restart_error:
            logger.error(
                f"L4.tools [tool:apply_patch] - Script restart failed: {restart_error}"
            )
            logger.critical(
                "L4.tools [tool:apply_patch] - Falling back to direct start"
            )

            # Fallback to theo.py start (not main.py)
            try:
                venv_python = _detect_python_interpreter(project_root)
                theo_script = str(project_root / "theo.py")
                
                if venv_python == "python3":
                    logger.warning(
                        "L4.tools [tool:apply_patch] - Fallback: No project venv; using system python3"
                    )
                
                logger.info(
                    f"L4.tools [tool:apply_patch] - Fallback executing: {venv_python} {theo_script} start"
                )
                
                # Create fallback script
                fallback_script_path = project_root / "start_temp.sh"
                fallback_script_content = f"""#!/bin/bash
cd "{project_root}"
# Clean up this script after execution
trap 'rm -f "{fallback_script_path}"' EXIT
exec "{venv_python}" "{theo_script}" start
"""
                
                with open(fallback_script_path, 'w') as f:
                    f.write(fallback_script_content)
                
                os.chmod(fallback_script_path, 0o755)
                
                subprocess.Popen(
                    ["/bin/bash", str(fallback_script_path)],
                    cwd=str(project_root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=os.environ.copy(),
                )
                time.sleep(2)
                os._exit(0)
            except Exception as fallback_error:
                logger.critical(
                    f"L4.tools [tool:apply_patch] - Fallback start failed: {fallback_error}"
                )
                # Hard exit - system will need external restart
                os._exit(1)

    except Exception as e:
        logger.critical(
            f"L4.tools [tool:apply_patch] - CRITICAL patch application error: {e}",
            exc_info=True,
        )

        # Try to set failure flag if possible
        try:
            _set_patch_flags(success=False)
        except BaseException:
            pass

        return {
            "success": False,
            "error": str(e),
            "backup_created": False,
            "patch_applied": False,
        }


def _detect_python_interpreter(project_root: Path) -> str:
    """
    Detect the best Python interpreter to use for restarts.

    Preference order:
    1) project `.venv/bin/python`
    2) project `venv/bin/python`
    3) interpreter from $VIRTUAL_ENV
    4) current `sys.executable`
    5) fallback to `python3`
    """
    candidates = []

    # Project-local virtualenvs
    for name in (".venv", "venv"):
        venv_path = project_root / name
        unix_py = venv_path / "bin" / "python"
        win_py = venv_path / "Scripts" / "python.exe"
        if unix_py.exists():
            candidates.append(str(unix_py))
        elif win_py.exists():
            candidates.append(str(win_py))

    # Environment virtualenv
    venv_env = os.environ.get("VIRTUAL_ENV")
    if venv_env:
        venv_env_path = Path(venv_env)
        unix_py = venv_env_path / "bin" / "python"
        win_py = venv_env_path / "Scripts" / "python.exe"
        if unix_py.exists():
            candidates.append(str(unix_py))
        elif win_py.exists():
            candidates.append(str(win_py))

    # Current interpreter
    if sys.executable:
        try:
            if Path(sys.executable).exists():
                candidates.append(sys.executable)
        except Exception:
            pass

    # Pick the first existing candidate
    for cand in candidates:
        try:
            if cand and Path(cand).exists():
                return cand
        except Exception:
            continue

    return "python3"

def _should_exclude_from_patch(relative_path: Path) -> bool:
    """
    Check if a file should be excluded from patch application.

    Args:
        relative_path: Path relative to project root

    Returns:
        True if file should be excluded from patching
    """
    path_str = str(relative_path)
    path_parts = relative_path.parts

    # Exclude directories and files that are cache, build artifacts, or user data
    excluded_dir_names = {
        # VCS and metadata
        ".git", ".svn", ".hg",
        # Python caches and envs
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".tox",
        ".venv", "venv", "env",
        # JS/TS and web build outputs
        "node_modules", ".next", ".nuxt", "dist", "build", "out", ".parcel-cache", ".turbo",
        # Other language/tool outputs
        "target", ".gradle",
        # IDE/editor folders
        ".idea", ".vscode",
        # Project-specific non-code data
        "backups", "logs",
        # Theo vault (user data, embeddings, clones, etc.)
        "vault",
    }

    # Exclude known junk or OS files
    excluded_file_names = {
        ".DS_Store", "Thumbs.db", ".coverage", "coverage.xml",
    }

    # Exclude hidden dotfiles by default except for a small whitelist of code configs
    allowed_dotfiles = {
        ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".flake8",
        ".pylintrc", ".prettierignore", ".eslintignore", ".eslintrc", ".eslintrc.json",
        ".prettierrc", ".prettierrc.json", ".prettierrc.yaml", ".prettierrc.yml", ".ruff.toml",
        ".coveragerc",
    }

    # 1) Exclude by directory names anywhere in the path
    if any(part in excluded_dir_names for part in path_parts):
        # Allow .github (CI) despite being a dot dir
        if ".github" in path_parts:
            pass
        else:
            return True

    # 2) Exclude Theo vault regardless of legacy aliasing
    rebase_fn: Optional[Callable[[Path, bool], Path]] = None
    try:
        from utils.vault_paths import get_vault_root, rebase_legacy_vault_path

        rebase_fn = rebase_legacy_vault_path  # type: ignore[assignment]
        vault_root = Path(get_vault_root())
        canonical_prefixes = {
            str(vault_root),
        }
        try:
            canonical_prefixes.add(str(vault_root.resolve(strict=False)))
        except Exception:
            pass
        try:
            rebased_vault = Path(
                rebase_legacy_vault_path(Path("layer3_longterm") / "vault", create_parents=False)
            )
            canonical_prefixes.add(str(rebased_vault))
            try:
                canonical_prefixes.add(str(rebased_vault.resolve(strict=False)))
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        canonical_prefixes = {"vault"}

    if any(path_str.startswith(prefix) for prefix in canonical_prefixes):
        return True

    if rebase_fn is not None:
        try:
            rebased_candidate = str(rebase_fn(Path(path_str), create_parents=False))  # type: ignore[misc]
            if any(rebased_candidate.startswith(prefix) for prefix in canonical_prefixes):
                return True
        except Exception:
            pass

    # 3) Exclude common junk files
    if relative_path.name in excluded_file_names:
        return True

    # 4) Exclude cypress artifacts (but keep cypress tests)
    if "cypress" in path_parts and ("videos" in path_parts or "screenshots" in path_parts):
        return True

    # 5) Exclude other clones if any matched pattern
    if any(part.startswith("clone_") for part in path_parts):
        return True

    # 6) Exclude most hidden dotfiles unless specifically whitelisted
    name = relative_path.name
    if name.startswith('.') and name not in allowed_dotfiles:
        return True

    return False


def _set_patch_flags(success: Optional[bool], rollback_complete: bool = False) -> None:
    """
    Set patch status flags for post-reboot detection.

    Args:
        success: True if patch succeeded, False if failed, None if in progress
        rollback_complete: True if this is setting rollback complete status
    """
    try:
        project_root = Path(__file__).parent.parent
        flag_file = project_root / ".patch_status"

        if rollback_complete:
            # Special flag for rollback completion
            status = "ROLLBACK_COMPLETE"
        elif success is None:
            # Patch in progress
            status = "IN_PROGRESS"
        elif success:
            # Patch succeeded
            status = "SUCCESS"
        else:
            # Patch failed
            status = "FAILURE"

        # Write flag file with timestamp
        flag_content = f"{status}|{datetime.now().isoformat()}"
        flag_file.write_text(flag_content)

        logger.debug(f"L4.tools [tool:apply_patch] - Set patch flag: {status}")

    except Exception as e:
        logger.error(
            f"L4.tools [tool:apply_patch] - Failed to set patch flags: {e}"
        )


def check_patch_status() -> Dict[str, Any]:
    """
    Check if there's a patch status flag from previous reboot.
    Used by main.py on startup to detect post-reboot status.

    Returns:
        Dict with patch status information
    """
    try:
        project_root = Path(__file__).parent.parent
        flag_file = project_root / ".patch_status"

        if not flag_file.exists():
            return {"has_flag": False, "status": None, "timestamp": None}

        # Read flag content
        flag_content = flag_file.read_text().strip()
        parts = flag_content.split("|", 1)

        if len(parts) != 2:
            logger.warning(
                "L4.tools [patch_status] - Invalid patch flag format"
            )
            return {"has_flag": True, "status": "INVALID", "timestamp": None}

        status, timestamp = parts

        logger.info(
            f"L4.tools [patch_status] - Found patch flag: {status} at {timestamp}"
        )

        return {"has_flag": True, "status": status, "timestamp": timestamp}

    except Exception as e:
        logger.error(
            f"L4.tools [patch_status] - Error checking patch status: {e}"
        )
        return {
            "has_flag": False,
            "status": "ERROR",
            "timestamp": None,
            "error": str(e),
        }


def clear_patch_status() -> bool:
    """
    Clear patch status flag after processing.
    Should be called by main.py after handling wake-up message.

    Returns:
        True if flag was cleared successfully
    """
    try:
        project_root = Path(__file__).parent.parent
        flag_file = project_root / ".patch_status"

        if flag_file.exists():
            flag_file.unlink()
            logger.debug("L4.tools [patch_status] - Cleared patch status flag")
            return True

        return True  # No flag to clear is success

    except Exception as e:
        logger.error(
            f"L4.tools [patch_status] - Failed to clear patch flag: {e}"
        )
        return False


def rollback_on_failure(max_retries: int = 5) -> Dict[str, Any]:
    """
    Enhanced rollback system that tries multiple strategies:
    1. First priority: Reset to pre-patch commit (stored during backup)
    2. Fallback: Go back commit by commit until startup works
    3. Last resort: Pull from remote
    Only affects code, never pulls vault (user data separation).

    Args:
        max_retries: Maximum number of commit rollbacks to attempt

    Returns:
        Dict with rollback status
    """
    try:
        logger.critical("L4.tools [rollback] - INITIATING EMERGENCY ROLLBACK")

        project_root = Path(__file__).parent.parent
        
        # Set failure flag for tracking
        _set_patch_flags(success=False)
        
        # Try to get pre-patch commit hash from backup metadata
        pre_patch_commit = _get_pre_patch_commit()
        
        try:
            repo = Repo(project_root)
            # Determine the actual vault root using centralized resolver
            try:
                from utils.vault_paths import get_vault_root as _get_vault_root
                vr = _get_vault_root()
                # Normalize to absolute path anchored to project root when relative
                vault_path = (vr if vr.is_absolute() else (project_root / vr)).resolve()
            except Exception:
                # Fallback to preferred default per specs
                vault_path = (project_root / "vault").resolve()
            
            # Ensure we're on main branch
            if repo.active_branch.name != "main":
                logger.info("L4.tools [rollback] - Switching to main branch")
                repo.git.checkout("main")
            
            # Strategy 1: Reset to pre-patch commit if available
            if pre_patch_commit:
                logger.info(f"L4.tools [rollback] - Attempting reset to pre-patch commit: {pre_patch_commit[:8]}")
                try:
                    repo.git.reset("--hard", pre_patch_commit)
                    logger.critical(
                        f"L4.tools [rollback] - ROLLBACK COMPLETE - RESET TO PRE-PATCH COMMIT {pre_patch_commit[:8]}"
                    )
                    _clear_pre_patch_commit()  # Clear the stored commit
                    _set_patch_flags(success=False, rollback_complete=True)  # Set rollback complete flag
                    _try_start_after_rollback()
                    return {
                        "success": True,
                        "action": "reset_to_pre_patch",
                        "commit": pre_patch_commit,
                        "vault_preserved": vault_path.exists(),
                        "timestamp": datetime.now().isoformat(),
                    }
                except Exception as e:
                    logger.warning(f"L4.tools [rollback] - Pre-patch reset failed: {e}")
            
            # Strategy 2: Iterative rollback through recent commits
            logger.info(f"L4.tools [rollback] - Attempting iterative rollback (max {max_retries} commits)")
            current_commit = repo.head.commit
            
            for i in range(max_retries):
                # Go back one commit
                try:
                    previous_commit = current_commit.parents[0] if current_commit.parents else None
                    if not previous_commit:
                        logger.warning("L4.tools [rollback] - No more parent commits available")
                        break
                    
                    logger.info(f"L4.tools [rollback] - Trying commit {previous_commit.hexsha[:8]} (attempt {i+1}/{max_retries})")
                    repo.git.reset("--hard", previous_commit.hexsha)
                    
                    # Test if this commit allows startup (basic syntax check)
                    if _test_startup_viability():
                        logger.critical(
                            f"L4.tools [rollback] - ROLLBACK COMPLETE - RESET TO WORKING COMMIT {previous_commit.hexsha[:8]}"
                        )
                        _set_patch_flags(success=False, rollback_complete=True)  # Set rollback complete flag
                        _try_start_after_rollback()
                        return {
                            "success": True,
                            "action": "iterative_commit_rollback",
                            "commit": previous_commit.hexsha,
                            "attempts": i + 1,
                            "vault_preserved": vault_path.exists(),
                            "timestamp": datetime.now().isoformat(),
                        }
                    
                    current_commit = previous_commit
                    
                except Exception as e:
                    logger.warning(f"L4.tools [rollback] - Commit rollback attempt {i+1} failed: {e}")
                    break
            
            # Strategy 3: Pull from remote as last resort
            logger.info("L4.tools [rollback] - Attempting remote pull as last resort")
            try:
                origin = repo.remote("origin")
                # Preserve local vault: move it aside before pulling code
                try:
                    from utils.vault_paths import get_vault_root as _get_vault_root
                    vr = _get_vault_root()
                    vault_path = (vr if vr.is_absolute() else (project_root / vr)).resolve()
                except Exception:
                    vault_path = (project_root / "vault").resolve()
                temp_vault = project_root / "_vault_preserve_tmp"
                moved = False
                try:
                    if vault_path.exists():
                        if temp_vault.exists():
                            shutil.rmtree(temp_vault)
                        shutil.move(str(vault_path), str(temp_vault))
                        moved = True
                        logger.info("L4.tools [rollback] - Preserved local vault before git pull")
                except Exception as ve:
                    logger.warning(f"L4.tools [rollback] - Could not move vault for preservation: {ve}")
                    moved = False

                try:
                    origin.pull()
                finally:
                    # Restore preserved vault back into place, removing any pulled vault content
                    if moved:
                        try:
                            if vault_path.exists():
                                shutil.rmtree(vault_path)
                        except Exception:
                            pass
                        try:
                            shutil.move(str(temp_vault), str(vault_path))
                            logger.info("L4.tools [rollback] - Restored local vault after git pull")
                        except Exception as re:
                            logger.warning(f"L4.tools [rollback] - Failed to restore preserved vault: {re}")
                
                logger.critical(
                    "L4.tools [rollback] - ROLLBACK COMPLETE - PULLED FROM REMOTE"
                )
                _set_patch_flags(success=False, rollback_complete=True)  # Set rollback complete flag
                _try_start_after_rollback()
                return {
                    "success": True,
                    "action": "remote_pull_rollback",
                    "vault_preserved": vault_path.exists(),
                    "timestamp": datetime.now().isoformat(),
                }
            except Exception as e:
                logger.critical(f"L4.tools [rollback] - Remote pull failed: {e}")
                
            # All strategies failed
            logger.critical("L4.tools [rollback] - ALL ROLLBACK STRATEGIES FAILED")
            return {
                "success": False,
                "action": "all_strategies_failed",
                "error": "All rollback strategies exhausted",
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as git_error:
            logger.critical(
                f"L4.tools [rollback] - Git rollback failed: {git_error}"
            )
            return {
                "success": False,
                "action": "git_operation_failed",
                "error": str(git_error),
                "timestamp": datetime.now().isoformat(),
            }
            
    except Exception as e:
        logger.critical(
            f"L4.tools [rollback] - CRITICAL rollback failure: {e}",
            exc_info=True,
        )
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


def _try_start_after_rollback() -> None:
    """Best-effort attempt to ensure Theo is running after a successful rollback.

    - Skips in test environments (PYTEST_CURRENT_TEST or THEO_TEST_MODE).
    - If a running PID is detected and alive, does nothing.
    - Otherwise, starts Theo in background using the preferred interpreter.
    """
    try:
        import os as _os
        if _os.environ.get("PYTEST_CURRENT_TEST") or _os.environ.get("THEO_TEST_MODE") == "1":
            return

        project_root = Path(__file__).parent.parent
        pid_path = project_root / "theo.pid"

        # If PID exists and process is alive, do nothing
        if pid_path.exists():
            try:
                pid_val = int(pid_path.read_text().strip())
                try:
                    _os.kill(pid_val, 0)
                    return  # already running
                except Exception:
                    pass
            except Exception:
                pass

        # Start Theo in background
        venv_python = _detect_python_interpreter(project_root)
        theo_script = str(project_root / "theo.py")
        try:
            proc = subprocess.Popen(
                [venv_python, theo_script, "start"],
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=_os.environ.copy(),
            )
            logger.info("L4.tools [rollback] - Auto-started Theo after rollback")

            # Briefly verify viability; if not up shortly, try a direct uvicorn fallback
            try:
                import time as _t
                _t.sleep(1.0)
                if not _test_startup_viability(timeout_seconds=6):
                    logger.warning("L4.tools [rollback] - Start probe failed; attempting direct uvicorn fallback")
                    # Fallback: launch uvicorn directly with project log config, writing to main log file
                    log_dir = project_root / "logs"
                    try:
                        log_dir.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        pass
                    log_path = log_dir / "theo.log"
                    try:
                        log_handle = open(log_path, "a", encoding="utf-8")
                    except Exception:
                        log_handle = subprocess.DEVNULL  # best-effort
                    uvicorn_cmd = [
                        venv_python, "-m", "uvicorn", "server.app:app",
                        "--host", "0.0.0.0", "--port", "8000",
                        "--log-level", "info", "--no-access-log",
                        "--log-config", str(project_root / "config" / "logging.yaml"),
                    ]
                    try:
                        subprocess.Popen(
                            uvicorn_cmd,
                            cwd=str(project_root),
                            stdin=subprocess.DEVNULL,
                            stdout=log_handle,
                            stderr=log_handle,
                            start_new_session=True,
                            env=_os.environ.copy(),
                        )
                        logger.info("L4.tools [rollback] - Launched uvicorn fallback after rollback")
                    except Exception as _ufe:
                        logger.error(f"L4.tools [rollback] - Uvicorn fallback failed: {_ufe}")
            except Exception:
                pass
        except Exception as se:
            logger.warning(f"L4.tools [rollback] - Auto-start after rollback failed: {se}")
    except Exception:
        # Never let auto-start raise
        pass


def _store_pre_patch_commit() -> str:
    """
    Store the current commit hash before applying a patch.
    This allows rollback to the exact pre-patch state as first priority.
    
    Returns:
        The stored commit hash
    """
    try:
        project_root = Path(__file__).parent.parent
        pre_patch_file = project_root / ".pre_patch_commit"
        
        repo = Repo(project_root)
        current_commit = repo.head.commit.hexsha
        
        pre_patch_file.write_text(current_commit)
        logger.info(f"L4.tools [backup] - Stored pre-patch commit: {current_commit[:8]}")
        
        return current_commit
        
    except Exception as e:
        logger.warning(f"L4.tools [backup] - Failed to store pre-patch commit: {e}")
        return ""


def _get_pre_patch_commit() -> str:
    """
    Retrieve the stored pre-patch commit hash.
    
    Returns:
        The pre-patch commit hash, or empty string if not available
    """
    try:
        project_root = Path(__file__).parent.parent
        pre_patch_file = project_root / ".pre_patch_commit"
        
        if pre_patch_file.exists():
            commit_hash = pre_patch_file.read_text().strip()
            logger.info(f"L4.tools [rollback] - Found pre-patch commit: {commit_hash[:8]}")
            return commit_hash
        
        return ""
        
    except Exception as e:
        logger.warning(f"L4.tools [rollback] - Failed to read pre-patch commit: {e}")
        return ""


def _clear_pre_patch_commit() -> None:
    """
    Clear the stored pre-patch commit after successful rollback.
    """
    try:
        project_root = Path(__file__).parent.parent
        pre_patch_file = project_root / ".pre_patch_commit"
        
        if pre_patch_file.exists():
            pre_patch_file.unlink()
            logger.info("L4.tools [rollback] - Cleared pre-patch commit reference")
        
    except Exception as e:
        logger.warning(f"L4.tools [rollback] - Failed to clear pre-patch commit: {e}")


def _test_startup_viability(timeout_seconds: int = 8) -> bool:
    """
    Probe that the web app can boot quickly.

    Strategy:
    1) Try to start uvicorn for server.app:app on a free local port
    2) HTTP GET '/' and expect a 200 within a short timeout
    3) Always tear down the server process

    Falls back to a lightweight import check if uvicorn is unavailable.

    Returns:
        True if startup looks viable, False otherwise
    """
    import socket
    import contextlib
    from urllib.request import urlopen
    from urllib.error import URLError

    project_root = Path(__file__).parent.parent

    # Fallback: ensure server/app.py and app object can be imported
    try:
        import importlib.util
        app_path = project_root / "server" / "app.py"
        if not app_path.exists():
            logger.warning("L4.tools [viability] - server/app.py not found")
            return False
        spec = importlib.util.spec_from_file_location("server.app", str(app_path))
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
            if not hasattr(module, "app"):
                logger.warning("L4.tools [viability] - FastAPI app not found in server/app.py")
                return False
        else:
            logger.warning("L4.tools [viability] - Could not import server/app.py")
            return False
    except Exception as e:
        logger.warning(f"L4.tools [viability] - Import check failed: {e}")
        return False

    # Try to launch a short-lived uvicorn subprocess if available
    try:
        venv_python = _detect_python_interpreter(project_root)

        # Pick a free port
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.bind(("127.0.0.1", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            free_port = s.getsockname()[1]

        cmd = [
            venv_python,
            "-m",
            "uvicorn",
            "server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--log-level",
            "warning",
            "--no-access-log",
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        start = time.time()
        url = f"http://127.0.0.1:{free_port}/"
        ok = False

        # Poll until server responds or timeout elapses
        while time.time() - start < timeout_seconds:
            try:
                with urlopen(url, timeout=1) as resp:
                    code = getattr(resp, "status", 200)
                    if 200 <= code < 500:  # HTML index or API error is ok for viability
                        ok = True
                        break
            except URLError:
                pass
            except Exception:
                pass
            time.sleep(0.2)

        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        except Exception:
            pass

        if ok:
            logger.debug("L4.tools [viability] - Web boot probe passed")
            return True
        else:
            logger.warning("L4.tools [viability] - Web boot probe timed out")
            return False

    except FileNotFoundError:
        # uvicorn not available in environment
        logger.warning("L4.tools [viability] - uvicorn not found; using import check only")
        return True
    except Exception as e:
        logger.warning(f"L4.tools [viability] - Boot probe failed: {e}")
        return False


def scheduled_backup():
    """Scheduled backup function called periodically by the scheduler."""
    push_result_holder: Dict[str, Any] = {}

    def _capture_push_result() -> Dict[str, Any]:
        push_result = _run_git_backup_push()
        push_result_holder["result"] = push_result
        return push_result

    def _log_push_outcome(push_result: Dict[str, Any], fallback_error: Optional[str]) -> None:
        branch = push_result.get("branch") or "unknown"
        commit = push_result.get("commit") or "unknown"
        upstream_before = push_result.get("upstream_before_push")
        set_upstream = push_result.get("set_upstream")
        context = (
            f"branch={branch}, commit={commit}, upstream_before={bool(upstream_before)}, set_upstream={bool(set_upstream)}"
        )

        if push_result.get("success"):
            logger.info(
                "L4.backup [scheduler] - Git push success (%s)",
                context,
            )
        else:
            detail = (
                push_result.get("error")
                or push_result.get("stderr")
                or fallback_error
                or "unknown error"
            )
            logger.error(
                "L4.backup [scheduler] - Git push failed (%s, error=%s)",
                context,
                detail,
            )

    try:
        logger.info("L4.backup [scheduler] - Executing scheduled backup")
        backup_result = create_backup(git_push_runner=_capture_push_result)
        push_result = push_result_holder.get("result") or backup_result.get("git_push_result") or {}
        _log_push_outcome(push_result, backup_result.get("git_error"))

        git_ok = bool(backup_result.get("git_pushed"))
        vault_ok = bool(backup_result.get("vault_backed_up"))
        if git_ok and vault_ok:
            logger.info("L4.backup [scheduler] - Scheduled backup completed successfully: Code pushed to Git, vault zipped")
        elif git_ok and not vault_ok:
            logger.info("L4.backup [scheduler] - Partial scheduled backup: Code pushed to Git (vault backup failed)")
        elif (not git_ok) and vault_ok:
            logger.info("L4.backup [scheduler] - Partial scheduled backup: Vault zipped (Git push failed)")
        else:
            logger.error("L4.backup [scheduler] - Scheduled backup failed: Both Git push and vault backup failed")
    except Exception as e:
        logger.error(f"L4.backup [scheduler] - Scheduled backup encountered error: {e}", exc_info=True)


async def handle_patch_wake_up(bot_instance, chat_history):
    """Handle post-reboot patch status wake-up message (silent per specs).

    Writes a hidden system wake-up to a sensible room so Theo can continue
    without showing anything to the user UI.
    """
    try:
        logger.info("L4.patch [wake_up] - Processing post-reboot patch status")

        # Get patch status (prefer fresh read)
        try:
            refreshed = check_patch_status()
            status_info = refreshed if refreshed and refreshed.get("has_flag", False) else getattr(bot_instance, "patch_status_info", {})
        except Exception:
            status_info = getattr(bot_instance, "patch_status_info", {})
        status = (status_info.get("status") or "UNKNOWN").upper()

        # If we reached on_ready with IN_PROGRESS, that implies success
        if status == "IN_PROGRESS" and getattr(bot_instance, "_is_ready", False):
            status = "SUCCESS"

        # Spec-compliant short messages
        if status == "SUCCESS":
            wake_text = "Patch applied, reboot successful! - SYSTEM"
        elif status in ("ROLLBACK_COMPLETE", "FAILURE"):
            wake_text = "Patch failed, restore and reboot successful! - SYSTEM"
        else:
            wake_text = "Reboot successful! - SYSTEM"

        # Choose the best room: most recent non-default → active → web
        target_room = None
        latest_ts = -1
        try:
            for room_id in chat_history.get_all_channels():
                if room_id == "web":
                    continue
                hist = chat_history.get_history(room_id)
                if hist:
                    ts = hist[-1].get("timestamp", 0)
                    if ts > latest_ts:
                        latest_ts = ts
                        target_room = room_id
        except Exception:
            target_room = None
        if target_room is None:
            try:
                from utils.active_room import get_active_room as _gar
                target_room = _gar() or None
            except Exception:
                target_room = None
        target_room = target_room or "web"

        # Append hidden system message
        appended = False
        try:
            from layer4_tools.web_tools import append_hidden_message
            await append_hidden_message(wake_text, channel_id=str(target_room), role="system")
            appended = True
            logger.info(f"L4.patch [wake_up] - Hidden wake-up appended to '{target_room}'")
        except Exception as ie:
            logger.debug(f"L4.patch [wake_up] - Hidden append failed: {ie}; falling back to visible history")

        if not appended:
            try:
                chat_history.add_message("system", wake_text, str(target_room))
            except Exception:
                pass

        try:
            clear_patch_status()
        except Exception:
            pass
        try:
            bot_instance.patch_wake_up_pending = False
            bot_instance.patch_status_info = None
        except Exception:
            pass
    except Exception as e:
        logger.error(f"L4.patch [wake_up] - Error: {e}", exc_info=True)
        try:
            bot_instance.patch_wake_up_pending = False
        except Exception:
            pass
