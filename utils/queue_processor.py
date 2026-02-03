"""
Queue Processor for Web App Integration

This module integrates the message queue system with the web app to provide
reliable delivery of task triggers, scheduled manual messages, and UI events.

Features:
- Periodic queue processing (every 5 seconds)
- Task vs manual message handling
- Error tracking and retry logic
- Integration with web tools
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from utils.logger import get_logger

try:  # pragma: no cover - helper may be unavailable in minimal test environments
    from server.routers.runs import _maybe_title_room as _auto_title_room  # type: ignore
except Exception:  # pragma: no cover - degrade gracefully when the router is missing
    _auto_title_room = None  # type: ignore
from utils.message_queue import get_message_queue

logger = get_logger(__name__)

# Ensure an event loop exists in main thread for synchronous test contexts
try:
    asyncio.get_event_loop()
except RuntimeError:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except Exception:
        pass

# Global bot instance for task execution
_global_bot_instance = None
_INLINE_LOOP_DEBUG = False  # toggled in tests if needed


def _resolve_bot_instance(candidate=None):
    """Best-effort resolver for the RunService/bot dependency.

    Accepts an explicit candidate, then falls back to the cached global. As a
    final fallback it attempts to import the shared RunService from
    ``server.deps``. This keeps task delivery functioning even if the worker
    process is not running (e.g., development mode where only the web server is
    launched).
    """

    global _global_bot_instance
    if candidate is not None:
        _global_bot_instance = candidate
        return candidate
    if _global_bot_instance is not None:
        return _global_bot_instance
    try:
        from server.deps import get_service  # type: ignore

        svc = get_service()
        if svc is not None:
            _global_bot_instance = svc
            return svc
    except Exception as e:
        logger.debug(f"L6.queue_processor [resolve_bot] - Service lookup skipped: {e}")
    return None

class QueueProcessor:
    """Processes message queue for web delivery."""
    
    def __init__(self, bot_instance=None):
        # In web mode, bot_instance is the RunService (provides chat_history and create_run)
        self.bot = _resolve_bot_instance(bot_instance)
        self.queue = get_message_queue()
        self.processing = False
        self.process_task = None
        
        # Store bot instance globally for task execution
        global _global_bot_instance
        _global_bot_instance = self.bot

    def set_bot(self, bot_instance) -> None:
        """Update the bound bot/run-service if one was not available earlier."""
        resolved = _resolve_bot_instance(bot_instance)
        if resolved is not None:
            self.bot = resolved
            global _global_bot_instance
            _global_bot_instance = resolved
        
    def start_processing(self, interval: float = 5.0):
        """
        Start periodic queue processing.
        
        Args:
            interval: Processing interval in seconds (default: 5 seconds)
        """
        if self.process_task and not self.process_task.done():
            logger.warning("L6.queue_processor [start] - Processing already running")
            return
            
        try:
            loop = asyncio.get_event_loop()
            self.process_task = loop.create_task(self._process_loop(interval))
        except RuntimeError:
            # No running loop; create one for background processing
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.process_task = loop.create_task(self._process_loop(interval))
        logger.info(f"L6.queue_processor [start] - Started queue processing (interval: {interval}s)")
    
    async def stop_processing(self):
        """Stop queue processing gracefully."""
        if self.process_task and not self.process_task.done():
            self.process_task.cancel()
            try:
                await self.process_task
            except asyncio.CancelledError:
                pass
            logger.info("L6.queue_processor [stop] - Stopped queue processing")
    
    async def _process_loop(self, interval: float):
        """Main processing loop."""
        while True:
            try:
                await self.process_pending_messages()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("L6.queue_processor [loop] - Processing loop cancelled")
                break
            except Exception as e:
                logger.error(f"L6.queue_processor [loop] - Error in processing loop: {e}")
                await asyncio.sleep(interval)  # Continue despite errors
    
    async def process_pending_messages(self, batch_size: int = 5):
        """
        Process pending messages from the queue.
        
        Args:
            batch_size: Maximum messages to process in one batch
        """
        if self.processing:
            return  # Avoid concurrent processing
            
        try:
            self.processing = True
            pending_messages = self.queue.get_pending_messages(batch_size)
            
            if not pending_messages:
                return  # No messages to process
                
            logger.debug(f"L6.queue_processor [process] - Processing {len(pending_messages)} messages")
            
            for message in pending_messages:
                await self._process_single_message(message)
                
        except Exception as e:
            logger.error(f"L6.queue_processor [process] - Error processing messages: {e}")
        finally:
            self.processing = False
    
    async def _process_single_message(self, message: Dict[str, Any]):
        """
        Process a single message from the queue.
        
        Args:
            message: Message dictionary from queue
        """
        message_id = message.get("id", "unknown")
        message_type = message.get("type", "unknown")
        content = message.get("content", "")
        metadata = message.get("metadata", {})
        
        try:
            logger.debug(f"L6.queue_processor [message] - Processing {message_type} message {message_id}")
            
            if message_type == "manual":
                success = await self._send_manual_message(content, metadata)
            elif message_type == "task":
                success = await self._send_task_message(content, metadata)
            elif message_type == "ui":
                success = await self._send_ui_action(content, metadata)
            else:
                logger.warning(f"L6.queue_processor [message] - Unknown message type: {message_type}")
                success = False
            
            if success:
                self.queue.mark_message_processed(message_id)
                logger.debug(f"L6.queue_processor [message] - Successfully processed {message_type} message {message_id}")
            else:
                self.queue.mark_message_failed(message_id, f"Failed to send {message_type} message")
                logger.warning(f"L6.queue_processor [message] - Failed to process {message_type} message {message_id}")
                
        except Exception as e:
            error_msg = f"Error processing {message_type} message: {str(e)}"
            self.queue.mark_message_failed(message_id, error_msg)
            logger.error(f"L6.queue_processor [message] - {error_msg}")
    
    async def _send_manual_message(self, content: str, metadata: Dict[str, Any]) -> bool:
        """Deliver a manual message via manually_send_message."""
        try:
            from layer4_tools.web_tools import manually_send_message

            delivery = str(metadata.get("delivery") or "default").lower()
            channel_id = metadata.get("channel_id") if isinstance(metadata, dict) else None
            attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
            original_text = metadata.get("original_text") if isinstance(metadata, dict) else None
            payload_text = content if isinstance(content, str) and content else (original_text or "")
            response_text, payload = await manually_send_message(
                payload_text,
                channel_id=channel_id,
                delivery=delivery,
                prebuilt_attachments=attachments if isinstance(attachments, list) else None,
            )
            return bool(isinstance(payload, dict) and payload.get("success"))
        except Exception as exc:
            logger.error(f"L6.queue_processor [manual] - Error sending manual message: {exc}")
            return False

    async def _send_task_message(self, content: str, metadata: Dict[str, Any]) -> bool:
        """Create a Run for the scheduled task so Theo processes it like a user message.

        - Chooses a target room (most recent active, or 'web')
        - Honors silent flag by creating a hidden run (no visible chat append)
        - Lets the normal orchestration pipeline handle DCO and execution
        - Implements error recovery with exponential backoff on failures
        """
        try:
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            task_id = metadata_dict.get("task_id", "unknown")
            is_silent = bool(metadata_dict.get("silent", False))
            retry_count = metadata_dict.get("retry_count", 0)
            logger.info(
                "L6.queue_processor [task] - Processing task %s (silent=%s, delivery=%s, retry=%d)",
                task_id,
                is_silent,
                metadata_dict.get("delivery_mode", "unknown"),
                retry_count
            )

            service = self.bot
            if service is None:
                logger.error("L6.queue_processor [task] - No RunService bound; cannot create run")
                return False

            # Build candidate rooms: explicit metadata (if meaningful) → active room → new room → placeholders
            placeholder_rooms = {"web", "default"}
            candidates: List[str] = []
            placeholder_queue: List[str] = []
            seen: set[str] = set()

            def _queue_candidate(room: Optional[str], allow_placeholder: bool = False) -> None:
                if not isinstance(room, str):
                    return
                rid = room.strip()
                if not rid or rid in seen:
                    return
                lowered = rid.lower()
                # Task rooms (starting with 'task') are ALWAYS valid - never treat as placeholder
                # This ensures tasks stay in their designated rooms even if the file doesn't exist yet
                is_task_room = rid.startswith("task")
                if lowered in placeholder_rooms and not allow_placeholder and not is_task_room:
                    # Store placeholders to try after primary fallbacks
                    logger.debug(f"L6.queue_processor [task] - Room '{rid}' queued as placeholder (not primary candidate)")
                    placeholder_queue.append(rid)
                    return
                seen.add(rid)
                candidates.append(rid)
                if is_task_room:
                    logger.debug(f"L6.queue_processor [task] - Queued task room '{rid}' as valid candidate")

            try:
                explicit_room = metadata_dict.get("room_id")
            except Exception:
                explicit_room = None
            
            # Track if explicit room was actually queued as a valid candidate
            explicit_room_queued = False
            if explicit_room:
                # Save current candidates count to check if room was added
                before_count = len(candidates)
                _queue_candidate(explicit_room)
                explicit_room_queued = len(candidates) > before_count
                
                # Log warning if task's explicit room failed to queue
                if not explicit_room_queued:
                    logger.warning(
                        "L6.queue_processor [task] - Task %s explicit room '%s' failed to queue (likely placeholder/duplicate)",
                        task_id,
                        explicit_room
                    )

            # CRITICAL: Tasks MUST NOT fall back to active_room
            # This was causing tasks to execute in whatever room the user was viewing,
            # leading to content bleeding between unrelated conversations.
            # Instead, if explicit room fails, we create a NEW dedicated room for the task.
            # This ensures task isolation and prevents cross-contamination.

            # Always attempt a brand-new room before resorting to placeholders
            candidates.append("__new__room__")

            # Ensure we eventually attempt placeholders (e.g., legacy 'web')
            for placeholder_room in placeholder_queue:
                if placeholder_room not in seen:
                    seen.add(placeholder_room)
                    candidates.append(placeholder_room)

            if "web" not in seen:
                # Final guard so we always have some room to fall back to
                seen.add("web")
                candidates.append("web")

            try:
                from utils.rooms import generate_new_room_id
            except Exception as rid_err:
                generate_new_room_id = None  # type: ignore[assignment]
                logger.debug(f"L6.queue_processor [task] - generate_new_room_id unavailable: {rid_err}")

            final_run = None
            final_room: Optional[str] = None
            attempted: List[str] = []
            last_error: Optional[str] = None

            for candidate in candidates:
                resolved_room = candidate
                if candidate == "__new__room__":
                    if generate_new_room_id is None:
                        last_error = "generate_new_room_id unavailable"
                        logger.debug("L6.queue_processor [task] - Skipping new-room fallback (generator missing)")
                        continue
                    try:
                        resolved_room = generate_new_room_id(prefix="task")
                    except Exception as e:
                        last_error = str(e)
                        logger.debug(f"L6.queue_processor [task] - New room id generation failed: {e}")
                        continue
                if not resolved_room:
                    continue

                attempted.append(resolved_room)
                try:
                    # Wrap task content in a system directive to ensure execution
                    # This prevents the model from interpreting the task description as a user request to create a task
                    task_prompt = (
                        f"SYSTEM INSTRUCTION: Execute the following scheduled task immediately.\n"
                        f"Do not treat this as a conversation or a request to schedule a task.\n"
                        f"Perfrom the actions described in the task content below.\n\n"
                        f"TASK CONTENT:\n{content}"
                    )
                    
                    run = service.create_run(
                        resolved_room,
                        task_prompt,
                        attachments=None,
                        hidden=is_silent,
                        suppress_user_message=True,
                    )
                    try:
                        setattr(run, "task_id", metadata_dict.get("task_id"))
                        setattr(run, "task_delivery_mode", metadata_dict.get("delivery_mode", "room_only"))
                        setattr(run, "task_trigger_content", content)
                        setattr(run, "task_is_silent", is_silent)
                    except Exception:
                        pass
                except Exception as ce:
                    last_error = str(ce)
                    logger.warning(
                        "L6.queue_processor [task] - Run creation failed for room %s: %s",
                        resolved_room,
                        ce,
                    )
                    continue

                final_run = run
                final_room = getattr(run, "room_id", None) or resolved_room
                try:
                    setattr(run, "task_room_hint", final_room)
                except Exception:
                    pass
                
                # VALIDATION: Ensure task resolved to a task room (not a standard room)
                # This prevents task content from bleeding into user conversation rooms
                if final_room and not final_room.startswith("task"):
                    logger.warning(
                        "L6.queue_processor [task] - Task %s resolved to NON-TASK room '%s' (expected task* pattern). "
                        "This may cause content bleeding. Explicit room was: %s",
                        task_id,
                        final_room,
                        explicit_room
                    )
                
                logger.info(
                    "L6.queue_processor [task] - Task %s resolved to room '%s' (is_task_room=%s)",
                    task_id,
                    final_room,
                    final_room.startswith("task") if final_room else False
                )
                break

            if final_run is None or not final_room:
                logger.error(
                    "L6.queue_processor [task] - Could not create run (attempted=%s, last_error=%s)",
                    attempted or candidates,
                    last_error,
                )
                return False

            # IMPORTANT: Per product spec, the message used to trigger a task
            # should NEVER appear in the chat UI. This applies to both silent and
            # non-silent tasks. Therefore we always suppress the user message
            # append (suppress_user_message=True). Visibility of the assistant
            # output is controlled exclusively by the `hidden` flag:
            # - hidden=True  (silent tasks): do not stream to UI; still persist
            #                  assistant message to history via run flag below.
            # - hidden=False (non-silent): stream assistant output to UI and
            #                  persist as usual.

            # IMPORTANT: In silent mode, persist the assistant reply even though the run is hidden
            try:
                if is_silent:
                    setattr(final_run, "persist_assistant_even_if_hidden", True)
            except Exception:
                pass

            # For non-silent tasks, hint the UI to attach to this run stream so
            # users see tokens as they arrive. This does NOT reveal the trigger
            # message itself (we suppressed the user append above).
            try:
                if not is_silent:
                    from utils.forms_store import enqueue_ui_event as _enqueue_form_evt
                    _enqueue_form_evt(
                        {
                            "action": "start_run",
                            "run_id": getattr(final_run, "id", None),
                            "room": final_room,
                        }
                    )
            except Exception as se:
                logger.debug(f"L6.queue_processor [task] - start_run enqueue skipped: {se}")

            # Surface a brief status in logs; streaming handled by RunService
            try:
                run_id = getattr(final_run, "id", None)
            except Exception:
                run_id = None
            logger.info(
                "L6.queue_processor [task] - Created run %s in room '%s' (hidden=%s)",
                run_id,
                final_room,
                is_silent,
            )

            if _auto_title_room and isinstance(final_room, str):
                try:
                    await _auto_title_room(final_room, content)
                except Exception as exc:
                    logger.debug(
                        "L6.queue_processor [task] - Auto-title skipped for room %s: %s",
                        final_room,
                        exc,
                    )
            
            timeout_seconds = self._resolve_task_execution_timeout()
            self._schedule_task_monitor(final_run, task_id, retry_count, timeout_seconds)
            return True

        except Exception as e:
            logger.error(f"L6.queue_processor [task] - Error processing task: {e}", exc_info=True)
            # Schedule retry for processing errors too
            try:
                from layer4_tools.agentic_tools import _schedule_task_retry
                task_id_fallback = metadata.get("task_id") if isinstance(metadata, dict) else "unknown"
                retry_count_fallback = metadata.get("retry_count", 0) if isinstance(metadata, dict) else 0
                _schedule_task_retry(task_id_fallback, str(e), retry_count_fallback + 1)
            except Exception as retry_exc:
                logger.error(f"L6.queue_processor [task] - Failed to schedule retry: {retry_exc}")
            return False

    def _resolve_task_execution_timeout(self) -> int:
        """Determine the maximum amount of time a scheduled task run should take."""
        default_timeout = 3600
        service = getattr(self, "bot", None)
        if service is not None:
            try:
                cfg = getattr(service, "config", {}) or {}
                candidate = cfg.get("tasks", {}).get("execution_timeout_seconds")
                if candidate:
                    candidate_int = int(candidate)
                    if candidate_int > 0:
                        return candidate_int
            except Exception:
                pass
        try:
            from utils.config_loader import load_config

            cfg = load_config()
            candidate = cfg.get("tasks", {}).get("execution_timeout_seconds")
            if candidate:
                candidate_int = int(candidate)
                if candidate_int > 0:
                    return candidate_int
        except Exception:
            pass
        return default_timeout

    def _schedule_task_monitor(self, run, task_id: str, retry_count: int, timeout_seconds: int) -> None:
        """Schedule background monitoring for a run without blocking queue processing."""
        def _on_complete(task: "asyncio.Task") -> None:
            try:
                task.result()
            except asyncio.CancelledError:
                logger.debug(f"L6.queue_processor [monitor] - Monitor cancelled for task {task_id}")
            except Exception as mon_err:
                logger.error(
                    f"L6.queue_processor [monitor] - Monitor error for task {task_id}: {mon_err}",
                    exc_info=True,
                )

        try:
            monitor = asyncio.create_task(
                self._monitor_task_run(run, task_id, retry_count, timeout_seconds),
                name=f"task-monitor-{task_id}",
            )
            monitor.add_done_callback(_on_complete)
            return
        except RuntimeError:
            loop = getattr(self.bot, "loop", None)
            if loop and loop.is_running() and not loop.is_closed():
                def _schedule_on_loop():
                    task = loop.create_task(
                        self._monitor_task_run(run, task_id, retry_count, timeout_seconds),
                        name=f"task-monitor-{task_id}",
                    )
                    task.add_done_callback(_on_complete)
                try:
                    loop.call_soon_threadsafe(_schedule_on_loop)
                except Exception as loop_err:
                    logger.error(
                        f"L6.queue_processor [monitor] - Failed to schedule monitor on RunService loop: {loop_err}",
                        exc_info=True,
                    )
            else:
                logger.error("L6.queue_processor [monitor] - No active event loop available to monitor task execution")

    async def _monitor_task_run(self, run, task_id: str, retry_count: int, timeout_seconds: int) -> None:
        """Monitor a run associated with a scheduled task and enqueue retries when needed."""
        run_id = getattr(run, "id", "unknown")
        room_id = getattr(run, "room_id", "unknown")

        try:
            await asyncio.sleep(0.5)
        except Exception:
            pass

        run_task = getattr(run, "task", None)
        if not run_task:
            logger.info(
                "L6.queue_processor [monitor] - Task %s (run %s) has no asyncio task; treating as success",
                task_id,
                run_id,
            )
            return

        next_retry = retry_count + 1
        try:
            await asyncio.wait_for(run_task, timeout=timeout_seconds)
            logger.info(
                "L6.queue_processor [monitor] - Task %s (run %s, room %s) completed successfully",
                task_id,
                run_id,
                room_id,
            )
        except asyncio.TimeoutError:
            error_msg = f"Task execution timed out after {timeout_seconds} seconds"
            logger.warning(
                "L6.queue_processor [monitor] - Task %s (run %s) timed out: %s",
                task_id,
                run_id,
                error_msg,
            )
            self._schedule_task_retry(task_id, error_msg, next_retry)
        except asyncio.CancelledError:
            error_msg = "Task execution was cancelled"
            logger.warning(
                "L6.queue_processor [monitor] - Task %s (run %s) cancelled",
                task_id,
                run_id,
            )
            self._schedule_task_retry(task_id, error_msg, next_retry)
        except Exception as run_exc:
            error_msg = str(run_exc)
            logger.error(
                "L6.queue_processor [monitor] - Task %s (run %s) failed: %s",
                task_id,
                run_id,
                error_msg,
                exc_info=True,
            )
            self._schedule_task_retry(task_id, error_msg, next_retry)

    def _schedule_task_retry(self, task_id: str, error_message: str, retry_count: int) -> None:
        """Schedule a retry with logging guards."""
        try:
            from layer4_tools.agentic_tools import _schedule_task_retry as _internal_schedule_retry

            next_time = _internal_schedule_retry(task_id, error_message, retry_count)
            if next_time:
                logger.info(
                    "L6.queue_processor [monitor] - Scheduled retry %d for task %s at %s",
                    retry_count,
                    task_id,
                    next_time.isoformat(),
                )
        except Exception as retry_exc:
            logger.error(
                f"L6.queue_processor [monitor] - Failed to schedule retry for task {task_id}: {retry_exc}",
                exc_info=True,
            )

    async def _send_ui_action(self, content: str, metadata: Dict[str, Any]) -> bool:
        """Deliver a UI action by appending to ui_events JSONL for SSE streaming."""
        try:
            from utils.forms_store import enqueue_ui_event
            # content is a JSON string produced by enqueue_ui_action; parse to dict
            import json
            try:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    payload = {"action": "unknown"}
            except Exception:
                payload = {"action": "unknown"}
            ok, _ = enqueue_ui_event(payload)
            return ok
        except Exception as e:
            logger.error(f"L6.queue_processor [ui] - Error delivering UI action: {e}")
            return False

    
    def get_processor_stats(self) -> Dict[str, Any]:
        """Get processor and queue statistics."""
        queue_stats = self.queue.get_queue_stats()
        return {
            **queue_stats,
            "processing": self.processing,
            "processor_running": self.process_task and not self.process_task.done() if self.process_task else False
        }

# Global processor instance
_queue_processor = None

def get_queue_processor(bot_instance=None) -> QueueProcessor:
    """Get the global queue processor instance."""
    global _queue_processor
    if _queue_processor is None:
        _queue_processor = QueueProcessor(bot_instance)
    elif bot_instance is not None:
        _queue_processor.set_bot(bot_instance)
    elif _queue_processor.bot is None:
        _queue_processor.set_bot(None)
    return _queue_processor

def start_queue_processing(bot_instance=None, interval: float = 5.0):
    """Start queue processing."""
    processor = get_queue_processor(bot_instance)
    processor.start_processing(interval)
    logger.info("L6.queue_processor [global] - Started global queue processing")

async def stop_queue_processing():
    """Stop queue processing."""
    global _queue_processor
    if _queue_processor:
        await _queue_processor.stop_processing()
        logger.info("L6.queue_processor [global] - Stopped global queue processing")


def process_queue_inline(bot_instance=None, limit: int = 5) -> None:
    """Process pending messages synchronously in the current thread.

    Used as a safety net when the dedicated worker process is not running. This
    keeps scheduled tasks/notifications functional in single-process
    deployments (e.g., local dev) by draining the queue immediately.
    """

    processor = get_queue_processor(bot_instance)
    if processor.bot is None:
        processor.set_bot(bot_instance)

    async def _runner():
        try:
            await processor.process_pending_messages(batch_size=limit)
        except Exception as exc:
            logger.error(f"L6.queue_processor [inline] - Error draining queue: {exc}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        target_loop: Optional[asyncio.AbstractEventLoop] = None
        bot = processor.bot
        if bot is not None:
            try:
                target_loop = getattr(bot, "loop", None)
                if target_loop and target_loop.is_closed():
                    target_loop = None
            except Exception:
                target_loop = None

        if target_loop and target_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(_runner(), target_loop)
                future.result()
            except Exception as exc:
                logger.error(f"L6.queue_processor [inline] - Error draining queue on bound loop: {exc}")
            return

        # Fallback: run via a temporary loop and wait for spawned tasks to finish
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_runner())
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception as exc:
            logger.error(f"L6.queue_processor [inline] - Error draining queue via fallback loop: {exc}")
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    else:
        loop.create_task(_runner())
