"""
Coding Tools for Theo AI Assistant

Available Tools:
- read_code_structure(file_path) - Parse AST for code overview
- run_quick_lint(file_path) - Run style/syntax checks with Flake8
- execute_tests(test_pattern) - Execute unit/functional tests with pytest

All operations work on source code files and provide comprehensive analysis
for Theo's self-improvement capabilities.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.logger import get_logger

logger = get_logger(__name__)

PYTHON_EXTENSIONS = {".py", ".pyi"}


def _is_within(path: Path, base: Path) -> bool:
    """Return True if *path* is inside *base* (handles symlinks safely)."""

    try:
        path.resolve(strict=False).relative_to(base.resolve(strict=False))
        return True
    except Exception:
        return False


def _sanitize_relative_path(relative: Path) -> Path:
    """Normalize a relative path and reject traversal attempts."""

    parts: List[str] = []
    for part in relative.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Path traversal outside clone root is not allowed")
        parts.append(part)
    if not parts:
        return Path(".")
    return Path(*parts)


def _get_project_root() -> Path:
    """Return the Theo project root."""

    return Path(__file__).resolve().parent.parent


def _get_vault_root(project_root: Path) -> Path:
    """Resolve the configured vault root, creating it when missing."""

    try:
        from utils.vault_paths import get_vault_root  # type: ignore

        vault_root = Path(get_vault_root())
    except Exception:
        vault_root = project_root / "vault"

    try:
        vault_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Directory creation may fail in read-only scenarios; callers handle later.
        pass
    return vault_root.resolve(strict=False)


def _candidate_clone_roots(project_root: Path) -> List[Path]:
    """Return the canonical clone root under the shared vault."""

    try:
        from utils.vault_paths import get_clone_root  # type: ignore

        clone_root = Path(get_clone_root())
    except Exception:
        vault_root = _get_vault_root(project_root)
        clone_root = vault_root / "backups" / "clones"

    candidates = [clone_root]

    resolved: List[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            resolved_candidate = candidate.resolve(strict=False)
        except Exception:
            resolved_candidate = candidate
        key = str(resolved_candidate)
        if key not in seen:
            seen.add(key)
            resolved.append(resolved_candidate)
    return resolved


def _rebase_legacy_path(path: Path) -> Path:
    """Rebase legacy clone paths into the canonical vault tree."""

    try:
        from utils.vault_paths import rebase_legacy_vault_path  # type: ignore
    except Exception:
        return path

    try:
        rebased = rebase_legacy_vault_path(path, create_parents=False)
    except Exception:
        return path

    try:
        return Path(rebased)
    except Exception:
        return path


def _get_latest_clone(project_root: Path) -> Optional[str]:
    """Get the most recently created clone directory name."""
    clones_roots = _candidate_clone_roots(project_root)
    all_clones = []
    
    for clones_root in clones_roots:
        if clones_root.exists():
            clones = [d.name for d in clones_root.iterdir() 
                     if d.is_dir() and d.name.startswith("clone_")]
            all_clones.extend(clones)
    
    if not all_clones:
        return None
    
    # Sort by name (which includes timestamp) and return the latest
    return sorted(all_clones)[-1]


def _normalize_clone_string(raw: str) -> Optional[str]:
    """Normalize various clone path expressions into 'clones/<...>' form."""

    if not raw:
        return None

    normalized = raw.replace("\\", "/").strip()
    if not normalized:
        return None

    if normalized.startswith("/"):
        normalized = normalized[1:]
    while normalized.startswith("./"):
        normalized = normalized[2:]

    lowered = normalized.lower()
    if lowered.startswith("vault/backups/clones/"):
        tail = normalized[len("vault/backups/clones/") :]
        return f"clones/{tail}"

    if lowered.startswith("vault/clones/"):
        tail = normalized[len("vault/clones/") :]
        return f"clones/{tail}"

    if lowered.startswith("layer3_longterm/vault/clones/"):
        tail = normalized[len("layer3_longterm/vault/clones/") :]
        try:
            _rebase_legacy_path(Path("layer3_longterm") / "vault" / "clones" / tail)
        except Exception:
            pass
        return f"clones/{tail}"

    if lowered.startswith("layer3_longterm/vault/backups/clones/"):
        tail = normalized[len("layer3_longterm/vault/backups/clones/") :]
        try:
            _rebase_legacy_path(
                Path("layer3_longterm") / "vault" / "backups" / "clones" / tail
            )
        except Exception:
            pass
        return f"clones/{tail}"

    if lowered.startswith("clones/"):
        return normalized

    if "/clones/" in lowered:
        tail = normalized.split("/clones/", 1)[1]
        return f"clones/{tail}"

    return None


def _resolve_clone_path(raw_path: str, project_root: Path) -> Tuple[Path, Path]:
    """Resolve a clone-relative path to an absolute path and clone root.

    Returns:
        Tuple of (resolved_path, clone_root_path).

    Raises:
        ValueError: If the path is outside allowed clone directories.
    """

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Provide a non-empty clone path (clones/<clone_id>/...).")

    # Auto-scope relative paths to latest clone for convenience
    # If path doesn't start with 'clones/' and isn't absolute, prepend latest clone
    if not raw_path.strip().startswith(('clones/', '/', 'vault/')):
        latest_clone = _get_latest_clone(project_root)
        if latest_clone:
            logger.debug(
                f"L4.tools [coding_tools] - Auto-scoping relative path '{raw_path}' to latest clone: {latest_clone}"
            )
            raw_path = f"clones/{latest_clone}/{raw_path.strip()}"

    path_obj = Path(raw_path)
    clones_roots = _candidate_clone_roots(project_root)

    # Accept absolute paths that are already within a clone root
    if path_obj.is_absolute():
        path_obj = _rebase_legacy_path(path_obj)
        resolved_abs = path_obj.resolve(strict=False)
        for clones_root in clones_roots:
            if _is_within(resolved_abs, clones_root):
                relative = resolved_abs.relative_to(clones_root)
                if not relative.parts:
                    raise ValueError("Clone path must include a clone identifier and file/directory")
                clone_root = (clones_root / relative.parts[0]).resolve(strict=False)
                return resolved_abs, clone_root
        # Fall back to string normalization when the absolute path is an alias
        normalized = _normalize_clone_string(str(resolved_abs))
    else:
        normalized = _normalize_clone_string(raw_path)

    if not normalized:
        # List available clones to help, with latest clone suggestion
        try:
            available_clones = []
            for clones_root in clones_roots:
                if clones_root.exists():
                    clones = [d.name for d in clones_root.iterdir() if d.is_dir() and d.name.startswith("clone_")][:3]
                    available_clones.extend(clones)
            if available_clones:
                clone_list = ", ".join(available_clones[:5])
                latest_clone = _get_latest_clone(project_root)
                hint = f"\n\nHint: Try 'clones/{latest_clone}/your/file.py'" if latest_clone else ""
                raise ValueError(
                    f"Invalid clone path format. Use 'clones/clone_YYYYMMDD_HHMMSS/path/to/file.py'\n\n"
                    f"Available clones: {clone_list}{hint}"
                )
        except ValueError:
            raise
        except Exception:
            pass
        raise ValueError(
            "Use clone-scoped paths like 'clones/clone_YYYYMMDD_HHMMSS/path/to/file.py'."
        )

    relative = Path(normalized[len("clones/"):])
    relative = _sanitize_relative_path(relative)
    if not relative.parts or relative.parts[0] in (".", ""):
        raise ValueError("Clone path must include a clone identifier and file/directory")

    fallback_path: Optional[Path] = None
    fallback_clone_root: Optional[Path] = None

    for clones_root in clones_roots:
        try:
            base = clones_root.resolve(strict=False)
        except Exception:
            base = clones_root

        clone_root = (base / relative.parts[0]).resolve(strict=False)
        candidate = (base / relative).resolve(strict=False)

        if fallback_path is None:
            fallback_path = candidate
            fallback_clone_root = clone_root

        if candidate.exists():
            return candidate, clone_root

    if fallback_path is not None and fallback_clone_root is not None:
        return fallback_path, fallback_clone_root

    raise ValueError("Clone path could not be resolved; ensure the clone exists first.")


def read_code_structure(file_path: str) -> Tuple[str, Dict[str, Any]]:
    """
    Parse AST for fast/read-only code inspection.

    Args:
        file_path: Path to Python file to analyze

    Returns:
        Tuple of (structure_analysis, tool_output)
    """
    try:
        logger.info(
            f"L4.tools [tool:read_code_structure] - Analyzing code structure: {file_path}"
        )

        project_root = _get_project_root()
        try:
            file_obj, clone_root = _resolve_clone_path(file_path, project_root)
        except ValueError as exc:
            logger.warning(
                "L4.tools [tool:read_code_structure] - Invalid path '%s': %s",
                file_path,
                exc,
            )
            return str(exc), {
                "action": "read_code_structure",
                "success": False,
                "error": "invalid_path",
                "details": str(exc),
            }

        if not file_obj.exists() or not file_obj.is_file():
            return f"Failed to read file: {file_path}", {
                "action": "read_code_structure",
                "success": False,
                "error": "File not found",
            }

        if file_obj.suffix.lower() not in PYTHON_EXTENSIONS:
            return (
                f"Unsupported file type for AST analysis: {file_obj.suffix or 'unknown'}",
                {
                    "action": "read_code_structure",
                    "success": False,
                    "error": "unsupported_extension",
                },
            )
        try:
            content = file_obj.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}", {
                "action": "read_code_structure",
                "success": False,
                "error": f"Read error: {e}",
            }

        # Parse AST
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as e:
            logger.warning(
                f"L4.tools [tool:read_code_structure] - Syntax error in {file_path}: {e}"
            )
            return f"Syntax error in {file_path}: {e}", {
                "action": "read_code_structure",
                "success": False,
                "error": f"Syntax error: {e}",
            }

        # Analyze structure
        analysis = _analyze_ast(tree, file_path)

        # Format analysis for output
        output_lines = [
            f"Code Structure Analysis for {file_path}",
            "=" * 50,
            "",
            f"File size: {len(content)} characters",
            f"Lines of code: {len(content.splitlines())}",
            "",
        ]

        if analysis["imports"]:
            output_lines.append("Imports:")
            for imp in analysis["imports"]:
                output_lines.append(f"  - {imp}")
            output_lines.append("")

        if analysis["classes"]:
            output_lines.append("Classes:")
            for cls in analysis["classes"]:
                output_lines.append(f"  - {cls['name']} (line {cls['line']})")
                if cls["methods"]:
                    for method in cls["methods"]:
                        output_lines.append(
                            f"    * {method['name']}() (line {method['line']})"
                        )
            output_lines.append("")

        if analysis["functions"]:
            output_lines.append("Functions:")
            for func in analysis["functions"]:
                output_lines.append(
                    f"  - {func['name']}() (line {func['line']})"
                )
            output_lines.append("")

        if analysis["globals"]:
            output_lines.append("Global Variables:")
            for var in analysis["globals"]:
                output_lines.append(f"  - {var}")
            output_lines.append("")

        structure_text = "\n".join(output_lines)

        logger.info(
            f"L4.tools [tool:read_code_structure] - Analysis complete (classes: {len(analysis['classes'])}, functions: {len(analysis['functions'])})"
        )
        logger.debug(
            f"L4.tools [tool:read_code_structure] - Found imports: {len(analysis['imports'])}"
        )

        try:
            clone_relative = str(file_obj.relative_to(clone_root))
        except Exception:
            clone_relative = file_obj.name

        return structure_text, {
            "action": "read_code_structure",
            "success": True,
            "file_path": file_path,
            "analysis": analysis,
            "lines_of_code": len(content.splitlines()),
            "character_count": len(content),
            "classes_count": len(analysis["classes"]),
            "functions_count": len(analysis["functions"]),
            "imports_count": len(analysis["imports"]),
            "clone_path": str(clone_root),
            "clone_relative_path": clone_relative,
        }

    except Exception as e:
        logger.error(
            f"L4.tools [tool:read_code_structure] - Error analyzing {file_path}: {e}",
            exc_info=True,
        )
        return f"Error analyzing code structure: {str(e)}", {
            "action": "read_code_structure",
            "success": False,
            "error": str(e),
        }


def run_quick_lint(file_path: str) -> Tuple[str, Dict[str, Any]]:
    """
    Rapidly check for style violations and syntax errors with Flake8.

    Args:
        file_path: Path to Python file to lint

    Returns:
        Tuple of (lint_results, tool_output)
    """
    try:
        logger.info(
            f"L4.tools [tool:run_quick_lint] - Linting file: {file_path}"
        )

        # Handle vault-relative paths (e.g., "clones/clone_TIMESTAMP/main.py")
        project_root = _get_project_root()
        try:
            file_obj, clone_root = _resolve_clone_path(file_path, project_root)
        except ValueError as exc:
            logger.warning(
                "L4.tools [tool:run_quick_lint] - Invalid path '%s': %s",
                file_path,
                exc,
            )
            return str(exc), {
                "action": "run_quick_lint",
                "success": False,
                "error": "invalid_path",
                "details": str(exc),
            }

        # Check if file exists
        if not file_obj.exists() or not file_obj.is_file():
            return f"File not found: {file_path}", {
                "action": "run_quick_lint",
                "success": False,
                "error": "File not found",
            }

        if file_obj.suffix.lower() not in PYTHON_EXTENSIONS:
            return (
                f"Unsupported file type for linting: {file_obj.suffix or 'unknown'}",
                {
                    "action": "run_quick_lint",
                    "success": False,
                    "error": "unsupported_extension",
                },
            )

        # Skip binary files or files containing NUL bytes to avoid flake8 crashes
        try:
            raw = file_obj.read_bytes()
            if b"\x00" in raw:
                msg = f"Skipped lint: {file_path} appears to be binary (contains NUL bytes)"
                logger.warning(f"L4.tools [tool:run_quick_lint] - {msg}")
                return msg, {
                    "action": "run_quick_lint",
                    "success": True,
                    "file_path": file_path,
                    "passed": True,
                    "issues_count": 0,
                    "issues": [],
                    "raw_output": "",
                    "stderr": "",
                }
        except Exception:
            # If we can't read bytes safely, proceed to flake8 which will handle text-only
            pass

        # Build flake8 command (prefer project virtualenv Python)
        venv_python_primary = project_root / ".venv" / "bin" / "python"
        venv_python_legacy = project_root / "venv" / "bin" / "python"
        if venv_python_primary.exists():
            python_executable = str(venv_python_primary)
        elif venv_python_legacy.exists():
            python_executable = str(venv_python_legacy)
        else:
            python_executable = sys.executable

        # Run flake8
        try:
            # Use --isolated to ignore project-level .flake8 excludes that would skip
            # vault files during unit tests. We pass our desired options explicitly.
            result = subprocess.run(
                [
                    python_executable,
                    "-m",
                    "flake8",
                    "--isolated",
                    "--select=E,W,F",
                    "--max-line-length=120",
                    "--extend-ignore=E203,W503",
                    str(file_obj),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            returncode = result.returncode
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            # Parse results
            if returncode == 0:
                lint_message = f"PASS: Lint check passed for {file_path}\nNo style violations or syntax errors found."
                logger.info(
                    f"L4.tools [tool:run_quick_lint] - Lint passed with no issues"
                )
                issues = []
            else:
                issues = _parse_flake8_output(stdout)
                issue_count = len(issues)
                lint_message = f"WARN: Lint check found {issue_count} issues in {file_path}:\n\n{stdout}"
                logger.info(
                    f"L4.tools [tool:run_quick_lint] - Lint completed with {issue_count} issues"
                )

            if stderr:
                logger.warning(
                    f"L4.tools [tool:run_quick_lint] - Flake8 stderr: {stderr}"
                )

            return lint_message, {
                "action": "run_quick_lint",
                "success": True,
                "file_path": file_path,
                "passed": returncode == 0,
                "issues_count": len(issues),
                "issues": issues,
                "raw_output": stdout,
                "stderr": stderr,
                "clone_path": str(clone_root),
            }

        except subprocess.TimeoutExpired:
            logger.error(
                f"L4.tools [tool:run_quick_lint] - Flake8 timeout for {file_path}"
            )
            return f"Lint check timed out for {file_path}", {
                "action": "run_quick_lint",
                "success": False,
                "error": "Timeout",
            }
        except FileNotFoundError:
            logger.error(f"L4.tools [tool:run_quick_lint] - Flake8 not found")
            return "Flake8 not installed or not found", {
                "action": "run_quick_lint",
                "success": False,
                "error": "Flake8 not found",
            }

    except Exception as e:
        logger.error(
            f"L4.tools [tool:run_quick_lint] - Error linting {file_path}: {e}",
            exc_info=True,
        )
        return f"Error running lint check: {str(e)}", {
            "action": "run_quick_lint",
            "success": False,
            "error": str(e),
        }


def execute_tests(
    test_pattern: str = "tests/",
    verbose: bool = False,
    profile: str = "smoke",
    keywords: str = "",
    markers: str = "",
    nodeids: Union[str, List[str], None] = None,
    paths: Union[str, List[str], None] = None,
    maxfail: int = 1,
    timeout: int = 25,
    durations: int = 10,
    last_failed: bool = False,
    failed_first: bool = False,
    collect_only: bool = False,
    verbosity: int = 0,
    extra_pytest_args: Union[str, List[str], None] = None,
    include_heavy: bool = False,
    include_network: bool = False,
    list_profiles: bool = False,
    allow_project_root: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """
    Execute unit/functional tests with pytest.

    For clone testing, automatically runs a targeted subset of core tests instead of
    the full 522-test suite to prevent timeout issues and ensure fast validation.

    Args:
        test_pattern: Test file pattern or specific test to run
        verbose: Whether to run in verbose mode

    Returns:
        Tuple of (test_results, tool_output)
    """
    try:
        logger.info(
            f"L4.tools [tool:execute_tests] - Running tests: {test_pattern} (profile={profile})"
        )

        profiles = {
            "smoke": {
                "k": "token_counter or config_loader or prompt_builder or prompt_printer_basic or chat_history or channel_manager or analysis_tools or interruptions or smart_reindexing_focused",
                "m": "not slow and not integration and not demo",
            },
            "unit-core": {
                "k": "token_counter or config_loader or prompt_builder or prompt_printer_basic or chat_history or channel_manager or analysis_tools or memory_manager or interruptions",
                "m": "not slow and not integration and not demo",
            },
            "integration": {
                "k": "layer1 or web_search_tools or memory_context_integration",
                "m": "integration and not slow",
            },
            "slow": {
                "k": "embedding_quality or local_embeddings or smart_search or dual_indexing",
                "m": "slow",
            },
        }

        if list_profiles:
            return (
                "Available test profiles: " + ", ".join(sorted(profiles.keys())),
                {"action": "execute_tests", "success": True, "profiles": profiles},
            )

        # Handle vault-relative paths (e.g., "clones/clone_TIMESTAMP/tests/")
        project_root = _get_project_root()
        working_directory = project_root
        is_clone_test = False
        clone_root: Optional[Path] = None
        original_pattern = test_pattern

        try:
            resolved_target, clone_root = _resolve_clone_path(test_pattern, project_root)
            is_clone_test = True
            working_directory = clone_root
            relative_target = resolved_target.relative_to(clone_root)
            relative_str = str(relative_target).strip()

            if not relative_str or relative_str in {".", ""}:
                test_pattern = "tests/"
            else:
                test_pattern = relative_str

            normalized_subset_target = test_pattern.replace("\\", "/").rstrip("/")
            if normalized_subset_target == "tests":
                test_pattern = "tests/test_config_loader.py tests/test_token_counter.py"
                logger.info(
                    "L4.tools [tool:execute_tests] - Clone test: running core tool subset"
                )

            logger.debug(
                "L4.tools [tool:execute_tests] - Clone path resolved: root=%s, target=%s",
                clone_root,
                test_pattern,
            )
        except ValueError:
            if not os.path.isabs(test_pattern):
                if not allow_project_root:
                    logger.error(
                        "L4.tools [tool:execute_tests] - Project-root tests are disabled by default (use clones/...)")
                    return (
                        "Project-root tests are disabled by default. Provide a 'clones/...' pattern or set allow_project_root=True.",
                        {"action": "execute_tests", "success": False, "error": "project_root_disabled"},
                    )
                test_pattern = str(project_root / test_pattern)
                logger.debug(
                    "L4.tools [tool:execute_tests] - Resolved to absolute project path: %s",
                    test_pattern,
                )
            else:
                working_directory = Path(test_pattern).resolve(strict=False).parent

        # Build pytest command - prefer project .venv Python, fallback to legacy venv, then system Python
        venv_python_primary = project_root / ".venv" / "bin" / "python"
        venv_python_legacy = project_root / "venv" / "bin" / "python"
        if venv_python_primary.exists():
            python_executable = str(venv_python_primary)
        elif venv_python_legacy.exists():
            python_executable = str(venv_python_legacy)
        else:
            python_executable = sys.executable

        cmd = [python_executable, "-m", "pytest"]

        if verbose:
            cmd.append("-v")
        else:
            cmd.append("-q")

        # Exclude .disabled test files to prevent collection issues
        cmd.extend(["--ignore-glob", "**/test_*.py.disabled", "--tb=short"])
        # Enforce sane defaults
        if maxfail is not None:
            cmd.extend(["--maxfail", str(maxfail)])
        # Only add --timeout when pytest-timeout plugin is available in the selected interpreter
        def _pytest_supports_timeout(pyexe: str) -> bool:
            try:
                probe = subprocess.run(
                    [
                        pyexe,
                        "-c",
                        (
                            "import importlib.util as u;"
                            "print('1' if (u.find_spec('pytest_timeout') or u.find_spec('pytest_timeout.plugin')) else '0')"
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return (probe.stdout or "").strip() == "1"
            except Exception:
                return False

        if timeout is not None and _pytest_supports_timeout(python_executable):
            cmd.extend(["--timeout", str(timeout)])
        else:
            try:
                logger.debug("L4.tools [tool:execute_tests] - pytest-timeout not available; skipping --timeout option")
            except Exception:
                pass
        if durations:
            cmd.extend(["--durations", str(durations)])

        # Apply profile unless user overrides with explicit keywords/markers
        if profile in profiles and not keywords and not markers:
            prof = profiles[profile]
            if prof.get("k"):
                cmd.extend(["-k", prof["k"]])
            if prof.get("m"):
                cmd.extend(["-m", prof["m"]])

        # User filters
        if keywords:
            cmd.extend(["-k", keywords])
        if markers:
            cmd.extend(["-m", markers])
        if last_failed:
            cmd.append("--lf")
        if failed_first:
            cmd.append("--ff")
        if collect_only:
            cmd.append("--collect-only")
        if verbosity:
            cmd.extend(["--verbosity", str(verbosity)])

        # Selection by nodeids/paths in addition to pattern
        selection_added = False
        if nodeids:
            if isinstance(nodeids, str):
                cmd.append(nodeids)
            else:
                cmd.extend(list(nodeids))
            selection_added = True
        if paths:
            if isinstance(paths, str):
                cmd.append(paths)
            else:
                cmd.extend(list(paths))
            selection_added = True

        # Add test pattern(s) - handle multiple patterns separated by space
        if profile == 'slow':
            # Include tests_slow folder for slow profile
            cmd.append('tests_slow')
            selection_added = True

        if not selection_added:
            if " " in test_pattern:
                cmd.extend(test_pattern.split())
            else:
                cmd.append(test_pattern)

        # Optional passthrough args
        if extra_pytest_args:
            if isinstance(extra_pytest_args, str):
                cmd.extend(extra_pytest_args.split())
            else:
                cmd.extend(list(extra_pytest_args))

        logger.debug(
            f"L4.tools [tool:execute_tests] - Command: {' '.join(cmd)}"
        )

        # Overall subprocess timeout safety
        overall_timeout = max(60, min(1200, timeout * 20))

        # Run tests - now runs in thread pool to prevent blocking Discord
        # heartbeat
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=overall_timeout,
                cwd=working_directory,
                env=os.environ.copy(),
            )

            returncode = result.returncode
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            # Parse test results
            test_summary = _parse_pytest_output(stdout)

            if returncode == 0:
                test_message = (
                    f"PASS: All tests passed for {test_pattern}\n\n{stdout}"
                )
                logger.info(
                    f"L4.tools [tool:execute_tests] - All tests passed ({test_summary.get('passed', 0)} tests)"
                )
            else:
                # Detect 'no tests collected' case (pytest exit code 5 or no summary and 'collected 0')
                no_tests_collected = (
                    returncode == 5 or (
                        test_summary.get('passed', 0) == 0 and
                        test_summary.get('failed', 0) == 0 and
                        ('collected 0 items' in stdout or 'no tests ran' in stdout.lower())
                    )
                )
                if no_tests_collected:
                    test_message = (
                        f"NO TESTS: No tests were collected for pattern {test_pattern}.\n\n"
                        f"Command: {' '.join(cmd)}\nWorking dir: {working_directory}\n\n{stdout}"
                    )
                    if stderr:
                        test_message += f"\n\nErrors:\n{stderr}"
                    logger.warning(
                        f"L4.tools [tool:execute_tests] - No tests collected for {test_pattern} (exit {returncode})"
                    )
                else:
                    test_message = (
                        f"FAIL: Test failures found for {test_pattern}\n\n{stdout}"
                    )
                    if stderr:
                        test_message += f"\n\nErrors:\n{stderr}"
                    logger.error(
                        f"L4.tools [tool:execute_tests] - Tests failed (passed: {test_summary.get('passed', 0)}, failed: {test_summary.get('failed', 0)})"
                    )

            return test_message, {
                "action": "execute_tests",
                "success": returncode == 0,
                "test_pattern": test_pattern,
                "return_code": returncode,
                "summary": test_summary,
                "raw_output": stdout,
                "stderr": stderr,
                "command": cmd,
                "working_directory": str(working_directory),
                "no_tests_collected": no_tests_collected if returncode != 0 else False,
                "clone_path": str(clone_root) if clone_root else None,
                "original_pattern": original_pattern,
            }

        except subprocess.TimeoutExpired:
            logger.error(
                f"L4.tools [tool:execute_tests] - Test execution timeout for {test_pattern}"
            )
            return f"Test execution timed out for {test_pattern}", {
                "action": "execute_tests",
                "success": False,
                "error": "Timeout",
            }
        except FileNotFoundError:
            logger.error(f"L4.tools [tool:execute_tests] - Pytest not found")
            return "Pytest not installed or not found", {
                "action": "execute_tests",
                "success": False,
                "error": "Pytest not found",
            }

    except Exception as e:
        logger.error(
            f"L4.tools [tool:execute_tests] - Error running tests {test_pattern}: {e}",
            exc_info=True,
        )
        return f"Error running tests: {str(e)}", {
            "action": "execute_tests",
            "success": False,
            "error": str(e),
        }


# Helper functions


def _analyze_ast(tree: ast.AST, filename: str) -> Dict[str, Any]:
    """Analyze AST and extract structure information."""
    analysis = {"imports": [], "classes": [], "functions": [], "globals": []}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                analysis["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                analysis["imports"].append(
                    f"{module}.{alias.name}" if module else alias.name
                )
        elif isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    methods.append({"name": item.name, "line": item.lineno})
            analysis["classes"].append(
                {"name": node.name, "line": node.lineno, "methods": methods}
            )
        elif (
            isinstance(node, ast.FunctionDef) and node.col_offset == 0
        ):  # Top-level functions only
            analysis["functions"].append(
                {"name": node.name, "line": node.lineno}
            )
        elif (
            isinstance(node, ast.Assign) and node.col_offset == 0
        ):  # Top-level assignments only
            for target in node.targets:
                if isinstance(target, ast.Name):
                    analysis["globals"].append(target.id)

    return analysis


def _parse_flake8_output(output: str) -> List[Dict[str, Any]]:
    """Parse flake8 output into structured issues."""
    issues = []
    for line in output.splitlines():
        if ":" in line:
            parts = line.split(":", 3)
            if len(parts) >= 4:
                issues.append(
                    {
                        "file": parts[0],
                        "line": int(parts[1]) if parts[1].isdigit() else 0,
                        "column": int(parts[2]) if parts[2].isdigit() else 0,
                        "message": parts[3].strip(),
                    }
                )
    return issues


def _parse_pytest_output(output: str) -> Dict[str, Any]:
    """Parse pytest output to extract test statistics."""
    summary = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    # Look for summary line like "1 passed, 2 failed in 0.03s" or
    # "========================= 3 failed, 5 passed, 1 skipped in 2.45s
    # ========================="
    for line in output.splitlines():
        line = line.strip()
        if (
            ("passed" in line or "failed" in line or "skipped" in line)
            and "in " in line
            and ("s ==" in line or line.endswith("s"))
        ):
            # Parse summary line
            parts = line.split()
            for i, part in enumerate(parts):
                if part.isdigit() and i + 1 < len(parts):
                    count = int(part)
                    status = parts[i + 1].rstrip(",")  # Remove trailing comma
                    if status == "passed":
                        summary["passed"] = count
                    elif status == "failed":
                        summary["failed"] = count
                    elif status == "skipped":
                        summary["skipped"] = count
                    elif status == "error" or status == "errors":
                        summary["errors"] = count

    return summary


def list_clones() -> Tuple[str, Dict[str, Any]]:
    """
    List all available code clones with metadata.

    Returns:
        Tuple of (formatted_list, tool_output)
    """
    try:
        project_root = _get_project_root()
        clones_roots = _candidate_clone_roots(project_root)
        
        all_clones = []
        
        for clones_root in clones_roots:
            if clones_root.exists():
                for clone_dir in sorted(clones_root.iterdir(), reverse=True):
                    if clone_dir.is_dir() and clone_dir.name.startswith("clone_"):
                        try:
                            # Get directory stats
                            file_count = sum(1 for _ in clone_dir.rglob("*") if _.is_file())
                            size_bytes = sum(f.stat().st_size for f in clone_dir.rglob("*") if f.is_file())
                            size_mb = size_bytes / (1024 * 1024)
                            mtime = clone_dir.stat().st_mtime
                            
                            all_clones.append({
                                "name": clone_dir.name,
                                "path": f"clones/{clone_dir.name}",
                                "full_path": str(clone_dir),
                                "file_count": file_count,
                                "size_mb": round(size_mb, 2),
                                "modified_time": mtime,
                            })
                        except Exception as e:
                            logger.warning(f"L4.tools [tool:list_clones] - Failed to stat {clone_dir.name}: {e}")
        
        if not all_clones:
            return "No clones found.", {
                "action": "list_clones",
                "success": True,
                "clones": [],
                "count": 0,
            }
        
        # Format output
        output_lines = [
            f"Available Clones ({len(all_clones)} total):",
            "=" * 60,
            ""
        ]
        
        latest = all_clones[0] if all_clones else None
        
        for i, clone in enumerate(all_clones, 1):
            marker = " (LATEST)" if i == 1 else ""
            output_lines.append(f"{i}. {clone['name']}{marker}")
            output_lines.append(f"   Path: {clone['path']}")
            output_lines.append(f"   Files: {clone['file_count']}, Size: {clone['size_mb']} MB")
            output_lines.append("")
        
        if latest:
            output_lines.append(f"Tip: Use 'clones/{latest['name']}/your/file.py' to reference files in the latest clone")
        
        output_text = "\n".join(output_lines)
        
        logger.info(f"L4.tools [tool:list_clones] - Listed {len(all_clones)} clones")
        
        return output_text, {
            "action": "list_clones",
            "success": True,
            "clones": all_clones,
            "count": len(all_clones),
            "latest": latest,
        }
        
    except Exception as e:
        logger.error(f"L4.tools [tool:list_clones] - Error: {e}", exc_info=True)
        return f"Error listing clones: {str(e)}", {
            "action": "list_clones",
            "success": False,
            "error": str(e),
        }


# Export all functions
__all__ = [
    "read_code_structure",
    "run_quick_lint",
    "execute_tests",
    "list_clones",
]
