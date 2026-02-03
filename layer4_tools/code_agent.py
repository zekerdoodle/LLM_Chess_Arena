"""
Code Agent tools for invoking external coding assistants.

Currently provides integration with the Codex CLI. Enforces clone-only
execution: the Codex process is executed with the working directory set to a
vault clone (created via the create_clone tool).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Tuple, Optional, Any

from utils.logger import get_logger

logger = get_logger(__name__)


def _resolve_clone_path(clone_path: str) -> Path:
    """Resolve a clone path like 'clones/clone_YYYYMMDD_HHMMSS' to an absolute path."""

    clone_full_path = Path(clone_path)
    project_root = Path(__file__).parent.parent
    try:
        from utils.vault_paths import get_clone_root

        allowed_root = Path(get_clone_root()).resolve()
    except Exception:
        allowed_root = (project_root / "vault" / "backups" / "clones").resolve()

    # Build candidate absolute path
    if clone_full_path.is_absolute():
        candidate = clone_full_path.resolve()
    else:
        normalized = clone_path.strip()
        if normalized.startswith("clones/"):
            candidate = (allowed_root / Path(normalized[len("clones/"):])).resolve()
        elif normalized.startswith("vault/backups/clones/"):
            candidate = (allowed_root / Path(normalized[len("vault/backups/clones/"):])).resolve()
        else:
            candidate = (allowed_root / Path(normalized)).resolve()

    # Enforce containment within managed clones workspace
    try:
        candidate.relative_to(allowed_root)
    except Exception:
        # Point to a path that will fail existence checks upstream
        return allowed_root / "__invalid_outside_clones__"

    return candidate


def _ensure_git_repo(repo_path: Path) -> None:
    """Initialize a minimal git repository with an initial commit if missing.

    Codex CLI in full-auto mode may require a tracked directory. We best-effort
    init a repo, configure identity, add all files, and make an initial commit.
    Failures are logged as warnings but do not raise.
    """
    try:
        if (repo_path / ".git").exists():
            return
        # git init
        subprocess.run(["git", "init"], cwd=str(repo_path), capture_output=True, text=True, check=False)
        # minimal identity; avoid global config dependence
        subprocess.run(["git", "config", "user.email", "theo@local"], cwd=str(repo_path), capture_output=True, text=True, check=False)
        subprocess.run(["git", "config", "user.name", "Theo"], cwd=str(repo_path), capture_output=True, text=True, check=False)
        # add all and commit
        subprocess.run(["git", "add", "-A"], cwd=str(repo_path), capture_output=True, text=True, check=False)
        subprocess.run(["git", "commit", "-m", "Initial clone snapshot for Codex"], cwd=str(repo_path), capture_output=True, text=True, check=False)
        logger.debug("L4.tools [tool:code_agent] - Initialized git repo in clone for Codex")
    except Exception as e:
        logger.warning("L4.tools [tool:code_agent] - Failed to initialize git repo in clone: %s", e)


def code_agent(
    prompt: str,
    clone_path: str,
    timeout_seconds: int = 900,
) -> Tuple[str, Dict[str, Any]]:
    
    """Run the Codex CLI with full-auto settings inside a clone directory.

    Args:
        prompt: The prompt to send to Codex.
        clone_path: Relative path to the clone (e.g., 'clones/clone_20250101_120000').
        timeout_seconds: Maximum seconds to allow the Codex process to run.

    Returns:
        A tuple of (human_readable_summary, structured_output_dict).
    """
    try:
        logger.info("L4.tools [tool:code_agent] - Preparing to run Codex in clone: %s", clone_path)

        # Enforce clone-only operation and path containment
        if not clone_path or not clone_path.startswith("clones/"):
            msg = "Codex is restricted to clones. Provide clone_path like 'clones/clone_YYYYMMDD_HHMMSS'."
            logger.error("L4.tools [tool:code_agent] - %s", msg)
            return msg, {"success": False, "error": msg}

        resolved = _resolve_clone_path(clone_path)
        if not resolved.exists() or not resolved.is_dir():
            msg = f"Clone directory not found or outside managed clones/: {clone_path}"
            logger.error("L4.tools [tool:code_agent] - %s", msg)
            return msg, {"success": False, "error": msg}

        # Ensure the clone is a tracked git repo to satisfy Codex full-auto safeguards
        _ensure_git_repo(resolved)

        # Build command (no shell) to avoid quoting issues; pass prompt as arg
        # Use non-interactive exec mode and full-auto convenience flag
        cmd = [
            "codex",
            "exec",
            "--full-auto",
            prompt,
        ]

        logger.debug("L4.tools [tool:code_agent] - Running: %s (cwd=%s)", " ".join(cmd), str(resolved))

        # Ensure API key mode for Codex CLI (inherit env; attempt config fallback)
        env = os.environ.copy()
        if not env.get("OPENAI_API_KEY"):
            # Try to load from config.yaml for headless environments
            try:
                from utils.config_loader import load_config, get_config_value
                cfg = load_config()
                key = get_config_value(cfg, "api_keys.openai")
                if key and isinstance(key, str) and not key.startswith("${"):
                    env["OPENAI_API_KEY"] = key
                    logger.info("L4.tools [tool:code_agent] - Using OPENAI_API_KEY from config for Codex")
                else:
                    logger.warning("L4.tools [tool:code_agent] - OPENAI_API_KEY not found; Codex may attempt interactive login")
            except Exception:
                logger.warning("L4.tools [tool:code_agent] - Could not load OPENAI_API_KEY from config")

        completed = subprocess.run(
            cmd,
            cwd=str(resolved),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        rc = completed.returncode

        # Verify actual file changes using git diff (most reliable method)
        verified_files = []
        git_diff_verified = False
        try:
            git_diff_result = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=str(resolved),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            
            if git_diff_result.returncode == 0:
                git_diff_verified = True
                # Parse git diff --stat output
                # Lines look like: " path/to/file.py | 10 +++++-----"
                for line in git_diff_result.stdout.splitlines():
                    if "|" in line:
                        filepath = line.split("|")[0].strip()
                        if filepath:
                            verified_files.append(filepath)
                
                logger.debug(
                    "L4.tools [tool:code_agent] - Git diff verification: %d files changed",
                    len(verified_files)
                )
        except Exception as e:
            logger.warning(
                "L4.tools [tool:code_agent] - Git diff verification failed: %s", e
            )

        # Extract key information from stdout for summary (fallback method)
        modified_files = []
        try:
            # Look for common patterns indicating file modifications
            for line in stdout.splitlines():
                # Git-style diff headers
                if line.startswith("--- a/") or line.startswith("+++ b/"):
                    filepath = line.split("/", 1)[1] if "/" in line else ""
                    if filepath and filepath not in modified_files:
                        modified_files.append(filepath)
                # Direct file write/edit messages from Codex
                elif "wrote" in line.lower() or "modified" in line.lower() or "created" in line.lower():
                    # Try to extract filenames
                    import re
                    file_pattern = r'[\w/._-]+\.\w+'
                    matches = re.findall(file_pattern, line)
                    for match in matches:
                        if match not in modified_files and "/" in match:
                            modified_files.append(match)
        except Exception:
            pass

        # Build clear status header
        status_header = "=" * 60 + "\n"
        status_header += "CODE AGENT RESULT\n"
        status_header += "=" * 60 + "\n"
        
        if rc == 0:
            status_header += "Status: ✓ SUCCESS (exit code 0)\n"
        else:
            status_header += f"Status: ✗ FAILED (exit code {rc})\n"
        
        status_header += f"Clone: {clone_path}\n"
        
        # Use verified files if available, otherwise fall back to parsed files
        files_to_report = verified_files if git_diff_verified else modified_files
        
        if files_to_report:
            verification_label = "verified" if git_diff_verified else "detected"
            status_header += f"Files Modified ({verification_label}): {len(files_to_report)}\n"
            for f in files_to_report[:10]:  # Limit to first 10 files
                status_header += f"  - {f}\n"
            if len(files_to_report) > 10:
                status_header += f"  ... and {len(files_to_report) - 10} more\n"
        else:
            # No changes detected
            if "no changes" in stdout.lower() or "nothing to commit" in stdout.lower():
                status_header += "Files Modified: 0 (no changes needed)\n"
            elif rc == 0 and git_diff_verified:
                # WARNING: Success exit code but no actual file changes
                status_header += "⚠️  WARNING: No file modifications detected!\n"
                status_header += "Files Modified: 0\n"
                status_header += "\n"
                status_header += "The agent reported success but made no actual file changes.\n"
                status_header += "This usually means:\n"
                status_header += "  • The agent analyzed the code without making edits\n"
                status_header += "  • The requested changes were not needed\n"
                status_header += "  • The agent encountered an issue preventing edits\n"
                status_header += "\n"
                status_header += "⚠️  Do NOT use apply_patch - there are no changes to apply!\n"
                status_header += "Use preview_patch to verify if unsure.\n"
            elif rc == 0:
                # Couldn't verify, warn user to check
                status_header += "Files Modified: Unknown (verification unavailable)\n"
                status_header += "⚠️  Tip: Use preview_patch to verify changes before applying\n"
        
        status_header += "\n"
        status_header += "=" * 60 + "\n\n"

        # Summarize output for chat history; include the last N lines (where Codex usually prints summaries)
        tail_lines = 200
        def _tail(text: str, n: int) -> str:
            try:
                lines = text.splitlines()
                if len(lines) <= n:
                    return text
                return "\n".join(lines[-n:])
            except Exception:
                return text

        summary_parts = [status_header]  # Start with status header
        
        if stdout.strip():
            summary_parts.append(f"Detailed Output (last {tail_lines} lines):\n" + _tail(stdout, tail_lines))
        if stderr.strip():
            summary_parts.append(f"Stderr (last {tail_lines} lines):\n" + _tail(stderr, tail_lines))
        
        if len(summary_parts) == 1:  # Only header, no output
            summary_parts.append(f"Codex finished with no output.")
        
        summary = "\n\n".join(summary_parts)

        result = {
            "success": rc == 0,
            "returncode": rc,
            "stdout": stdout,
            "stderr": stderr,
            "clone_path": clone_path,
            "modified_files": verified_files if git_diff_verified else modified_files,
            "verified": git_diff_verified,
        }

        if rc != 0 and stderr.strip():
            # Surface stderr for troubleshooting (often contains the reason, e.g., missing API key or policy refusal)
            logger.warning("L4.tools [tool:code_agent] - Codex stderr (rc=%s): %s", rc, stderr.strip()[:2000])
        
        files_count = len(verified_files) if git_diff_verified else len(modified_files)
        logger.info(
            "L4.tools [tool:code_agent] - Codex completed (rc=%s, stdout=%dB, stderr=%dB, files=%d, verified=%s)",
            rc, len(stdout), len(stderr), files_count, git_diff_verified
        )

        return summary, result

    except subprocess.TimeoutExpired as e:
        msg = f"Codex timed out after {timeout_seconds}s"
        logger.error("L4.tools [tool:code_agent] - %s", msg)
        return msg, {"success": False, "error": msg, "clone_path": clone_path}
    except FileNotFoundError:
        msg = "Codex CLI not found. Ensure 'codex' is installed and on PATH."
        logger.error("L4.tools [tool:code_agent] - %s", msg)
        return msg, {"success": False, "error": msg, "clone_path": clone_path}
    except Exception as e:
        msg = f"Codex invocation failed: {str(e)}"
        logger.error("L4.tools [tool:code_agent] - %s", msg)
        return msg, {"success": False, "error": msg, "clone_path": clone_path}
