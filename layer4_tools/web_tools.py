"""
Web-native tool implementations that replace Discord-specific tools.

Maps existing Discord tool names to web app behaviors:
- manually_send_message → append assistant message to room and request UI emit
"""

import base64
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger
from utils.vault_paths import get_vault_root
from utils.attachments import ensure_unique_filename, save_bytes_attachment
from utils.active_room import get_active_room, set_active_room
from utils.rooms import generate_new_room_id
from layer4_tools.agentic_tools import schedule_manual_message
from utils.task_inbox import append_inbox_entry
from utils.presence import emit_presence_notification

logger = get_logger(__name__)


def _rooms_dir() -> str:
    try:
        return os.path.join(str(get_vault_root()), "chats")
    except Exception:
        return os.path.join("vault", "chats")


def _room_file(room_id: str) -> str:
    return os.path.join(_rooms_dir(), f"{room_id}.json")


# Removed room management helpers: create_channel, update_channel, remove_channel, pin_message


def _normalize_candidate_to_vault(raw_path: str) -> Optional[Path]:
    """Normalize a path to the vault directory without requiring it to exist.

    Similar to _coerce_to_vault_file_path but returns a Path object even if the
    file doesn't exist yet. Used for creating new files in the vault.

    Args:
        raw_path: Path string (may be relative or absolute)

    Returns:
        Path object under vault root if valid, None if outside vault
    """
    if not isinstance(raw_path, str):
        return None

    candidate = raw_path.strip()
    if not candidate:
        return None

    try:
        vault_root = Path(get_vault_root())
    except Exception:
        vault_root = Path("vault")

    try:
        vault_root = vault_root if vault_root.is_absolute() else (Path.cwd() / vault_root)
        vault_root = vault_root.resolve(strict=False)
    except Exception:
        vault_root = (Path.cwd() / "vault").resolve(strict=False)

    path_obj = Path(candidate)

    # Try as vault-relative first (most common case for new files)
    if not path_obj.is_absolute():
        try:
            resolved = (vault_root / path_obj).resolve(strict=False)
            # Verify it's still under vault root after resolution
            resolved.relative_to(vault_root)
            return resolved
        except Exception:
            pass

    # Try as absolute or cwd-relative
    try:
        if path_obj.is_absolute():
            resolved = path_obj.resolve(strict=False)
        else:
            resolved = (Path.cwd() / path_obj).resolve(strict=False)
        # Verify it's under vault root
        resolved.relative_to(vault_root)
        return resolved
    except Exception:
        pass

    return None


def _coerce_to_vault_file_path(raw_path: str) -> Optional[str]:
    """Resolve a user-supplied attachment path into the vault sandbox.

    Purpose:
        Normalize relative or absolute paths so attachments always originate inside
        the configured vault root while preventing path traversal outside the sandbox.
    Parameters:
        raw_path: Original path string provided by the caller (may be relative).
    Returns:
        A string containing the absolute path under the vault root when the target
        file exists and is safe to share; otherwise ``None``.
    Example:
        >>> _coerce_to_vault_file_path("daily_plans/sample.md")  # doctest: +SKIP
        '/abs/path/to/vault/daily_plans/sample.md'
    """

    if not isinstance(raw_path, str):
        return None

    candidate = raw_path.strip()
    if not candidate:
        return None

    try:
        vault_root = Path(get_vault_root())
    except Exception:
        vault_root = Path("vault")

    try:
        vault_root = vault_root if vault_root.is_absolute() else (Path.cwd() / vault_root)
        vault_root = vault_root.resolve(strict=False)
    except Exception:
        vault_root = (Path.cwd() / "vault").resolve(strict=False)

    path_obj = Path(candidate)
    probe_paths = []

    # First probe: treat the path as provided (absolute or relative to cwd)
    try:
        probe_paths.append(path_obj if path_obj.is_absolute() else (Path.cwd() / path_obj))
    except Exception:
        pass

    # Second probe: treat the path as vault-relative when not already absolute
    if not path_obj.is_absolute():
        try:
            probe_paths.append(vault_root / path_obj)
        except Exception:
            pass

    seen: set[str] = set()
    for probe in probe_paths:
        try:
            resolved = Path(probe).resolve(strict=False)
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved.relative_to(vault_root)
        except Exception:
            continue
        if resolved.is_file():
            return key

    return None


