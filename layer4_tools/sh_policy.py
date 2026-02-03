"""
sh_policy

Lightweight blacklist policy loader and enforcer for the bash tool.

Responsibilities:
- Load a JSON policy file from the vault (vault/policy.json)
- Provide helpers to check commands against:
  - blocked binaries (exact match on the first program token)
  - blocked regex patterns (applied to the raw command string)
  - protected read/write path globs (simple substring prefix tripwire)
- Support a single maintenance override file that temporarily disables
  protected path checks (but not dangerous patterns/binaries).

Notes:
- This is not a security sandbox. It is a practical policy nudge and
  tripwire against catastrophic or self‑tampering commands. Primary
  protection remains: non‑root execution in the isolated vault CWD.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Policy:
    kind: str
    blocked_binaries: List[str]
    blocked_command_patterns: List[str]
    blocked_read_paths_glob: List[str]
    blocked_write_paths_glob: List[str]
    maintenance_override_file: Optional[str]


def _default_policy() -> Policy:
    """Return a conservative default policy if no policy.json exists.

    Mirrors the design in the user prompt while remaining generic and
    environment‑agnostic. The blocked paths lists are intentionally minimal
    here; the user should tailor policy.json as needed.
    """
    return Policy(
        kind="blacklist",
        blocked_binaries=[
            "sudo",
            "su",
            "pkexec",
            "doas",
            "shutdown",
            "reboot",
            "halt",
            "poweroff",
        ],
        blocked_command_patterns=[
            r"^\s*rm\s+-rf\s+/(\s|$)",
            r"^\s*rm\s+--no-preserve-root\b",
            r"^\s*dd\b.*\bof=/dev/(sd|nvme|mmcblk)\w+",
            r"^\s*shred\b.*\s/dev/",
            r"^\s*wipefs\b",
            r"^\s*(mkfs\.|mkswap)\b",
            r"^\s*(parted|fdisk|sfdisk|sgdisk|lsblk\s+--wipe)\b",
            r"^\s*(losetup|cryptsetup|kpartx)\b",
            r"^\s*(modprobe|insmod|rmmod|kmod)\b",
            r"^\s*systemctl\s+(poweroff|reboot|isolate|rescue|emergency)\b",
            r"^\s*systemctl\s+(stop|disable)\s+ssh(d)?\b",
            r"^\s*(service)\s+ssh(d)?\s+(stop|disable)\b",
            r"^\s*kill(all)?\s+-9\s+1(\s|$)",
            r"^\s*ip\s+link\s+set\s+\S+\s+down\b",
            r"^\s*nmcli\s+networking\s+off\b",
            r"^\s*rfkill\s+block\b",
            r"^\s*nft\s+(add|insert|delete|flush|reset)\b",
            r"^\s*iptables(?!.*\s-?L\b).*\b(-A|-I|-D|--append|--insert|--delete|--flush|--policy|--zero)\b",
            r"^\s*chroot\b",
            r"^\s*(pivot_root|unshare|nsenter)\b",
        ],
        blocked_read_paths_glob=[],
        blocked_write_paths_glob=[
            "/boot/**",
            "/dev/**",
            "/proc/**",
            "/sys/**",
            "/run/**",
            "/etc/sudoers",
            "/etc/sudoers.d/**",
            "/etc/shadow",
            "/etc/gshadow",
        ],
        maintenance_override_file=".unlock_core",
    )


def _safe_json_load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_policy(vault_root: Path) -> Policy:
    """Load policy.json from the vault, or return defaults.

    The maintenance override file may be absolute or relative; if relative,
    it is treated as relative to the vault root.
    """
    raw = _safe_json_load(vault_root / "policy.json")
    if not isinstance(raw, dict):
        return _default_policy()

    def _list(key: str) -> List[str]:
        v = raw.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    pol = Policy(
        kind=str(raw.get("kind", "blacklist")),
        blocked_binaries=_list("blocked_binaries") or _default_policy().blocked_binaries,
        blocked_command_patterns=_list("blocked_command_patterns") or _default_policy().blocked_command_patterns,
        blocked_read_paths_glob=_list("blocked_read_paths_glob"),
        blocked_write_paths_glob=_list("blocked_write_paths_glob") or _default_policy().blocked_write_paths_glob,
        maintenance_override_file=str(raw.get("maintenance_override_file") or _default_policy().maintenance_override_file),
    )
    return pol


def _blocked_by_patterns(cmd: str, patterns: List[str]) -> Optional[str]:
    """Return the matching pattern if cmd matches any blocked regex, else None."""
    try:
        for pat in patterns:
            if re.search(pat, cmd):
                return pat
    except re.error:
        # On invalid regex, ignore that entry
        return None
    return None


def _blocked_by_binaries(cmd: str, blocked_bins: List[str]) -> Optional[str]:
    """Return the binary name if the first token matches a blocked binary."""
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return None
    first = tokens[0]
    base = os.path.basename(first)
    for b in blocked_bins:
        if base == b or first in (f"/usr/bin/{b}", f"/bin/{b}", f"/sbin/{b}", f"/usr/sbin/{b}"):
            return b
    return None


def _normalize_glob_prefix(glob_pattern: str, vault_root: Path) -> str:
    """Return a simple prefix used for substring matching.

    We take the portion before the first '**' and strip trailing '*' chars.
    If the prefix starts with '/vault', transparently map it to the real
    absolute vault path so commands referencing absolute paths still trip the
    guard.
    """
    prefix = glob_pattern.split("**")[0].rstrip("*")
    if not prefix:
        return prefix
    # Map '/vault' prefix to the actual vault root for this runtime
    try:
        v_abs = str(vault_root.resolve())
        if prefix.startswith("/vault"):
            return prefix.replace("/vault", v_abs, 1)
    except Exception:
        pass
    return prefix


def _mentions_blocked_paths(cmd: str, key_globs: List[str], vault_root: Path) -> Optional[str]:
    """Return the offending glob if the command references a protected path.

    This is a fast, conservative tripwire: we search for the normalized glob
    prefix as a substring in the expanded command string.
    """
    try:
        expanded = os.path.expanduser(os.path.expandvars(cmd))
    except Exception:
        expanded = cmd
    for g in key_globs:
        pref = _normalize_glob_prefix(str(g), vault_root)
        if pref and pref in expanded:
            return str(g)
    return None


def maintenance_mode_enabled(policy: Policy, vault_root: Path) -> bool:
    """Return True if the maintenance override file exists."""
    path = policy.maintenance_override_file or ""
    if not path:
        return False
    p = Path(path)
    if not p.is_absolute():
        p = vault_root / p
    try:
        return p.exists()
    except Exception:
        return False


def enforce_policy_precheck(command: str, policy: Policy, vault_root: Path) -> Tuple[bool, Dict[str, Any]]:
    """Apply policy checks before executing a command.

    Returns (blocked, info_dict). When blocked is True, info_dict contains
    the reason and contextual fields to return from the sh tool.
    """
    # 1) Blocked binaries
    b = _blocked_by_binaries(command, policy.blocked_binaries)
    if b:
        return True, {
            "action": "bash",
            "success": False,
            "blocked": True,
            "reason": "binary",
            "binary": b,
            "chain": command,
        }

    # 2) Blocked regex patterns
    pat = _blocked_by_patterns(command, policy.blocked_command_patterns)
    if pat:
        return True, {
            "action": "bash",
            "success": False,
            "blocked": True,
            "reason": "pattern",
            "pattern": pat,
            "chain": command,
        }

    # 3) Protected paths (only when not in maintenance mode)
    if not maintenance_mode_enabled(policy, vault_root):
        rp = _mentions_blocked_paths(command, policy.blocked_read_paths_glob, vault_root)
        if rp:
            return True, {
                "action": "bash",
                "success": False,
                "blocked": True,
                "reason": "read_path",
                "blocked_path": rp,
                "chain": command,
            }
        wp = _mentions_blocked_paths(command, policy.blocked_write_paths_glob, vault_root)
        if wp:
            return True, {
                "action": "bash",
                "success": False,
                "blocked": True,
                "reason": "write_path",
                "blocked_path": wp,
                "chain": command,
            }

    # All clear
    return False, {}


__all__ = [
    "Policy",
    "load_policy",
    "enforce_policy_precheck",
    "maintenance_mode_enabled",
]
