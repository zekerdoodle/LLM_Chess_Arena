"""
Tool Execution Utilities

This module handles the execution of Theo's tools through native function calling.
It provides the execute_tool_call function that routes tool calls to their implementations.
"""

import asyncio
import functools
import json
import traceback
from typing import Any, Dict, Optional
import inspect

from utils.logger import get_logger, log_tool_execution, log_tool_result

logger = get_logger(__name__)


def _sanitize_string_arg(value: Any) -> Any:
    """Strip extraneous quotes from model-generated strings.
    
    Some models (especially xAI/Grok) wrap string arguments in extra quotes during
    JSON serialization, causing strings like "\"form_id\"" instead of "form_id".
    This function detects and strips one layer of matching quotes when present.
    
    Args:
        value: Any value (typically a string from tool arguments)
        
    Returns:
        The value with outermost matching quotes removed if present, otherwise unchanged
    """
    if not isinstance(value, str):
        return value
    
    # Only strip if the string has both leading and trailing quotes of the same type
    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"):
            stripped = value[1:-1]
            logger.debug(f"Tool executor: Sanitized string arg '{value}' -> '{stripped}'")
            return stripped
    
    return value


def _sanitize_tool_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively sanitize tool arguments to strip extraneous quotes.
    
    Args:
        arguments: Dictionary of tool arguments from the model
        
    Returns:
        Sanitized arguments dictionary with quotes stripped from strings
    """
    if not isinstance(arguments, dict):
        return arguments
    
    sanitized = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            sanitized[key] = _sanitize_string_arg(value)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_tool_arguments(value)
        elif isinstance(value, list):
            sanitized[key] = [
                _sanitize_string_arg(item) if isinstance(item, str)
                else _sanitize_tool_arguments(item) if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            sanitized[key] = value
    
    return sanitized


# Import all tool functions
from layer4_tools.agentic_tools import (
    create_task,
    list_task,
    update_task,
)
from layer4_tools.coding_tools import (
    execute_tests,
    read_code_structure,
    run_quick_lint,
    list_clones,
)
from utils.diff_utils import create_diff
from layer4_tools.working_memory_tools import (
    add_working_memory,
    remove_working_memory,
    snapshot_working_memory,
    update_working_memory,
)
# Web-native replacements for former Discord tools
from layer4_tools.web_tools import (
    manually_send_message,
    send_file,
)
# File tools removed; replaced by 'bash' tool.
from layer4_tools.info_tools import (
    add_memory,
    update_memory,
    delete_memory,
    list_rooms,
)
from layer4_tools.self_patch_tools import (
    apply_patch,
    create_clone,
    preview_patch,
    rollback_on_failure,
)
from layer4_tools.web_search_tools import (
    web_search,
)
from layer4_tools.deep_research_tools import (
    url_retrieval,
    page_parser,
)
from layer4_tools.code_agent import (
    code_agent,
)
from layer4_tools.image_tools import (
    generate_image,
)
from layer4_tools.image_analysis_tools import (
    analyze_image,
)
from layer4_tools.bash_tool import (
    bash as bash_tool,
)
from layer4_tools.forms_tools import (
    define_form,
    show_form,
    save_form_submission,
    list_form_submissions,
)
from layer4_tools.financial_tools import (
    connect_bank_account,
    get_financial_accounts,
    get_transactions,
    get_spending_analysis,
    disconnect_bank_account,
    get_connection_status,
)


# Tool Registry - Maps tool names to functions
TOOL_REGISTRY = {
    # Working memory tools
    "add_working_memory": add_working_memory,
    "remove_working_memory": remove_working_memory,
    "update_working_memory": update_working_memory,
    "snapshot_working_memory": snapshot_working_memory,
    # File handling tools removed (migrated to 'bash')
    # Web replacements for Discord tools (subset only)
    "manually_send_message": manually_send_message,
    "send_file": send_file,
    # Info tools
    "list_rooms": list_rooms,
    # Coding tools
    "read_code_structure": read_code_structure,
    "run_quick_lint": run_quick_lint,
    "execute_tests": execute_tests,
    "list_clones": list_clones,
    "create_diff": create_diff,
    # Agentic tools
    "create_task": create_task,
    "update_task": update_task,
    "list_task": list_task,
    # Analysis tool removed per spec (use 'bash' instead)
    # Web search tools
    "web_search": web_search,
    # Deep research tools
    "url_retrieval": url_retrieval,
    "page_parser": page_parser,
    # Self-patching tools
    "create_clone": create_clone,
    "preview_patch": preview_patch,
    "apply_patch": apply_patch,
    "rollback_on_failure": rollback_on_failure,
    # Info tools
    "add_memory": add_memory,
    "update_memory": update_memory,
    "delete_memory": delete_memory,
    # Code agent tools
    "code_agent": code_agent,
    # Image generation
    "generate_image": generate_image,
    # Image analysis
    "analyze_image": analyze_image,
    # Shell tool (vault-sandboxed) — now explicitly Bash
    "bash": bash_tool,
    # Forms
    "define_form": define_form,
    "show_form": show_form,
    "save_form_submission": save_form_submission,
    "list_form_submissions": list_form_submissions,
    # Financial tools
    "connect_bank_account": connect_bank_account,
    "get_financial_accounts": get_financial_accounts,
    "get_transactions": get_transactions,
    "get_spending_analysis": get_spending_analysis,
    "disconnect_bank_account": disconnect_bank_account,
    "get_connection_status": get_connection_status,

    # Alias for live review
}

# Legacy Discord-only tool names are no longer exposed in web mode


async def execute_tool_call(
    tool_call: Dict[str, Any],
    model_call_func,
    prompt: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a tool call with explicit parameter mapping (like original main.py).

    Args:
        tool_call: Dictionary containing tool call information
        model_call_func: Function to call the model
        prompt: Optional prompt for context
        context: Conversation metadata (e.g., conversation_id, topic_id)

    Returns:
        Dictionary containing tool execution result
    """
    try:
        # Extract tool call information
        tool_name = tool_call.get("name")
        raw_arguments = tool_call.get("arguments", {})
        tool_id = tool_call.get("id")

        if not tool_name:
            return {
                "success": False,
                "error": "No tool name provided",
                "tool_id": tool_id,
            }

        # Sanitize arguments to strip extraneous quotes from model outputs
        # (especially xAI/Grok which wraps strings in extra quotes)
        arguments = _sanitize_tool_arguments(raw_arguments)
        
        # Log tool execution with sanitized arguments
        log_tool_execution(logger, tool_name, arguments)

        # Check if tool exists in registry
        if tool_name not in TOOL_REGISTRY:
            error_msg = f"Tool '{tool_name}' not found in registry"
            logger.error(f"Tool execution failed: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "tool_id": tool_id,
            }

        # Get tool function
        tool_func = TOOL_REGISTRY[tool_name]

        # Execute tool function with EXPLICIT parameter mapping (like original main.py)
        try:
            result_text = None
            tool_output = {}
            
            # Handle different tool signatures with explicit parameter mapping
            if tool_name == "add_working_memory":
                result_text, tool_output = tool_func(
                    content=arguments.get("content", ""),
                    tag=arguments.get("tag"),
                    ttl_exchanges=arguments.get("ttl_exchanges"),
                    context=context,
                )
            elif tool_name == "remove_working_memory":
                result_text, tool_output = tool_func(
                    index=arguments.get("index"),
                    wm_version=arguments.get("wm_version"),
                    context=context,
                )
            elif tool_name == "update_working_memory":
                result_text, tool_output = tool_func(
                    index=arguments.get("index"),
                    content=arguments.get("content"),
                    append=arguments.get("append"),
                    ttl_exchanges=arguments.get("ttl_exchanges"),
                    tag=arguments.get("tag"),
                    wm_version=arguments.get("wm_version"),
                    context=context,
                )
            elif tool_name == "snapshot_working_memory":
                result_text, tool_output = tool_func(
                    importance=arguments.get("importance", 0),
                    scope=arguments.get("scope", "visible"),
                    tag=arguments.get("tag"),
                    indices=arguments.get("indices"),
                    title=arguments.get("title"),
                    context=context,
                )
            # Coding tools
            elif tool_name == "read_code_structure":
                file_path = arguments.get("file_path", "")
                result_text, tool_output = tool_func(file_path)
            elif tool_name == "run_quick_lint":
                file_path = arguments.get("file_path", "")
                result_text, tool_output = tool_func(file_path)
            elif tool_name == "list_clones":
                result_text, tool_output = tool_func()
            elif tool_name == "execute_tests":
                call_kwargs = {
                    "test_pattern": arguments.get("test_pattern", "tests/"),
                    "verbose": arguments.get("verbose", False),
                    "profile": arguments.get("profile", "smoke"),
                    "keywords": arguments.get("keywords", ""),
                    "markers": arguments.get("markers", ""),
                    "nodeids": arguments.get("nodeids"),
                    "paths": arguments.get("paths"),
                    "maxfail": arguments.get("maxfail", 1),
                    "timeout": arguments.get("timeout", 25),
                    "durations": arguments.get("durations", 10),
                    "last_failed": arguments.get("last_failed", False),
                    "failed_first": arguments.get("failed_first", False),
                    "collect_only": arguments.get("collect_only", False),
                    "verbosity": arguments.get("verbosity", 0),
                    "extra_pytest_args": arguments.get("extra_pytest_args"),
                    "include_heavy": arguments.get("include_heavy", False),
                    "include_network": arguments.get("include_network", False),
                    "list_profiles": arguments.get("list_profiles", False),
                    "allow_project_root": arguments.get("allow_project_root", False),
                }
                # Run tests in thread pool to avoid blocking event loop
                import concurrent.futures

                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = loop.run_in_executor(
                        executor,
                        functools.partial(tool_func, **call_kwargs),
                    )
                    result_text, tool_output = await future
            elif tool_name == "create_diff":
                file_path = arguments.get("file_path", "")
                proposed_changes = arguments.get("proposed_changes", "")
                result_text, tool_output = tool_func(file_path, proposed_changes)
            # removed granular edit helpers (use update_file)
            elif tool_name == "generate_image":
                prompt = arguments.get("prompt", "")
                # New preferred flag; keep dont_send for back-compat
                auto_send = arguments.get("auto_send")
                dont_send = arguments.get("dont_send", False)
                steps = arguments.get("steps")
                cfg_scale = arguments.get("cfg_scale")
                width = arguments.get("width")
                height = arguments.get("height")
                negative_prompt = arguments.get("negative_prompt")
                seed = arguments.get("seed")
                timeout_seconds = arguments.get("timeout_seconds")
                # Run blocking HTTP call in thread executor
                import concurrent.futures
                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = loop.run_in_executor(
                        executor,
                        tool_func,
                        prompt,
                        dont_send,
                        steps,
                        cfg_scale,
                        width,
                        height,
                        negative_prompt,
                        seed,
                        timeout_seconds,
                        auto_send,
                    )
                    result_text, tool_output = await future
            # Forms
            elif tool_name == "define_form":
                form = arguments.get("form", {})
                result_text, tool_output = tool_func(form)
            elif tool_name == "show_form":
                form_id = arguments.get("form_id", "")
                prefill = arguments.get("prefill")
                room_id = arguments.get("room_id")
                result_text, tool_output = tool_func(form_id, prefill, room_id)
            elif tool_name == "save_form_submission":
                form_id = arguments.get("form_id", "")
                answers = arguments.get("answers", {})
                room_id = arguments.get("room_id")
                mock_mode = arguments.get("mock_mode", False)
                result_text, tool_output = tool_func(form_id, answers, room_id, mock_mode)
            elif tool_name == "list_form_submissions":
                form_id = arguments.get("form_id")
                limit = arguments.get("limit", 20)
                after_ts = arguments.get("after_ts")
                room_id = arguments.get("room_id")
                result_text, tool_output = tool_func(form_id, limit, after_ts, room_id)

            elif tool_name == "see_form_updates":
                # Alias for listing new submissions since a timestamp with optional room filter
                form_id = arguments.get("form_id")
                room_id = arguments.get("room_id")
                after_ts = arguments.get("after_ts")
                limit = arguments.get("limit", 20)
                # Reuse list_form_submissions implementation
                result_text, tool_output = list_form_submissions(form_id, limit, after_ts, room_id)
            # Code agent tools
            elif tool_name == "code_agent":
                agent_prompt = arguments.get("prompt", "")
                clone_path = arguments.get("clone_path", "")
                timeout_seconds = arguments.get("timeout_seconds", 300)
                # Run in thread executor to avoid blocking event loop
                import concurrent.futures
                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = loop.run_in_executor(executor, tool_func, agent_prompt, clone_path, timeout_seconds)
                    result_text, tool_output = await future
            elif tool_name == "sh":
                # Normalize timeout to seconds (schema may pass ms)
                raw_to = arguments.get("timeout")
                try:
                    timeout_sec = int(raw_to) if raw_to is not None else None
                    if timeout_sec and timeout_sec > 1000:
                        timeout_sec = timeout_sec // 1000
                except Exception:
                    timeout_sec = None
                cmd = arguments.get("command", "")
                stdin_data = arguments.get("stdin")
                env_vars = arguments.get("env")
                # Run blocking shell tool in a thread pool
                import concurrent.futures
                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = loop.run_in_executor(executor, bash_tool, cmd, timeout_sec, stdin_data, env_vars)
                    result_text, tool_output = await future
            # Deep research tools
            elif tool_name == "url_retrieval":
                query = arguments.get("query", "")
                max_results = arguments.get("max_results", 10)
                site = arguments.get("site")
                time_range = arguments.get("time_range")
                include_snippets = arguments.get("include_snippets", True)
                result_text = tool_func(query, max_results=max_results, site=site, time_range=time_range, include_snippets=include_snippets)
                tool_output = {}
            elif tool_name == "page_parser":
                url = arguments.get("url")
                urls = arguments.get("urls")
                save = arguments.get("save", False)
                max_tokens = arguments.get("max_tokens", 8192)
                fmt = arguments.get("format", "markdown")
                use_cache = arguments.get("use_cache", True)
                force = arguments.get("force", False)
                target = urls if (isinstance(urls, list) and len(urls) > 0) else (url or "")
                result_text = tool_func(target, save=save, max_tokens=max_tokens, format=fmt, use_cache=use_cache, force=force)
                tool_output = {}
            # Web room tools: Discord-era channel management is not supported in web mode
            elif tool_name == "manually_send_message":
                text = arguments.get("text", "")
                # room_id preferred; fallback to legacy channel_id for compatibility
                room_id = arguments.get("room_id") or arguments.get("channel_id")
                embed = arguments.get("embed", False)
                # CRITICAL: Use run_id from context if not provided in arguments
                # This ensures task messages are sent to the correct room
                run_id = arguments.get("run_id") or (context.get("run_id") if context else None)
                file_path = arguments.get("file_path")
                file_paths = arguments.get("file_paths")
                file_data_base64 = arguments.get("file_data_base64")
                filename = arguments.get("filename")
                mime_type = arguments.get("mime_type")
                display_inline = arguments.get("display_inline", False)
                delivery = str(arguments.get("delivery") or "default").strip().lower()
                if delivery not in ("default", "inbox"):
                    delivery = "default"
                raw_schedule = arguments.get("schedule")
                schedule = raw_schedule if isinstance(raw_schedule, dict) else None

                result_text, tool_output = await tool_func(
                    text,
                    channel_id=room_id,
                    delivery=delivery,
                    schedule=schedule,
                    embed=embed,
                    run_id=run_id,
                    file_path=file_path,
                    file_paths=file_paths,
                    file_data_base64=file_data_base64,
                    filename=filename,
                    mime_type=mime_type,
                    display_inline=display_inline,
                )
            elif tool_name == "send_file":
                path = arguments.get("path") or arguments.get("file_path")
                # Default room_id to the channel from context if not provided
                room_id = arguments.get("room_id")
                if not room_id and context:
                    room_id = context.get("channel_id") or context.get("room_id")
                
                result_text, tool_output = await tool_func(
                    path=path,
                    room_id=room_id,
                )
            # Agentic tools
            elif tool_name == "create_task":
                name = arguments.get("name", "")
                details = arguments.get("details", "")
                start_time = arguments.get("start_time", "")
                recurrence = arguments.get("recurrence")
                silent = arguments.get("silent", False)
                room_id = arguments.get("room_id")
                call_result = tool_func(name, details, start_time, recurrence, silent, room_id)
                if inspect.isawaitable(call_result):
                    call_result = await call_result
                if isinstance(call_result, tuple) and len(call_result) == 2:
                    result_text, tool_output = call_result
                else:
                    result_text, tool_output = str(call_result), {}
            elif tool_name == "update_task":
                task_id = arguments.get("task_id", "")
                # Extract all fields except task_id for **fields
                fields = {k: v for k, v in arguments.items() if k != "task_id"}
                call_result = tool_func(task_id, **fields)
                if inspect.isawaitable(call_result):
                    call_result = await call_result
                if isinstance(call_result, tuple) and len(call_result) == 2:
                    result_text, tool_output = call_result
                else:
                    result_text, tool_output = str(call_result), {}
            elif tool_name == "list_task":
                task_id = arguments.get("task_id")
                status = arguments.get("status")
                include_recent_completed = arguments.get("include_recent_completed", False)
                recent_completed_limit = arguments.get("recent_completed_limit", 10)
                call_result = tool_func(
                    task_id=task_id,
                    status=status,
                    include_recent_completed=include_recent_completed,
                    recent_completed_limit=recent_completed_limit
                )
                if inspect.isawaitable(call_result):
                    call_result = await call_result
                if isinstance(call_result, tuple) and len(call_result) == 2:
                    result_text, tool_output = call_result
                else:
                    result_text, tool_output = str(call_result), {}
            # Analysis tool removed
            # Web search tools (Perplexity-powered with filtering parameters)
            elif tool_name == "web_search":
                result_text = await tool_func(
                    query=arguments.get("query"),
                    queries=arguments.get("queries"),
                    max_results=arguments.get("max_results"),
                    recency=arguments.get("recency"),
                    country=arguments.get("country"),
                    domains=arguments.get("domains"),
                    languages=arguments.get("languages"),
                )
                tool_output = {"success": True}
            # No-op: other tools handled above
            # Self-patching tools
            # removed: create_backup tool
            elif tool_name == "create_clone":
                # Run clone in thread executor to prevent blocking event loop
                logger.debug("L4.tools [execution] - Running create_clone in thread executor")
                clone_result = await asyncio.get_event_loop().run_in_executor(None, tool_func)
                result_text = f"Clone creation completed: {'Success' if clone_result['success'] else 'Failed'}\n"
                if clone_result["success"]:
                    result_text += f"✓ Clone created: {clone_result['clone_path']} ({clone_result['files_copied']} files)\n"
                else:
                    result_text += f"✗ Clone failed: {clone_result.get('error', 'Unknown error')}\n"
                tool_output = clone_result
            elif tool_name == "preview_patch":
                clone_path = arguments.get("clone_path", "")
                max_diffs = arguments.get("max_diffs", 200)
                include_deletions = arguments.get("include_deletions", True)
                extensions = arguments.get("extensions")
                preview = tool_func(clone_path, max_diffs, include_deletions, extensions)
                if preview.get("success"):
                    cnt = preview.get("changes_count", 0)
                    result_text = f"Preview patch: {cnt} change(s) detected\n"
                    listed = 0
                    for ch in preview.get("changes", [])[:10]:
                        result_text += f"- {ch.get('status','?')}: {ch.get('path')}\n"
                        listed += 1
                    if cnt > listed:
                        result_text += f"... and {cnt - listed} more\n"
                else:
                    result_text = f"Preview patch failed: {preview.get('error','unknown error')}\n"
                tool_output = preview
            elif tool_name == "apply_patch":
                clone_path = arguments.get("clone_path", "")
                # Optional flags: default to visible continuation (silent=False)
                silent_continuation = arguments.get("silent_continuation", None)
                target_room = arguments.get("target_room", None)
                try:
                    if silent_continuation is None and target_room is None:
                        patch_result = tool_func(clone_path)
                    elif target_room is None:
                        patch_result = tool_func(clone_path, silent_continuation)
                    else:
                        patch_result = tool_func(clone_path, silent_continuation, target_room)
                except TypeError:
                    # Back-compat: older apply_patch signature (clone_path only)
                    patch_result = tool_func(clone_path)
                # Note: If we reach this point, it means backup failed (otherwise system would reboot)
                result_text = f"Patch application: {'Success' if patch_result['success'] else 'Failed'}\n"
                if not patch_result["success"]:
                    result_text += f"✗ Error: {patch_result.get('error', 'Unknown error')}\n"
                    result_text += f"✗ Backup created: {'Yes' if patch_result.get('backup_created', False) else 'No'}\n"
                else:
                    result_text += "✓ System will reboot to apply changes\n"
                    result_text += "System did not reboot - patch was not applied due to errors.\n"
                tool_output = patch_result
            elif tool_name == "rollback_on_failure":
                max_retries = arguments.get("max_retries", 3)
                # Run rollback in thread executor to prevent blocking event loop
                logger.debug("L4.tools [execution] - Running rollback_on_failure in thread executor")
                rollback_result = await asyncio.get_event_loop().run_in_executor(None, tool_func, max_retries)
                result_text = f"Rollback operation completed: {'Success' if rollback_result['success'] else 'Failed'}\n"
                if rollback_result["success"]:
                    action = rollback_result.get('action', 'unknown')
                    if action == "reset_to_pre_patch":
                        result_text += f"✓ Reset to pre-patch commit: {rollback_result.get('commit', 'unknown')[:8]}\n"
                    elif action == "iterative_commit_rollback":
                        result_text += f"✓ Rolled back to working commit: {rollback_result.get('commit', 'unknown')[:8]} (attempts: {rollback_result.get('attempts', 'unknown')})\n"
                    elif action == "remote_pull_rollback":
                        result_text += "✓ Pulled from remote repository\n"
                    result_text += f"✓ Vault preserved: {'Yes' if rollback_result.get('vault_preserved', False) else 'No'}\n"
                else:
                    result_text += f"✗ Rollback failed: {rollback_result.get('error', 'Unknown error')}\n"
                tool_output = rollback_result
            # Info tools
            elif tool_name == "add_memory":
                memory_string = arguments.get("memory_string", "")
                importance = arguments.get("importance", 50)
                tool_result = tool_func(memory_string, importance)
                result_text = tool_result.get("message", "Memory operation completed")
                tool_output = tool_result
            elif tool_name == "update_memory":
                memory_id = arguments.get("memory_id", "")
                memory_string = arguments.get("memory_string")
                importance = arguments.get("importance")
                tool_result = tool_func(memory_id, memory_string, importance)
                result_text = tool_result.get("message", "Memory update completed")
                tool_output = tool_result
            elif tool_name == "delete_memory":
                memory_id = arguments.get("memory_id", "")
                tool_result = tool_func(memory_id)
                result_text = tool_result.get("message", "Memory deletion completed")
                tool_output = tool_result
            elif tool_name == "list_rooms":
                room_id = arguments.get("room_id")
                result_text, tool_output = tool_func(room_id)
            elif tool_name in ("create_clone", "create_backup"):
                # Ignore any provided arguments and call with no params
                result = tool_func()
                if isinstance(result, tuple) and len(result) == 2:
                    result_text, tool_output = result
                else:
                    result_text, tool_output = str(result), {}
            elif tool_func == "special_handler":
                # This should not be reached because explicit branch must handle it
                result_text = f"ERROR: Tool '{tool_name}' marked special but not handled"
                tool_output = {"success": False, "error": "Internal routing error"}
            else:
                # Generic fallback: route unknown tools using registry signature
                try:
                    if isinstance(arguments, dict):
                        call_result = tool_func(**arguments)
                    else:
                        call_result = tool_func(arguments)
                    if inspect.isawaitable(call_result):
                        call_result = await call_result
                    if isinstance(call_result, tuple) and len(call_result) == 2:
                        result_text, tool_output = call_result
                    else:
                        result_text, tool_output = str(call_result), {}
                except TypeError:
                    # Fallback to positional call if kwargs mapping fails
                    call_result = tool_func(arguments)
                    if inspect.isawaitable(call_result):
                        call_result = await call_result
                    if isinstance(call_result, tuple) and len(call_result) == 2:
                        result_text, tool_output = call_result
                    else:
                        result_text, tool_output = str(call_result), {}

            # Derive success flag from tool_output when available
            success_flag = True
            try:
                if isinstance(tool_output, dict) and "success" in tool_output:
                    success_flag = bool(tool_output.get("success"))
            except Exception:
                success_flag = True

            # Log execution outcome
            if success_flag:
                logger.info(f"L4.tools [tool:{tool_name}] - Tool executed successfully")
            else:
                logger.warning(f"L4.tools [tool:{tool_name}] - Tool reported failure")
            logger.debug(f"L4.tools [tool:{tool_name}] - Result: {str(result_text)[:100]}...")
            
            # Log tool result with actual file paths for file operations
            log_tool_result(logger, tool_name, success_flag, result_text, tool_output)
            
            # Derive a concise top-level error for orchestrator display on failures
            top_error = None
            if not success_flag:
                try:
                    if isinstance(tool_output, dict) and tool_output.get("error"):
                        top_error = str(tool_output.get("error"))
                    else:
                        # Fallback to first line of the textual result
                        first_line = (str(result_text) or "").splitlines()[0:1]
                        if first_line:
                            top_error = first_line[0][:200]
                except Exception:
                    pass

            # Filter out _ui_only metadata before returning (UI metadata shouldn't reach LLM)
            llm_tool_data = {}
            ui_only_data = {}
            if isinstance(tool_output, dict):
                for key, value in tool_output.items():
                    if key == "_ui_only":
                        ui_only_data = value
                    else:
                        llm_tool_data[key] = value
            else:
                llm_tool_data = tool_output
            
            # Return in the format expected by tool orchestrator
            return {
                "success": success_flag,
                "output": result_text,
                "tool_data": llm_tool_data,  # LLM-safe metadata only
                "ui_data": ui_only_data,     # UI-specific metadata (for web app)
                "tool_id": tool_id,
                "tool_name": tool_name,
                **({"error": top_error} if (not success_flag and top_error) else {}),
            }

        except Exception as tool_error:
            error_msg = f"Tool execution error: {str(tool_error)}"
            logger.error(f"Tool '{tool_name}' execution failed: {error_msg}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Log tool failure
            log_tool_result(logger, tool_name, False, error_msg)
            
            return {
                "success": False,
                "error": error_msg,
                "tool_id": tool_id,
                "tool_name": tool_name,
            }

    except Exception as e:
        error_msg = f"Tool call processing error: {str(e)}"
        logger.error(f"Tool call processing failed: {error_msg}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Log processing failure
        tool_name_for_log = tool_call.get("name", "unknown")
        log_tool_result(logger, tool_name_for_log, False, error_msg)
        
        return {
            "success": False,
            "error": error_msg,
            "tool_id": tool_call.get("id"),
        } 