def _build_default_file_message(file_names: List[str]) -> str:
    """Generate a user-facing sentence describing the shared files."""
    safe_names = ["file"] * len(file_names)
    for idx, name in enumerate(file_names):
        cleaned = (name or "").strip("`\" ")
        safe_names[idx] = cleaned or "file"

    if not safe_names:
        return "Sharing file."

    if len(safe_names) == 1:
        return f"Sharing `{safe_names[0]}`."

    preview = ", ".join(f"`{n}`" for n in safe_names[:3])
    if len(safe_names) > 3:
        preview += f" + {len(safe_names) - 3} more"
    return f"Sharing {len(safe_names)} files: {preview}"


def _resolve_target_room(channel_id: Optional[str], run_id: Optional[str] = None) -> Tuple[str, bool]:
    """Determine which room to target for a manual message.

    For task executions, ALWAYS uses the task's dedicated room.
    Otherwise prefers explicit channel_id, then active room, then generates new.
    """
    if isinstance(channel_id, str) and channel_id.strip():
        return channel_id.strip(), False
    
    # CRITICAL: Check if this is a task execution
    # Use logging context (which is properly set during task execution) instead of
    # trying to look up the run from RunService (which may be a different instance
    # in the worker process vs web process, causing lookup failures)
    if isinstance(run_id, str) and run_id.strip():
        try:
            from utils.logger import get_current_room_id as _get_ctx_room
            
            # Try to get room from logging context first (most reliable)
            ctx_room = _get_ctx_room()
            if isinstance(ctx_room, str) and ctx_room.strip() and ctx_room != "-":
                # Verify this is a task room (starts with 'task')
                if ctx_room.startswith("task"):
                    logger.debug(f"L4.web_tools [resolve_room] - Using task room from context: {ctx_room}")
                    return ctx_room, False
            
            # Fallback: Try RunService lookup (may fail in worker process)
            from server.deps import get_service as _get_service
            service = _get_service()
            run_obj = service.get_run(run_id) if service else None
            if run_obj is not None:
                task_id = getattr(run_obj, "task_id", None)
                if task_id:
                    # This is a task - use the run's room (task room)
                    task_room = getattr(run_obj, "room_id", None)
                    if isinstance(task_room, str) and task_room:
                        logger.debug(f"L4.web_tools [resolve_room] - Using task room from run object: {task_room}")
                        return task_room, False
        except Exception as err:
            logger.debug(f"L4.web_tools [resolve_room] - task context lookup failed: {err}")
    
    # Non-task flow: use active room as before
    try:
        active = get_active_room()
    except Exception:
        active = None
    if isinstance(active, str) and active:
        return active, False
    try:
        new_room = generate_new_room_id()
        if isinstance(new_room, str) and new_room:
            logger.debug("L4.web_tools [resolve_room] - no active room, created %s", new_room)
            return new_room, True
    except Exception as err:
        logger.debug("L4.web_tools [resolve_room] - failed to generate new room: %s", err)
    return "web", False


