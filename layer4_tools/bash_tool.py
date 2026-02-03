"""
bash_tool

Purpose
-------
Provide a non‑interactive Bash tool Theo can use for discovery, file IO, and
automation. It favors a permissive, blacklist‑first posture with practical
tripwires — and one hard rule: never write to the live, running source code.

Update (2025-09-10):
- Add convenience environment variables for path discovery:
  - THEO_VAULT_ROOT → absolute vault path
  - THEO_CLONES_ROOT → absolute clones workspace path (vault/backups/clones)
  - THEO_PROJECT_ROOT → absolute live project code path (writes protected; reads allowed)
- Normalize common mistaken clone paths like "../clones/..." or "/clones/..."
  to the real absolute vault clones path before execution. A short advisory is
  appended in the result when normalization occurs. This addresses confusion
  observed in the latest chat where clones were assumed to be outside vault.

Key behavior
------------
- Default "blacklist" mode: allow most commands unless blocked by policy/patterns.
- Optional "allowlist" mode (strict) via config.
- CWD is the vault; absolute/relative paths outside the vault are allowed to
  support realistic workflows.
- Hard block: any command that would WRITE to the live project source directory
  is rejected. Read‑only access is allowed for quick inspections. Changing
  directory into the live source remains blocked to avoid ambiguous relative
  writes. For any edits, Theo must use the clone workflow (create_clone →
  preview_patch/apply_patch).
- Sudo/escalation entrypoints are blocked by policy.
- Environment variables are filtered by an allowlist; HOME is set to the vault.
- Outputs are token‑aware truncated; full text is optionally logged into
  vault/sh_logs when truncated.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.config_loader import get_config_value, load_config
from utils.logger import get_logger
from utils.token_counter import count_tokens, truncate_text_to_tokens
from utils.vault_paths import get_clone_root, get_vault_root
from layer4_tools.sh_policy import (
    load_policy,
    enforce_policy_precheck,
)

logger = get_logger(__name__)


def _default_allowlist() -> List[str]:
    """Strict allowlist of safe commands for allowlist mode."""
    return [
        # Discovery/diagnostics
        "ls", "stat", "du", "df", "file", "id", "whoami", "uname", "uptime", "date", "env", "pwd", "which", "whereis",
        # Shell builtins/helpers commonly used in pipelines
        "echo", "true", "printf",
        "lscpu", "lsblk", "free", "top", "ps",
        # Text/search
        "cat", "head", "tail", "nl", "wc", "grep", "rg", "awk", "sed", "uniq", "tr",
        # Text processing helpers used in tests/workflows
        "sort", "cut", "xargs",
        # Find/archiving
        "find", "fd", "zip", "unzip", "tar",
        # Networking deliberately excluded in strict mode
        # Dev/test
        "python", "pip", "pytest", "flake8", "ruff", "mypy", "black",
        # Logs/diagnostics
        "journalctl",
        # File ops (allowed; guardrails via blocklist and patterns)
        "mv", "cp", "rm", "mkdir", "touch", "diff",
        # JSON/YAML processors
        "jq", "yq",
    ]


def _default_sudo_allowlist() -> List[str]:
    return [
        # Services (tight subset; specific service names recommended via patterns)
        "systemctl",
        # Docker
        "docker",
        # Package mgmt
        "apt-get", "apt-cache",
        # Surgical file writes (tee)
        "tee",
    ]


def _default_blocklist_patterns() -> List[str]:
    return [
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\bhalt\b",
        r"\binit\s+(0|6)\b",
        r"systemctl\s+(poweroff|reboot)\b",
        r"rm\s+-rf\s+/(\s|$)",
        r"rm\s+-rf\s+\*/?\s*$",
        r"mkfs\w*\b",
        r"dd\s+if=/dev/zero\s+of=/dev/sd\w+",
        r":\(\)\{\s*:\|:&\s*\};:",  # fork bomb
        r"(insmod|rmmod|modprobe)\b",
        r"chown\s+-R\s+/\b",
        r"chmod\s+-R\s+0{3}\b",
    ]


def _sanitize_unmatched_quotes(cmd: str) -> str:
    """Attempt minimal, safe sanitation for unmatched trailing quotes.

    Heuristic: if the command ends with a lone quote and overall quote count is
    odd, drop the last trailing quote. This addresses common cases like:
        sed -n '1,40p' path/to/file'

    We avoid adding quotes or rebalancing internal groups to keep behavior
    conservative.
    """
    try:
        single_count = cmd.count("'")
        double_count = cmd.count('"')
        # Drop a single trailing unmatched quote
        if single_count % 2 == 1 and cmd.endswith("'"):
            return cmd[:-1]
        if double_count % 2 == 1 and cmd.endswith('"'):
            return cmd[:-1]
    except Exception:
        pass
    return cmd


def _split_segments(cmd: str) -> List[str]:
    # Split by ;, &&, || but keep pipelines intact in each segment
    # Use shlex to tokenize; on quoting errors, apply a conservative sanitation
    # and fall back to treating the full command as a single segment.
    parts: List[str] = []
    current: List[str] = []
    try:
        lexer = shlex.shlex(cmd, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError as ve:
        # Try a minimal sanitation and retry once
        safe_cmd = _sanitize_unmatched_quotes(cmd)
        if safe_cmd != cmd:
            try:
                lexer = shlex.shlex(safe_cmd, posix=True)
                lexer.whitespace_split = True
                lexer.commenters = ''
                tokens = list(lexer)
            except Exception:
                # As a last resort, return the whole (sanitized) command as one segment
                return [safe_cmd.strip()] if safe_cmd.strip() else []
        else:
            # Could not sanitize; return the original as a single segment so later
            # validation can still inspect the first program token.
            return [cmd.strip()] if cmd.strip() else []

    # Rebuild segments by scanning tokens
    sep_set = {";", "&&", "||"}
    j = 0
    while j < len(tokens):
        tok = tokens[j]
        if tok in sep_set:
            if current:
                parts.append(" ".join(current))
                current = []
        else:
            current.append(tok)
        j += 1
    if current:
        parts.append(" ".join(current))
    # If no tokens, return empty
    return [p.strip() for p in parts if p.strip()]


def _split_pipeline(segment: str) -> List[str]:
    return [p.strip() for p in segment.split("|")]


def _first_program(cmd_segment: str) -> Tuple[bool, str, List[str]]:
    """Return (uses_sudo, program, argv) for a simple (non-pipeline) segment."""
    # Handle heredoc patterns (cat << EOF, cat <<'EOF', etc.)
    # Extract the command before the heredoc operator
    heredoc_match = re.match(r'^(.+?)\s*<<', cmd_segment)
    if heredoc_match:
        # Parse just the part before the heredoc
        cmd_before_heredoc = heredoc_match.group(1).strip()
        try:
            toks = shlex.split(cmd_before_heredoc, posix=True)
        except Exception:
            toks = cmd_before_heredoc.split()
    else:
        # Normal command parsing
        try:
            toks = shlex.split(cmd_segment, posix=True)
        except Exception:
            toks = cmd_segment.split()
    
    if not toks:
        return False, "", []
    uses_sudo = toks[0] == "sudo"
    if uses_sudo and len(toks) > 1:
        return True, toks[1], toks[2:]
    return False, toks[0], toks[1:]


def _matches_any(name: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if pat.startswith("re:"):
            if re.search(pat[3:], name):
                return True
        elif name == pat:
            return True
    return False


def _is_blocked(raw: str, patterns: List[str]) -> Optional[str]:
    """Check if a command string matches any blocklist pattern."""
    for pat in patterns:
        pattern = pat[3:] if pat.startswith("re:") else pat
        if re.search(pattern, raw):
            return pat
    return None


def _is_command_blocked(command_name: str, patterns: List[str]) -> bool:
    """Check if a specific command name matches any blocklist pattern."""
    for pat in patterns:
        pattern = pat[3:] if pat.startswith("re:") else pat
        try:
            # Check if the pattern would match this specific command
            # Use word boundaries to avoid false positives
            if re.search(pattern, command_name):
                return True
        except re.error:
            # If regex is invalid, treat as literal string match
            if pattern in command_name:
                return True
    return False


def _get_optimizer_budget_tokens(config: Dict[str, Any]) -> int:
    # Try to get last optimizer allocation shared via dynamic optimizer helper
    try:
        from utils.dynamic_optimizer import get_last_total_allocation
        total = int(get_last_total_allocation(config) or 0)
        if total > 0:
            return total
    except Exception:
        pass
    # Fallback to max_token_budget
    try:
        return int(get_config_value(config, "max_token_budget", 10000))
    except Exception:
        return 10000


def _truncate_to_token_fraction(text: str, model: str, total_budget_tokens: int, fraction: float = 0.10, strategy: str = "head_tail") -> Tuple[str, bool, int]:
    max_tokens = max(1, int(total_budget_tokens * fraction))
    tok_count = count_tokens(text, model)
    if tok_count <= max_tokens:
        return text, False, tok_count
    if strategy == "head_tail":
        half = max(1, max_tokens // 2)
        head = truncate_text_to_tokens(text, half, model, keep_end=False)
        tail = truncate_text_to_tokens(text, half, model, keep_end=True)
        merged = head + "\n... [truncated middle] ...\n" + tail
        return merged, True, max_tokens
    # Default head-only
    truncated = truncate_text_to_tokens(text, max_tokens, model, keep_end=False)
    return truncated, True, max_tokens


def _path_is_within(path: Path, parent: Path) -> bool:
    """Return True if 'path' is inside 'parent' (after resolving)."""
    try:
        p = path.resolve()
        par = parent.resolve()
        return str(p) == str(par) or str(p).startswith(str(par) + os.sep)
    except Exception:
        return False

def _path_is_within_any(path: Path, parents: List[Path]) -> bool:
    """Return True if 'path' is inside any of the given parents."""
    for par in parents:
        if _path_is_within(path, par):
            return True
    return False


def _normalize_clone_paths_in_command(command: str, vault_root: Path) -> Tuple[str, Optional[str]]:
    """Rewrite common mistaken clone path references to the vault clones path.

    Examples normalized:
    - "../clones/XYZ" → "/abs/vault/backups/clones/XYZ"
    - "/clones/XYZ" → "/abs/vault/backups/clones/XYZ"

    Bare ``clones/...`` references are normalized as well so callers do not need
    a compatibility symlink inside the vault.

    Returns:
        (possibly_modified_command, advisory_note_if_modified)
    """
    try:
        clones_abs = str(Path(get_clone_root()).resolve())
    except Exception:
        clones_abs = str((vault_root / "backups" / "clones").resolve())

    original = command

    # Absolute /clones/... → absolute managed clones path
    command = re.sub(
        r"(?P<prefix>(^|\s|['\"]))/clones(?P<rest>/[^\s'\";&|]+)",
        lambda m: f"{m.group('prefix')}{clones_abs}{m.group('rest')}",
        command,
    )

    # One or more ../ prefixes before clones/...
    command = re.sub(
        r"(?P<prefix>(^|\s|['\"]))(?P<trav>(?:\.\./)+)clones(?P<rest>/[^\s'\";&|]+)",
        lambda m: f"{m.group('prefix')}{clones_abs}{m.group('rest')}",
        command,
    )

    # Bare relative clones references (clones or clones/...)
    command = re.sub(
        r"(?P<prefix>(^|\s|['\"`]))clones(?P<rest>(/[^\s'\";&|]+)?)\b",
        lambda m: f"{m.group('prefix')}{clones_abs}{m.group('rest')}",
        command,
    )

    note: Optional[str] = None
    if command != original:
        note = f"Normalized clone paths to {clones_abs}"
    return command, note


def _detect_cd_targets(segment: str) -> List[str]:
    """Best‑effort detection of 'cd <path>' targets within a command segment.

    This is intentionally lightweight, not a full shell parser. It catches
    common forms like 'cd /path && ...' or 'cd ../..; make'.
    """
    try:
        toks = shlex.split(segment, posix=True)
    except Exception:
        toks = segment.split()
    targets: List[str] = []
    for i, t in enumerate(toks[:-1]):
        if t == "cd":
            nxt = toks[i + 1]
            if nxt not in ("&&", ";", "||", "|"):
                targets.append(nxt)
    return targets

def _expand_theo_vars(s: str, vault_root: Path, project_root: Path) -> str:
    """Expand only THEO_* variables we control to absolute paths.

    This keeps policy checks consistent even when the user references
    $THEO_PROJECT_ROOT or ${THEO_PROJECT_ROOT} instead of an absolute path.

    We do not perform general env expansion here (the shell will do that);
    we only replace THEO_VAULT_ROOT, THEO_CLONES_ROOT, and THEO_PROJECT_ROOT
    to support our guardrails.
    """
    try:
        clones = (vault_root / "clones").resolve()
    except Exception:
        clones = (vault_root / "clones")
    mapping = {
        "$THEO_VAULT_ROOT": str(vault_root.resolve() if hasattr(vault_root, "resolve") else vault_root),
        "${THEO_VAULT_ROOT}": str(vault_root.resolve() if hasattr(vault_root, "resolve") else vault_root),
        "$THEO_CLONES_ROOT": str(clones),
        "${THEO_CLONES_ROOT}": str(clones),
        "$THEO_PROJECT_ROOT": str(project_root.resolve() if hasattr(project_root, "resolve") else project_root),
        "${THEO_PROJECT_ROOT}": str(project_root.resolve() if hasattr(project_root, "resolve") else project_root),
    }
    out = s
    try:
        for k, v in mapping.items():
            out = out.replace(k, v)
    except Exception:
        return s
    return out

def _any_path_within_project(paths: List[str], project_root: Path, vault_root: Path, allowed_clone_roots: List[Path]) -> Optional[str]:
    """Return the offending path if any resolves into the protected project root.

    Paths inside the vault or allowed clone roots are ignored.
    """
    for p in paths:
        try:
            # Expand ~ explicitly; THEO_* were expanded upstream
            if p.startswith("~"):
                p = os.path.expanduser(p)
            rp = Path(p).resolve() if os.path.isabs(p) else None
            if rp is None:
                # Relative paths won't hit project root since 'cd' into it is blocked
                continue
            if _path_is_within(rp, project_root) and not (
                _path_is_within(rp, Path(vault_root)) or _path_is_within_any(rp, allowed_clone_roots)
            ):
                return str(rp)
        except Exception:
            continue
    return None

def _extract_paths(tokens: List[str]) -> List[str]:
    """Return tokens that look like file/dir paths.

    Lightweight heuristic: include tokens that contain a '/'
    and do not start with '-' (option). Also extract values that
    appear as option arguments for flags like '-o', '--output', '-f', '--file',
    '-C', '--directory', '-d', '--dir'.
    """
    paths: List[str] = []
    for i, t in enumerate(tokens):
        if not t:
            continue
        if t.startswith("-"):
            # capture common option-with-arg patterns
            if t in ("-o", "--output", "-f", "--file", "-C", "--directory", "-d", "--dir"):
                if i + 1 < len(tokens):
                    paths.append(tokens[i + 1])
            continue
        if "/" in t or t.startswith("~"):
            paths.append(t)
    return paths

def _segment_writes_to_project_root(raw_segment: str, expanded_segment: str, project_root: Path, vault_root: Path, allowed_clone_roots: List[Path]) -> Optional[str]:
    """Detect if a single pipeline command attempts to write into the live project root.

    Returns the offending path (string) when a write is detected; otherwise None.

    Heuristics covered:
    - Redirections: >, >>, 1>, 2>, &> targeting a file under project root
    - Write-intent programs (rm, mv, cp, mkdir, rmdir, touch, ln, chmod, chown,
      chgrp, truncate, tee, sed -i, perl -i, patch, unzip, tar extract into -C
      project root, rsync dest under project root, git with -C project_root and
      mutating subcommands)
    - We do NOT allow 'cd' into project root anywhere (handled separately).
    """
    try:
        # 1) Redirections
        redir_matches = re.findall(r"(?:^|\s)(?:\d?>|\d?>>|&>|1>|2>|>>|>)(\s*)([^\s;&|]+)", expanded_segment)
        if redir_matches:
            for _sp, target in redir_matches:
                offending = _any_path_within_project([target], project_root, vault_root, allowed_clone_roots)
                if offending:
                    return offending

        # 2) Tokenize for program/args analysis
        try:
            toks = shlex.split(expanded_segment, posix=True)
        except Exception:
            toks = expanded_segment.split()
        if not toks:
            return None
        prog = os.path.basename(toks[0])

        # Helper to test any path tokens
        def _offending_in_tokens(tokens: List[str]) -> Optional[str]:
            paths = _extract_paths(tokens)
            ex_paths = []
            for p in paths:
                if p in ("$THEO_PROJECT_ROOT", "${THEO_PROJECT_ROOT}"):
                    ex_paths.append(str(project_root))
                elif p in ("$THEO_VAULT_ROOT", "${THEO_VAULT_ROOT}"):
                    ex_paths.append(str(vault_root))
                elif p in ("$THEO_CLONES_ROOT", "${THEO_CLONES_ROOT}"):
                    ex_paths.append(str((Path(vault_root) / "clones")))
                else:
                    ex_paths.append(p)
            return _any_path_within_project(ex_paths, project_root, vault_root, allowed_clone_roots)

        write_cmds_any_path = {
            "rm", "rmdir", "mkdir", "touch", "truncate", "chmod", "chown", "chgrp", "tee", "patch",
        }
        if prog in write_cmds_any_path:
            off = _offending_in_tokens(toks[1:])
            if off:
                return off

        if prog in {"mv", "cp", "install", "ln"}:
            # Destination is typically the last path token
            paths = _extract_paths(toks[1:])
            if paths:
                dest = paths[-1]
                off = _any_path_within_project([dest], project_root, vault_root, allowed_clone_roots)
                if off:
                    return off

        if prog == "sed":
            # Block only when in-place edit requested
            if any(a == "-i" or a.startswith("-i") for a in toks[1:]):
                off = _offending_in_tokens(toks[1:])
                if off:
                    return off

        if prog == "perl":
            # -i or -pi or -i.bak variants indicate in-place
            if any(a == "-i" or a.startswith("-i") or a.startswith("-pi") for a in toks[1:]):
                off = _offending_in_tokens(toks[1:])
                if off:
                    return off

        if prog == "git":
            # Detect -C <path> then block mutating subcommands
            # Allowed read-only: status, diff, log, show, grep, rev-parse, ls-files
            sub = None
            c_path = None
            i = 1
            while i < len(toks):
                t = toks[i]
                if t in ("-C", "--work-tree") and i + 1 < len(toks):
                    c_path = toks[i + 1]
                    i += 2
                    continue
                if not t.startswith("-") and sub is None:
                    sub = t
                i += 1
            if c_path:
                off = _any_path_within_project([c_path], project_root, vault_root, allowed_clone_roots)
                if off:
                    mutating = {
                        "add", "commit", "reset", "restore", "checkout", "switch", "merge", "rebase",
                        "apply", "am", "cherry-pick", "revert", "mv", "rm", "clean", "fetch", "pull",
                        "worktree", "submodule",
                    }
                    if sub is None or sub in mutating:
                        return off

        if prog == "rsync":
            # Consider last positional path as destination
            arg_paths = [t for t in toks[1:] if not t.startswith("-")]
            if arg_paths:
                dest = arg_paths[-1]
                off = _any_path_within_project([dest], project_root, vault_root, allowed_clone_roots)
                if off:
                    return off

        if prog == "tar":
            # Detect extract into project root: '-C <path>' with -x/--extract
            has_x = any(t == "-x" or t == "--extract" or (t.startswith("-") and "x" in t[1:]) for t in toks[1:])
            if has_x:
                # find -C path
                for i, t in enumerate(toks[1:], start=1):
                    if t in ("-C", "--directory") and i + 1 < len(toks):
                        cdir = toks[i + 1]
                        off = _any_path_within_project([cdir], project_root, vault_root, allowed_clone_roots)
                        if off:
                            return off
            # Creating archive with -f <path> inside project root is also a write
            has_c = any(t == "-c" or (t.startswith("-") and "c" in t[1:]) for t in toks[1:])
            if has_c:
                for i, t in enumerate(toks[1:], start=1):
                    if t in ("-f", "--file") and i + 1 < len(toks):
                        fpath = toks[i + 1]
                        off = _any_path_within_project([fpath], project_root, vault_root, allowed_clone_roots)
                        if off:
                            return off

        if prog == "unzip":
            # -d <path> destination under project root → block
            for i, t in enumerate(toks[1:], start=1):
                if t in ("-d", "--extract-dir") and i + 1 < len(toks):
                    d = toks[i + 1]
                    off = _any_path_within_project([d], project_root, vault_root, allowed_clone_roots)
                    if off:
                        return off

        return None
    except Exception:
        # On parser errors, fail closed by returning a sentinel path to block.
        try:
            return str(project_root)
        except Exception:
            return "/project"


def bash(command: str, timeout: Optional[int] = None, stdin: Optional[str] = None, env: Optional[Union[Dict[str, str], List[str]]] = None) -> Tuple[str, Dict[str, Any]]:
    """Execute a Bash command using pure blacklist security (maximum flexibility).

    WRITABLE PATHS:
      - /tmp/ (temporary files, auto-cleaned by system)
      - {vault_root}/clones/clone_YYYYMMDD_HHMMSS/ (code workspaces for safe experimentation)
      - Current working directory (typically vault root)
    
    READ-ONLY / RESTRICTED:
      - Project root (Theo's core code - use code tools for modifications)
      - System directories (/etc, /bin, /usr, etc.)
    
    NOTE: For persistent file storage, use vault-specific tools (send_file, etc.)
          rather than direct bash writes.

    Args:
        command: The shell command to execute (can include pipes)
        timeout: Optional timeout in seconds (clamped by config)
        stdin: Optional input to pass to the command
        env: Optional environment variables (filtered by allowlist). Can be a dict {K: V} or list ["K=V"].

    Returns:
        (result_text, tool_output)
    """
    start = time.time()
    try:
        cfg = load_config()
        # ... (rest of init) ...

        # (Redacted setup code not changing)
        # Determine vault root and working directory
        vault_root = get_vault_root(cfg)
        vault_root_str = str(
            get_config_value(cfg, "tools.bash.cwd", get_config_value(cfg, "tools.sh.cwd", vault_root))
            or vault_root
        )
        cwd = Path(vault_root_str)
        cwd.mkdir(parents=True, exist_ok=True)

        # Security configuration
        allowlist: List[str] = get_config_value(cfg, "tools.bash.allowlist") or _default_allowlist()
        sudo_allowlist: List[str] = get_config_value(cfg, "tools.bash.sudo_allowlist") or _default_sudo_allowlist()
        blocklist: List[str] = get_config_value(cfg, "tools.bash.blocklist") or _default_blocklist_patterns()
        security_mode = str(get_config_value(cfg, "tools.bash.security_mode", get_config_value(cfg, "tools.sh.security_mode", "blacklist")) or "blacklist").lower()
        # Load blacklist policy (blocked binaries, patterns, protected paths)
        policy = load_policy(vault_root)

        # Determine live project root for hard-rule checks below
        project_root = Path(__file__).parent.parent
        try:
            proj_abs = project_root.resolve()
        except Exception:
            proj_abs = project_root
        
        allowed_clone_roots: List[Path] = []
        try:
            allowed_clone_roots.append((Path(vault_root) / "clones").resolve())
        except Exception:
            allowed_clone_roots.append(Path(vault_root) / "clones")
        try:
            allowed_clone_roots.append((proj_abs / "clones").resolve())
        except Exception:
            allowed_clone_roots.append(proj_abs / "clones")

        # Timeout clamp
        default_timeout = int(get_config_value(cfg, "tools.bash.timeout_seconds", get_config_value(cfg, "tools.sh.timeout_seconds", 30)) or 30)
        max_timeout = max(5, default_timeout)
        to = int(timeout or default_timeout)
        if to > max_timeout:
            to = max_timeout

        # Model for token counting
        model = str(get_config_value(cfg, "primary_model", "gpt-5") or "gpt-5")

        # Normalize common mistaken clone paths (defensive convenience)
        command, norm_note = _normalize_clone_paths_in_command(command, cwd)

        # Policy pre-check (blocked binaries/patterns/paths)
        blocked, info = enforce_policy_precheck(command, policy, vault_root)
        if blocked:
            # Keep message brief and actionable
            reason = info.get("reason")
            if reason == "binary":
                msg = f"Blocked by policy (binary: {info.get('binary')})"
            elif reason == "pattern":
                msg = f"Blocked by policy (pattern: {info.get('pattern')})"
            elif reason in ("read_path", "write_path"):
                msg = f"Blocked by policy (protected path: {info.get('blocked_path')})"
            else:
                msg = "Blocked by policy"
            return msg, info

        # Additional legacy blocklist (defense-in-depth; may duplicate policy)
        blocked_pat = _is_blocked(command, blocklist)
        if blocked_pat:
            msg = f"Blocked by safety policy (pattern: {blocked_pat})"
            return msg, {
                "action": "bash",
                "success": False,
                "blocked": True,
                "pattern": blocked_pat,
                "chain": command,
                "blocked_context": {"reason": "pattern", "pattern": blocked_pat},
            }

        # Validate each segment and pipeline command name
        segments = _split_segments(command)
        if not segments:
            return "Empty command", {"action": "bash", "success": False, "error": "Empty command"}

        for seg_idx, seg in enumerate(segments):
            # Expand THEO_* envs for robust path detection
            expanded_seg = _expand_theo_vars(seg, vault_root, project_root)
            pipes = _split_pipeline(seg)
            for pipe_idx, p in enumerate(pipes):
                uses_sudo, prog, _argv = _first_program(p)
                if not prog:
                    return "Invalid command segment", {
                        "action": "sh",
                        "success": False,
                        "error": "Invalid segment",
                        "chain": command,
                        "segment": seg,
                        "segment_index": seg_idx,
                        "pipe_index": pipe_idx,
                    }
                if uses_sudo:
                    if not _matches_any(prog, sudo_allowlist):
                        blocked_pat2 = _is_blocked(command, blocklist)
                        if blocked_pat2:
                            msg = f"Blocked by safety policy (pattern: {blocked_pat2})"
                            return msg, {
                                "action": "sh",
                                "success": False,
                                "blocked": True,
                                "pattern": blocked_pat2,
                                "chain": command,
                            }
                        err_msg = (
                            f"sudo not allowed for '{prog}' in chain segment {seg_idx + 1}, pipe {pipe_idx + 1}: {p}"
                        )
                        return err_msg, {
                            "action": "bash",
                            "success": False,
                            "error": "sudo not allowed",
                            "blocked_command": p,
                            "blocked_program": prog,
                            "segment": seg,
                            "segment_index": seg_idx,
                            "pipe_index": pipe_idx,
                            "chain": command,
                        }
                else:
                    if security_mode == "allowlist":
                        if not _matches_any(prog, allowlist):
                            return (
                                f"Command '{prog}' not allowed by strict allowlist",
                                {
                                    "action": "sh",
                                    "success": False,
                                    "blocked": True,
                                    "blocked_command": p,
                                    "blocked_program": prog,
                                    "segment": seg,
                                    "segment_index": seg_idx,
                                    "pipe_index": pipe_idx,
                                    "chain": command,
                                },
                            )
                    else:
                        if _is_command_blocked(prog, blocklist):
                            msg = f"Command '{prog}' blocked by safety policy"
                            return msg, {
                                "action": "bash",
                                "success": False,
                                "blocked": True,
                                "blocked_command": p,
                                "blocked_program": prog,
                                "segment": seg,
                                "segment_index": seg_idx,
                                "pipe_index": pipe_idx,
                                "chain": command,
                            }

                try:
                    proj_root = project_root
                    offending = _segment_writes_to_project_root(p, expanded_seg, proj_root, vault_root, allowed_clone_roots)
                    if offending:
                        return (
                            f"Write to live source is blocked: {offending}",
                            {
                                "action": "bash",
                                "success": False,
                                "blocked": True,
                                "reason": "project_root_write_protected",
                                "blocked_path": offending,
                            },
                        )

                    for cd_target in _detect_cd_targets(p):
                        cd_expanded = _expand_theo_vars(cd_target, vault_root, project_root)
                        try:
                            target_path = (
                                Path(cd_expanded).resolve()
                                if os.path.isabs(cd_expanded)
                                else (cwd / cd_target).resolve()
                            )
                        except Exception:
                            continue
                        if _path_is_within(target_path, proj_root) and not (
                            _path_is_within(target_path, Path(vault_root)) or _path_is_within_any(target_path, allowed_clone_roots)
                        ):
                            return (
                                f"Changing directory into live source is blocked (read using absolute paths instead): {cd_target}",
                                {
                                    "action": "bash",
                                    "success": False,
                                    "blocked": True,
                                    "reason": "project_root_protected",
                                    "blocked_path": cd_target,
                                },
                            )
                except Exception:
                    pass

        # Prepare environment (allowlist)
        allowed_env_patterns = get_config_value(cfg, "tools.sh.allow_env") or [
            "THEO_*", "LC_ALL", "LANG", "PYTHON*", "PATH"
        ]
        def _env_allowed(k: str) -> bool:
            for pat in allowed_env_patterns:
                if pat.endswith("*"):
                    if k.startswith(pat[:-1]):
                        return True
                elif pat.startswith("*"):
                    if k.endswith(pat[1:]):
                        return True
                elif k == pat:
                    return True
            return False

        run_env = {k: v for k, v in os.environ.items() if _env_allowed(k)}
        # Ensure HOME is set to the vault for user-space tool installs/use
        try:
            run_env["HOME"] = str(vault_root)
        except Exception:
            pass
        # Helpful discovery env for Theo and tools
        try:
            run_env["THEO_VAULT_ROOT"] = str(vault_root.resolve())
        except Exception:
            run_env["THEO_VAULT_ROOT"] = str(vault_root)
        try:
            run_env["THEO_CLONES_ROOT"] = str(Path(get_clone_root()).resolve())
        except Exception:
            run_env["THEO_CLONES_ROOT"] = str(vault_root / "backups" / "clones")
        try:
            project_root_env = Path(__file__).parent.parent
            run_env["THEO_PROJECT_ROOT"] = str(project_root_env.resolve())
        except Exception:
            run_env["THEO_PROJECT_ROOT"] = str(Path(__file__).parent.parent)
        run_env.update({"DEBIAN_FRONTEND": "noninteractive"})
        
        # Process user-supplied env
        if env:
            # Normalize list ["K=V", ...] to dict {K: V}
            env_dict = {}
            if isinstance(env, list):
                for item in env:
                    if isinstance(item, str) and "=" in item:
                        k, v = item.split("=", 1)
                        env_dict[k] = v
            elif isinstance(env, dict):
                env_dict = env
            
            for k, v in env_dict.items():
                if _env_allowed(k):
                    run_env[k] = str(v)

        # Execute with shell to support pipes/redirects; non-interactive
        # Always run in Bash to support pipefail and richer shells
        bash_path = "/bin/bash"
        args = [bash_path, "-lc", command]
        proc = subprocess.run(
            args,
            input=(stdin.encode("utf-8") if isinstance(stdin, str) else None),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=run_env,
            timeout=to,
        )

        raw_out = proc.stdout.decode("utf-8", errors="replace")
        raw_err = proc.stderr.decode("utf-8", errors="replace")
        full_text = raw_out if raw_err.strip() == "" else (raw_out + "\n[stderr]\n" + raw_err)

        # Truncate to 10% of current token budget
        total_budget = _get_optimizer_budget_tokens(cfg)
        strategy = str(get_config_value(cfg, "tools.bash.truncate_strategy", get_config_value(cfg, "tools.sh.truncate_strategy", "head_tail")) or "head_tail")
        truncated_text, truncated, tokens_used = _truncate_to_token_fraction(full_text, model, total_budget, 0.10, strategy)

        # Optionally save full output when truncated
        output_file = None
        if truncated and bool(get_config_value(cfg, "tools.bash.log_to_vault", get_config_value(cfg, "tools.sh.log_to_vault", True))):
            logs_dir = cwd / "sh_logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            output_file = logs_dir / f"sh_{ts}.log"
            try:
                output_file.write_text(full_text, encoding="utf-8")
            except Exception:
                output_file = None

        duration_ms = int((time.time() - start) * 1000)
        summary = (
            f"PASS (exit {proc.returncode}), duration {duration_ms} ms, tokens {tokens_used}, truncated: {truncated}"
            if proc.returncode == 0
            else f"FAIL (exit {proc.returncode}), duration {duration_ms} ms, tokens {tokens_used}, truncated: {truncated}"
        )
        advisory_lines = []
        if 'norm_note' in locals() and norm_note:
            advisory_lines.append(norm_note)
        # If we saved the full output due to truncation, surface the path so Theo can find it
        if truncated and output_file is not None:
            try:
                advisory_lines.append(f"Full untruncated output saved to: {str(output_file)}")
            except Exception:
                # Best-effort; absence of path is non-fatal
                pass
        advisory = ("\n" + "\n".join(advisory_lines)) if advisory_lines else ""
        result_text = summary + "\n" + (truncated_text or "") + advisory

        result_meta: Dict[str, Any] = {
            "action": "bash",
            "success": True,  # Tool successfully ran (even if command failed) to avoid tripping circuit breaker
            "command_success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
            "truncated": truncated,
            "tokens_used": tokens_used,
            "cwd": str(cwd),
            "output_file": str(output_file) if output_file else None,
        }
        if 'norm_note' in locals() and norm_note:
            result_meta["normalized_clone_paths"] = True
            # Safe to echo the env value we just set
            result_meta["normalized_to"] = run_env.get("THEO_CLONES_ROOT")
        # Provide a concise top-level error for orchestrator when command failed
        if proc.returncode != 0:
            first_line = (truncated_text or full_text).splitlines()[0:1]
            err_summary = f"Command failed (exit {proc.returncode})"
            if first_line:
                err_summary += f": {first_line[0][:200]}"
            result_meta["error"] = err_summary

        return result_text, result_meta

    except subprocess.TimeoutExpired:
        return "Command timed out", {
            "action": "bash",
            "success": False,
            "error": "timeout",
        }
    except Exception as e:
        logger.error(f"bash tool error: {e}", exc_info=True)
        return f"bash error: {e}", {
            "action": "bash",
            "success": False,
            "error": str(e),
        }


__all__ = ["bash"]
