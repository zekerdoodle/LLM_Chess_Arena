"""
Message Queue System for Theo web deliveries.

Provides a persistent queue for scheduled manual messages, UI actions, and
task triggers.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from utils.logger import get_logger
from utils.vault_paths import get_vault_root
from utils.atomic_file_ops import load_json, save_json

logger = get_logger(__name__)

# Queue file path (under vault root)
try:
    QUEUE_FILE = Path(get_vault_root()) / "message_queue.json"
except Exception:
    # Fallback to preferred default
    QUEUE_FILE = Path("vault/message_queue.json")

class MessageQueue:
    """Thread-safe message queue for web delivery."""
    
    def __init__(self, queue_file: Path = QUEUE_FILE):
        self.queue_file = queue_file
        self._ensure_queue_file()
    
    def _ensure_queue_file(self):
        """Ensure queue file exists with proper structure."""
        try:
            if not self.queue_file.exists():
                self.queue_file.parent.mkdir(parents=True, exist_ok=True)
                initial_data = {
                    "messages": [],
                    "processed": [],
                    "failed": []
                }
                self._save_queue_data(initial_data)
                logger.debug("L6.queue [init] - Created new message queue file")
        except Exception as e:
            logger.error(f"L6.queue [init] - Failed to initialize queue file: {e}")
    
    def _load_queue_data(self) -> Dict[str, Any]:
        """Load queue data from file with atomic operations and error handling."""
        default_data = {"messages": [], "processed": [], "failed": []}
        data = load_json(self.queue_file, default_data)
        
        # Ensure all required sections exist
        if "messages" not in data:
            data["messages"] = []
        if "processed" not in data:
            data["processed"] = []
        if "failed" not in data:
            data["failed"] = []
            
        return data
    
    def _save_queue_data(self, data: Dict[str, Any]) -> bool:
        """Save queue data to file with atomic operations and locking."""
        return save_json(self.queue_file, data)
    
    def enqueue_message(self, message_type: str, content: str,
                       metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Add a message to the queue for web delivery.

        Args:
            message_type: Type of message ("manual", "task", "ui")
            content: Message content to send
            metadata: Optional metadata (source_id, silent flag, etc.)

        Returns:
            Message ID for tracking
        """
        try:
            data = self._load_queue_data()
            
            message_id = str(uuid.uuid4())[:8]
            message = {
                "id": message_id,
                "type": message_type,
                "content": content,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
                "retry_count": 0,
                "status": "pending"
            }
            
            data["messages"].append(message)
            
            if self._save_queue_data(data):
                logger.debug(f"L6.queue [enqueue] - Added {message_type} message {message_id}")
                return message_id
            else:
                logger.error(f"L6.queue [enqueue] - Failed to save {message_type} message")
                return ""
                
        except Exception as e:
            logger.error(f"L6.queue [enqueue] - Error adding message: {e}")
            return ""
    
    def get_pending_messages(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get pending messages from queue for processing.
        
        Args:
            limit: Maximum number of messages to retrieve
            
        Returns:
            List of pending messages
        """
        try:
            data = self._load_queue_data()
            pending = [msg for msg in data["messages"] if msg.get("status") == "pending"]
            return pending[:limit]
        except Exception as e:
            logger.error(f"L6.queue [get_pending] - Error retrieving messages: {e}")
            return []
    
    def mark_message_processed(self, message_id: str) -> bool:
        """
        Mark a message as successfully processed.
        
        Args:
            message_id: ID of the message to mark as processed
            
        Returns:
            True if successful, False otherwise
        """
        try:
            data = self._load_queue_data()
            
            # Find the message to process
            message_to_process = None
            for msg in data["messages"]:
                if msg["id"] == message_id and msg.get("status", "pending") == "pending":
                    message_to_process = msg.copy()
                    break
            
            if message_to_process:
                # Remove from pending messages
                data["messages"] = [msg for msg in data["messages"] 
                                  if not (msg["id"] == message_id and msg.get("status", "pending") == "pending")]
                
                # Add to processed list
                message_to_process["status"] = "processed"
                message_to_process["processed_at"] = datetime.now().isoformat()
                data["processed"].append(message_to_process)
                
                # Keep only last 100 processed messages
                data["processed"] = data["processed"][-100:]
                
                if self._save_queue_data(data):
                    logger.debug(f"L6.queue [processed] - Marked message {message_id} as processed")
                    return True
            else:
                logger.warning(f"L6.queue [processed] - Message {message_id} not found or not pending")
            
            return False
            
        except Exception as e:
            logger.error(f"L6.queue [processed] - Error marking message processed: {e}")
            return False
    
    def mark_message_failed(self, message_id: str, error: str, 
                           max_retries: int = 3) -> bool:
        """
        Mark a message as failed, with retry logic.
        
        Args:
            message_id: ID of the message that failed
            error: Error description
            max_retries: Maximum retry attempts
            
        Returns:
            True if message should be retried, False if permanently failed
        """
        try:
            data = self._load_queue_data()
            
            # Find the message
            for msg in data["messages"]:
                if msg["id"] == message_id:
                    msg["retry_count"] = msg.get("retry_count", 0) + 1
                    msg["last_error"] = error
                    msg["last_attempt"] = datetime.now().isoformat()
                    
                    if msg["retry_count"] >= max_retries:
                        # Move to failed permanently
                        msg["status"] = "failed"
                        data["failed"].append(msg.copy())
                        data["messages"] = [m for m in data["messages"] if m["id"] != message_id]
                        
                        # Keep only last 50 failed messages
                        data["failed"] = data["failed"][-50:]
                        
                        logger.warning(f"L6.queue [failed] - Message {message_id} permanently failed after {max_retries} attempts")
                        self._save_queue_data(data)
                        return False
                    else:
                        # Keep for retry
                        msg["status"] = "pending"
                        logger.debug(f"L6.queue [retry] - Message {message_id} will retry (attempt {msg['retry_count']}/{max_retries})")
                        self._save_queue_data(data)
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"L6.queue [failed] - Error handling failed message: {e}")
            return False
    
    def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics for monitoring."""
        try:
            data = self._load_queue_data()
            return {
                "pending": len([msg for msg in data["messages"] if msg.get("status") == "pending"]),
                "processed": len(data.get("processed", [])),
                "failed": len(data.get("failed", [])),
                "total_messages": len(data["messages"])
            }
        except Exception as e:
            logger.error(f"L6.queue [stats] - Error getting stats: {e}")
            return {"pending": 0, "processed": 0, "failed": 0, "total_messages": 0}

# Global queue instance
_message_queue = None

def get_message_queue() -> MessageQueue:
    """Get the global message queue instance."""
    global _message_queue
    if _message_queue is None:
        _message_queue = MessageQueue()
    return _message_queue

# Convenience functions for common operations

def enqueue_task_message(content: str, source_id: str = None, silent: bool = False, room_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Queue a task message for delivery via the RunService."""
    metadata = metadata.copy() if isinstance(metadata, dict) else {}
    metadata.setdefault("source_id", source_id)
    metadata.setdefault("silent", silent)
    # Always override room_id from parameter if provided (don't use setdefault)
    if room_id:
        metadata["room_id"] = room_id
    message_id = get_message_queue().enqueue_message("task", content, metadata)
    if message_id:
        try:
            from utils.queue_processor import process_queue_inline
            process_queue_inline()
        except Exception as e:
            logger.debug(f"L6.queue [enqueue_task_message] - Inline processing skipped: {e}")
    return message_id


def enqueue_manual_message(content: str, delivery: str = "default", channel_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Queue a manual message (manually_send_message) for delivery."""
    metadata = metadata.copy() if isinstance(metadata, dict) else {}
    metadata.setdefault("delivery", delivery)
    if channel_id:
        metadata.setdefault("channel_id", channel_id)
    message_id = get_message_queue().enqueue_message("manual", content, metadata)
    if message_id:
        try:
            from utils.queue_processor import process_queue_inline
            process_queue_inline()
        except Exception as e:
            logger.debug(f"L6.queue [enqueue_manual_message] - Inline processing skipped: {e}")
    return message_id


def enqueue_ui_action(content: Dict[str, Any], source_id: str = None) -> str:
    """Queue a UI action (e.g., show_form) for delivery via SSE."""
    if not isinstance(content, dict):
        content = {"action": "unknown"}
    metadata = {"source_id": source_id} if source_id else {}
    return get_message_queue().enqueue_message("ui", json.dumps(content, ensure_ascii=False), metadata)