async def manually_send_message(
    text: str,
    channel_id: Optional[str] = None,
    delivery: str = "default",
    schedule: Optional[Dict[str, Any]] = None,
    embed: bool = False,
    run_id: Optional[str] = None,
    # New optional arguments to support sending files
    file_path: Optional[str] = None,
    file_paths: Optional[list[str]] = None,
    file_data_base64: Optional[str] = None,
    filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    display_inline: bool = False,
    *,
    prebuilt_attachments: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Send or schedule a manual message.

    Args:
        text: Message body.
        channel_id: Target room (defaults to active room if omitted).
        delivery: "default" for room delivery, "inbox" to also surface in the Tasks Inbox feed.
        schedule: Optional dict with start_time and recurrence for future delivery.
    """
    try:
        delivery_mode = str((delivery or "default")).lower().strip()
        if delivery_mode not in ("default", "inbox"):
            delivery_mode = "default"

        linked_task_id: Optional[str] = None
        if isinstance(run_id, str) and run_id.strip():
            try:
                from server.deps import get_service as _get_service

                service = _get_service()
                run_obj = service.get_run(run_id) if service else None
                if run_obj is not None:
                    linked_task_id = getattr(run_obj, "task_id", None)
                    task_delivery = getattr(run_obj, "task_delivery_mode", "room_only")
                    is_silent_task = bool(getattr(run_obj, "task_is_silent", False))
                    if (
                        delivery_mode == "default"
                        and linked_task_id
                        and not is_silent_task
                        and task_delivery == "room_and_inbox"
                    ):
                        delivery_mode = "inbox"
            except Exception as run_lookup_err:
                logger.debug(
                    f"L4.web_tools [send_message] - run context lookup failed: {run_lookup_err}"
                )

        room, created_new_room = _resolve_target_room(channel_id, run_id=run_id)

        base_text = str(text or "").strip()
        attachments: List[Dict[str, Any]] = []
        md_fragments: List[str] = []

        processed_attachments = prebuilt_attachments if isinstance(prebuilt_attachments, list) else None
        uploads_root: Optional[str] = None

        if processed_attachments is not None:
            attachments = [dict(item) for item in processed_attachments if isinstance(item, dict)]
        else:
            try:
                uploads_root = os.path.join(str(get_vault_root()), "uploads", room)
            except Exception:
                uploads_root = os.path.join("vault", "uploads", room)
            os.makedirs(uploads_root, exist_ok=True)

            files_to_process: list[Dict[str, Any]] = []
            try:
                if isinstance(file_paths, list) and file_paths:
                    for p in file_paths:
                        if isinstance(p, str) and p.strip():
                            files_to_process.append({"mode": "path", "path": p.strip()})
                elif isinstance(file_path, str) and file_path.strip():
                    files_to_process.append({"mode": "path", "path": file_path.strip()})
            except Exception:
                pass

            if isinstance(file_data_base64, str) and file_data_base64:
                files_to_process.append({
                    "mode": "b64",
                    "data": file_data_base64,
                    "filename": (filename or "file.bin"),
                    "mime": mime_type or "application/octet-stream",
                })

            for spec in files_to_process:
                try:
                    dest_name = None
                    src_desc = None
                    original_name = None
                    if spec.get("mode") == "path":
                        raw_path = str(spec.get("path") or "").strip()
                        if not raw_path:
                            continue
                        src_path = _coerce_to_vault_file_path(raw_path)
                        if not src_path:
                            logger.warning(
                                "L4.web_tools [send_message] - Blocked non-vault file send: %s",
                                raw_path,
                            )
                            continue
                        if not os.path.exists(src_path) or not os.path.isfile(src_path):
                            continue
                        base = os.path.basename(src_path)
                        dest_path, dest_name = ensure_unique_filename(uploads_root, base)
                        with open(src_path, 'rb') as rf, open(dest_path, 'wb') as wf:
                            wf.write(rf.read())
                        src_desc = dest_path
                        original_name = base
                    elif spec.get("mode") == "b64":
                        base = str(spec.get("filename") or "file.bin").strip() or "file.bin"
                        import base64 as _b64
                        try:
                            data_bytes = _b64.b64decode(str(spec.get("data") or ""), validate=False)
                        except Exception:
                            data_bytes = b""
                        if len(data_bytes) > 10 * 1024 * 1024:
                            logger.warning("L4.web_tools [send_message] - File too large (>10MB), skipping")
                            continue
                        att = save_bytes_attachment(room, base, data_bytes, mime_type)
                        src_desc = os.path.join(uploads_root, os.path.basename(att.get("url", "/").split("/")[-1]))
                        dest_name = os.path.basename(src_desc)
                        original_name = base
                    else:
                        continue

                    if not src_desc or not os.path.exists(src_desc):
                        continue

                    try:
                        size = os.path.getsize(src_desc)
                    except Exception:
                        size = 0
                    rel_url = f"/uploads/{room}/{dest_name}"
                    attachments.append({
                        "filename": original_name or dest_name,
                        "url": rel_url,
                        "size": int(size or 0),
                        "content_type": str(mime_type or ""),
                    })

                    _, ext = os.path.splitext(dest_name.lower())
                    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                        md_fragments.append(f"![]({rel_url})")
                    else:
                        # Always show just a download link - never inline file contents
                        # Users can click to view the file
                        md_fragments.append(f"[Download {dest_name}]({rel_url})")
                except Exception as fe:
                    logger.debug(f"L4.web_tools [send_message] - Attachment processing failed: {fe}")

            try:
                from utils.attachments import normalize_attachments as _norm_atts
                attachments = _norm_atts(attachments)
            except Exception:
                pass

        if processed_attachments is not None:
            final_text = base_text if base_text else ""
        else:
            final_text = base_text
            if md_fragments:
                attach_block = "\n\n" + "\n\n".join(md_fragments)
                final_text = (final_text + attach_block).strip()
            if not final_text:
                final_text = "(sent attachment)" if attachments else ""

        if schedule is not None:
            if not isinstance(schedule, dict):
                return "ERROR: schedule must be an object with 'start_time'", {
                    "success": False,
                    "error": "invalid_schedule",
                }
            start_value = schedule.get("start_time")
            if not start_value:
                return "ERROR: schedule.start_time is required", {
                    "success": False,
                    "error": "missing_start_time",
                }
            recurrence_value = schedule.get("recurrence")
            try:
                scheduled = schedule_manual_message(
                    final_text or "(sent attachment)",
                    delivery=delivery_mode,
                    start_time=str(start_value),
                    recurrence=str(recurrence_value) if recurrence_value else None,
                    channel_id=room,
                    attachments=attachments,
                    original_text=str(text or ""),
                )
            except ValueError as exc:
                return f"ERROR: {exc}", {
                    "success": False,
                    "error": str(exc),
                }
            return (
                f"Scheduled message for {scheduled['start_time']}",
                {
                    "success": True,
                    "scheduled": True,
                    "message_id": scheduled.get("id"),
                    "delivery": scheduled.get("delivery"),
                    "room_id": scheduled.get("channel_id") or room,
                    "recurrence": scheduled.get("recurrence"),
                },
            )

        os.makedirs(_rooms_dir(), exist_ok=True)
        path = _room_file(room)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        with open(path, "r", encoding="utf-8") as f:
            msgs = json.load(f)
        mid = str(uuid.uuid4())
        ts = time.time()

        try:
            from utils.attachments import normalize_attachments as _norm_atts
            attachments = _norm_atts(attachments)
        except Exception:
            pass

        entry = {
            "role": "assistant",
            "content": final_text or str(text or ""),
            "message_id": mid,
            "timestamp": ts,
            "reactions": {},
        }
        # Persist attachments on the message so history streams can render them immediately
        try:
            if attachments:
                entry["attachments"] = attachments
        except Exception:
            pass
        if isinstance(run_id, str) and run_id:
            entry["run_id"] = run_id
        msgs.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(msgs, f, indent=2, ensure_ascii=False)
        if created_new_room and room not in {None, ""}:
            try:
                set_active_room(room)
            except Exception as set_err:
                logger.debug(f"L4.web_tools [send_message] - set_active_room failed for {room}: {set_err}")
        logger.info(f"L4.web_tools [send_message] - room={room} mid={mid} len={len(final_text or '')}")
        # Request the orchestrator to emit this immediately for a live feel
        if delivery_mode == "inbox":
            try:
                append_inbox_entry(
                    source="manual",
                    room_id=room,
                    message_id=mid,
                    text=final_text,
                    task_id=linked_task_id,
                )
            except Exception as inbox_err:
                logger.debug(f"L4.web_tools [send_message] - inbox append failed: {inbox_err}")
        target_context = "tasks_inbox" if delivery_mode == "inbox" else f"room:{room}"
        try:
            emit_presence_notification(target_context, final_text)
        except Exception as notify_err:
            logger.debug(f"L4.web_tools [send_message] - presence emit skipped: {notify_err}")
        
        # Build a clear confirmation message for tool output
        confirmation_msg = "Message sent"
        if attachments:
            if len(attachments) == 1:
                attachment_name = attachments[0].get("filename", "file")
                confirmation_msg = f"File \"{attachment_name}\" sent successfully to user"
            else:
                confirmation_msg = f"{len(attachments)} files sent successfully to user"
        
        return (
            confirmation_msg,
            {
                "success": True,
                "action": "web.message",
                "text": final_text,
                "room": room,
                "message_id": mid,
                "run_id": run_id,
                "attachments": attachments,
                "delivery": delivery_mode,
                "task_id": linked_task_id,
            },
        )
    except Exception as e:
        logger.error(f"L4.web_tools [send_message] - {e}")
        return (f"Error sending message: {e}", {"success": False, "error": str(e)})


async def send_file(
    *,
    path: str,
    room_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Send a single existing vault file to the user.
    
    Args:
        path: Vault-relative or absolute path to the file to send
        room_id: Optional room ID (defaults to the active room)
    
    Returns:
        Tuple of (message, result_dict) where result_dict contains success status
    """
    try:
        # Validate and normalize the path
        if not path or not isinstance(path, str) or not path.strip():
            return (
                "Error: File path is required.",
                {"success": False, "error": "missing_path"},
            )
        
        # Resolve to vault path
        resolved_path = _coerce_to_vault_file_path(path.strip())
        if not resolved_path:
            logger.warning(
                "L4.web_tools [send_file] - Blocked non-vault file send: %s",
                path,
            )
            return (
                f"Error: Path '{path}' is not a valid vault file path.",
                {"success": False, "error": "artifact_missing_source"},
            )
        
        # Check if file exists
        if not os.path.exists(resolved_path) or not os.path.isfile(resolved_path):
            return (
                f"Error: File '{path}' not found in vault.",
                {"success": False, "error": "artifact_missing_source"},
            )
        
        # Get filename for display
        try:
            filename = os.path.basename(resolved_path)
        except Exception:
            filename = "file"
        
        # Resolve room ID explicitly to ensure we know where it's going
        room, _ = _resolve_target_room(room_id)
        
        # Build message
        message_text = _build_default_file_message([filename])
        
        # Send the file
        result_msg, result_data = await manually_send_message(
            message_text,
            channel_id=room,  # Use resolved room
            file_paths=[resolved_path],
        )
        
        # Check if attachment was created
        attachments_sent = result_data.get("attachments", [])
        if not attachments_sent:
            return (
                f"Error: Failed to attach file '{filename}'.",
                {"success": False, "error": "attachment_upload_failed"},
            )
        
        # Return success
        success = bool(result_data.get("success"))
        if success:
            return (
                f"File '{filename}' has been sent to room '{room}' successfully.",
                {
                    "success": True,
                    "file": filename,
                    "path": resolved_path,
                    "room_id": room,
                    "attachments": result_data.get("attachments"),
                    "text": message_text,
                },
            )
        else:
            error_msg = result_data.get("error", "Unknown error")
            return (
                f"Error: Failed to send file '{filename}': {error_msg}",
                {"success": False, "error": error_msg},
            )
    
    except Exception as exc:
        logger.error(f"L4.web_tools [send_file] - {exc}", exc_info=True)
        return (
            f"Error sending file: {exc}",
            {"success": False, "error": str(exc)},
        )


async def append_hidden_message(text: str, channel_id: Optional[str] = None, role: str = "system", run_id: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Append a hidden message to a room's chat history without emitting to the user interface.

    The message will be stored with a "hidden": true flag so the web API can suppress it
    from user-visible history while it remains available for Theo's prompt/memory context.

    Args:
        text: Message content to store
        channel_id: Target room id (defaults to "web")
        role: Role to store (defaults to system)
        run_id: Optional run identifier linking this hidden message to a specific user run.
            When a past user message is edited, any hidden entries with matching run_id
            are removed along with messages from that run.
    """
    try:
        room, created_new_room = _resolve_target_room(channel_id)
        os.makedirs(_rooms_dir(), exist_ok=True)
        path = _room_file(room)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        with open(path, "r", encoding="utf-8") as f:
            msgs = json.load(f)
        mid = str(uuid.uuid4())
        ts = time.time()
        entry = {
            "role": role,
            "content": str(text or ""),
            "message_id": mid,
            "timestamp": ts,
            "reactions": {},
            "hidden": True,
        }
        if isinstance(run_id, str) and run_id:
            entry["run_id"] = run_id
        msgs.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(msgs, f, indent=2, ensure_ascii=False)
        logger.info(f"L4.web_tools [append_hidden] - room={room} mid={mid} len={len(text or '')}")
        return (
            "Hidden message appended",
            {"success": True, "action": "web.hidden", "room": room, "message_id": mid, "run_id": run_id},
        )
    except Exception as e:
        logger.error(f"L4.web_tools [append_hidden] - {e}")
        return (f"Error appending hidden message: {e}", {"success": False, "error": str(e)})
# Utility for tool result persistence with deduplication per specs
from typing import Optional


def append_tool_result_with_dedup(text: str, room_id: str, *, run_id: Optional[str] = None) -> None:
    """Append a hidden tool result to the room, replacing earliest duplicate with a notice.

    Behavior (spec-compliant):
    - If an identical "Tool Result: ..." entry already exists (hidden/system), replace the earliest
      occurrence with a deduplication notice and still append the current full result.
    - All tool results are stored with hidden=true and role=system so they are suppressed from
      user-visible history but available in Theo's context.
    """
    try:
        room = room_id or "web"
        os.makedirs(_rooms_dir(), exist_ok=True)
        path = _room_file(room)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        with open(path, "r", encoding="utf-8") as f:
            msgs = json.load(f) or []
        # Find earliest duplicate
        duplicate_index = -1
        for idx, m in enumerate(msgs):
            try:
                if m.get("hidden") and m.get("role") == "system" and str(m.get("content")) == str(text):
                    duplicate_index = idx
                    break
            except Exception:
                continue
        # If duplicate found, don't append again
        if duplicate_index >= 0:
            logger.debug(f"L4.web_tools [dedup] - Skipped duplicate tool result (already in chat history)")
            return
        # Append current result
        from uuid import uuid4
        entry = {
            "role": "system",
            "content": str(text or ""),
            "message_id": str(uuid4()),
            "timestamp": time.time(),
            "hidden": True,
        }
        if isinstance(run_id, str) and run_id:
            entry["run_id"] = run_id
        msgs.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(msgs, f, indent=2, ensure_ascii=False)
        logger.debug(
            f"L4.web_tools [tool_persist] - Stored tool result (len={len(text or '')}) for room={room}"
        )
    except Exception as e:
        logger.debug(f"L4.web_tools [tool_persist] - Failed to store tool result: {e}")
