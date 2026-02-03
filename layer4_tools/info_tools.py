"""
Information Tools for Layer 4 - Tools

Provides information management tools including Theo memory CRUD operations. These tools allow Theo to manage his own
information and memory state intelligently.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from layer2_shortterm.chat_history import ChatHistory
from layer3_longterm.theo_memory import (
    add_memory as theo_add_memory,
    delete_memory as theo_delete_memory,
    get_theo_manager,
    update_memory as theo_update_memory,
)
from layer3_longterm.verbatim_memory import get_verbatim_manager
from utils.logger import get_logger
from utils.vault_paths import get_vault_root
from utils.rooms_meta import load as load_rooms_meta

logger = get_logger(__name__)


# Removed reset_chat_history tool per deprecation


def add_memory(memory_string: str, importance: int = 50, *, context: Optional[dict] = None) -> dict:
    """
    Add a new Theo memory with importance ranking.
    
    Args:
        memory_string: The memory content to store
        importance: Importance ranking 0-100 (default 50)
        context: Optional context dict containing run_id for regeneration tracking
        
    Returns:
        Dict with operation status and memory details
    """
    try:
        logger.info(f"L4.tools [tool:add_memory] - Adding memory (importance: {importance})")
        logger.debug(f"L4.tools [tool:add_memory] - Memory content: {memory_string[:100]}...")
        
        # Extract run_id from context for regeneration tracking
        run_id = (context or {}).get("run_id")
        if isinstance(run_id, str):
            run_id = run_id.strip() or None
        else:
            run_id = None

        # Build metadata with run_id for revocation support
        metadata = {"run_id": run_id} if run_id else None
        
        # Add memory using theo_memory module
        memory_id = theo_add_memory(memory_string, importance, metadata=metadata)
        
        if memory_id:
            logger.info(f"L4.tools [tool:add_memory] - Successfully added memory: {memory_id}")
            return {
                "success": True,
                "memory_id": memory_id,
                "importance": importance,
                "message": f"Memory added successfully with ID: {memory_id}"
            }
        else:
            logger.error(f"L4.tools [tool:add_memory] - Failed to add memory")
            return {
                "success": False,
                "error": "Memory creation failed",
                "message": "Failed to add memory to storage"
            }
            
    except Exception as e:
        logger.error(f"L4.tools [tool:add_memory] - Error adding memory: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to add memory due to error"
        }


def update_memory(memory_id: str, memory_string: Optional[str] = None, importance: Optional[int] = None) -> dict:
    """
    Update an existing Theo memory.
    
    Args:
        memory_id: ID of the memory to update
        memory_string: New memory content (optional)
        importance: New importance ranking 0-100 (optional)
        
    Returns:
        Dict with operation status and update details
    """
    try:
        logger.info(f"L4.tools [tool:update_memory] - Updating memory: {memory_id}")
        
        # Validate that at least one field is being updated
        if memory_string is None and importance is None:
            logger.warning(f"L4.tools [tool:update_memory] - No updates specified for memory: {memory_id}")
            return {
                "success": False,
                "error": "No updates specified",
                "message": "Must specify either memory_string or importance to update"
            }
        
        # Build update description for logging
        updates = []
        if memory_string is not None:
            updates.append("content")
            logger.debug(f"L4.tools [tool:update_memory] - New content: {memory_string[:100]}...")
        if importance is not None:
            updates.append(f"importance -> {importance}")
        
        logger.debug(f"L4.tools [tool:update_memory] - Updates: {', '.join(updates)}")
        
        # Update memory using theo_memory module
        success = theo_update_memory(memory_id, memory_string, importance)
        
        if success:
            logger.info(f"L4.tools [tool:update_memory] - Successfully updated memory: {memory_id}")
            return {
                "success": True,
                "memory_id": memory_id,
                "updates_applied": updates,
                "message": f"Memory {memory_id} updated successfully: {', '.join(updates)}"
            }
        else:
            logger.warning(f"L4.tools [tool:update_memory] - Memory not found: {memory_id}")
            
            # List recent memory IDs to help find the right one
            try:
                manager = get_theo_manager()
                recent_ids = [mem.memory_id for mem in manager.memories[-5:]]
                if recent_ids:
                    id_list = ", ".join(recent_ids)
                    return {
                        "success": False,
                        "error": "Memory not found",
                        "message": f"No memory found with ID: {memory_id}\n\nRecent memory IDs: {id_list}",
                        "recent_ids": recent_ids
                    }
            except Exception:
                pass
            
            return {
                "success": False,
                "error": "Memory not found",
                "message": f"No memory found with ID: {memory_id}"
            }
            
    except Exception as e:
        logger.error(f"L4.tools [tool:update_memory] - Error updating memory: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to update memory due to error"
        }


def delete_memory(memory_id: str) -> dict:
    """
    Delete a Theo memory.
    
    Args:
        memory_id: ID of the memory to delete
        
    Returns:
        Dict with operation status and deletion details
    """
    try:
        logger.info(f"L4.tools [tool:delete_memory] - Deleting memory: {memory_id}")
        
        # Get memory details before deletion for logging
        theo_manager = get_theo_manager()
        memory = theo_manager.get_memory(memory_id)
        
        if memory:
            logger.debug(f"L4.tools [tool:delete_memory] - Memory found: importance={memory.importance}, content={memory.content[:50]}...")
        
        # Delete memory using theo_memory module
        success = theo_delete_memory(memory_id)
        
        if success:
            logger.info(f"L4.tools [tool:delete_memory] - Successfully deleted memory: {memory_id}")
            return {
                "success": True,
                "memory_id": memory_id,
                "message": f"Memory {memory_id} deleted successfully"
            }
        else:
            logger.warning(f"L4.tools [tool:delete_memory] - Memory not found: {memory_id}")
            
            # List recent memory IDs to help find the right one
            try:
                manager = get_theo_manager()
                recent_ids = [mem.memory_id for mem in manager.memories[-5:]]
                if recent_ids:
                    id_list = ", ".join(recent_ids)
                    return {
                        "success": False,
                        "error": "Memory not found",
                        "message": f"No memory found with ID: {memory_id}\n\nRecent memory IDs: {id_list}",
                        "recent_ids": recent_ids
                    }
            except Exception:
                pass
            
            return {
                "success": False,
                "error": "Memory not found",
                "message": f"No memory found with ID: {memory_id}"
            }
            
    except Exception as e:
        logger.error(f"L4.tools [tool:delete_memory] - Error deleting memory: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to delete memory due to error"
        }


# Additional helper function for getting memory statistics
def get_memory_info() -> dict:
    """
    Get comprehensive information about Theo's memory state.
    
    Returns:
        Dict with memory statistics and status
    """
    try:
        logger.debug(f"L4.tools [tool:get_memory_info] - Retrieving memory information")
        
        # Get managers
        chat_manager = ChatHistory()
        verbatim_manager = get_verbatim_manager()
        theo_manager = get_theo_manager()
        
        # Get statistics
        chat_stats = chat_manager.get_stats()
        verbatim_stats = verbatim_manager.get_memory_stats()
        theo_stats = theo_manager.get_memory_stats()
        
        # Get guaranteed memories (importance = 100)
        guaranteed_memories = theo_manager.get_guaranteed_memories()
        
        result = {
            "chat_history": chat_stats,
            "verbatim_memory": verbatim_stats,
            "theo_memory": theo_stats,
            "guaranteed_memories": len(guaranteed_memories),
            "timestamp": time.time()
        }
        
        logger.debug(f"L4.tools [tool:get_memory_info] - Memory info retrieved successfully")
        return result
        
    except Exception as e:
        logger.error(f"L4.tools [tool:get_memory_info] - Error retrieving memory info: {e}", exc_info=True)
        return {
            "error": str(e),
            "message": "Failed to retrieve memory information"
        }


def _extract_exchanges(messages: List[Dict], count: int = 3, from_start: bool = True) -> List[Tuple[Dict, Optional[Dict]]]:
    """
    Extract exchanges (user message + assistant response pairs) from message list.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        count: Number of exchanges to extract
        from_start: If True, extract from start; if False, extract from end
        
    Returns:
        List of (user_msg, assistant_msg) tuples. assistant_msg may be None if no response.
    """
    exchanges = []
    i = 0
    
    # Work through messages to find user→assistant pairs
    while i < len(messages) and len(exchanges) < count:
        msg = messages[i]
        
        # Look for user messages
        if msg.get('role') == 'user':
            user_msg = msg
            assistant_msg = None
            
            # Look for following assistant message
            if i + 1 < len(messages) and messages[i + 1].get('role') == 'assistant':
                assistant_msg = messages[i + 1]
                i += 2
            else:
                i += 1
            
            exchanges.append((user_msg, assistant_msg))
        else:
            i += 1
    
    if not from_start:
        # For end extraction, we need to work backwards
        exchanges = []
        i = len(messages) - 1
        
        while i >= 0 and len(exchanges) < count:
            msg = messages[i]
            
            # Look for assistant messages and work backwards to find the user message
            if msg.get('role') == 'assistant':
                assistant_msg = msg
                user_msg = None
                
                # Look for preceding user message
                if i - 1 >= 0 and messages[i - 1].get('role') == 'user':
                    user_msg = messages[i - 1]
                    i -= 2
                else:
                    i -= 1
                
                if user_msg:
                    exchanges.insert(0, (user_msg, assistant_msg))
            else:
                i -= 1
    
    return exchanges


def _format_exchange(exchange: Tuple[Dict, Optional[Dict]], index: int) -> str:
    """Format a single exchange for display."""
    user_msg, assistant_msg = exchange
    
    user_content = (user_msg.get('content') or '')[:150]
    if len(user_msg.get('content', '')) > 150:
        user_content += '...'
    
    lines = [f"  Exchange {index}:"]
    lines.append(f"    User: {user_content}")
    
    if assistant_msg:
        assistant_content = (assistant_msg.get('content') or '')[:150]
        if len(assistant_msg.get('content', '')) > 150:
            assistant_content += '...'
        lines.append(f"    Assistant: {assistant_content}")
    else:
        lines.append(f"    Assistant: (no response)")
    
    return '\n'.join(lines)


def list_rooms(room_id: Optional[str] = None) -> Tuple[str, Dict]:
    """
    List all available rooms with detailed information including title, message history preview, and activity.
    
    Args:
        room_id: Optional specific room ID to get details for. If omitted, lists all rooms.
        
    Returns:
        Tuple of (formatted_text, data_dict) with room information
    """
    try:
        logger.debug(f"L4.tools [tool:list_rooms] - Listing rooms (filter: {room_id or 'all'})")
        
        # Get vault paths
        try:
            vault_root = Path(get_vault_root())
        except Exception:
            vault_root = Path("vault")
        
        rooms_dir = vault_root / "chats"
        
        # Load room metadata (titles, types, etc.)
        try:
            meta = load_rooms_meta()
        except Exception:
            meta = {}
        
        # Get all room files
        try:
            room_files = list(rooms_dir.glob("*.json"))
            all_room_ids = [f.stem for f in room_files]
        except Exception:
            all_room_ids = []
        
        # Normalize null-like strings to None for graceful handling
        if room_id and room_id.lower() in ("null", "none", "undefined", ""):
            room_id = None
        
        # Filter to specific room if requested
        if room_id:
            if room_id not in all_room_ids:
                return (
                    f"ERROR: Room '{room_id}' not found",
                    {"success": False, "error": "room_not_found", "room_id": room_id}
                )
            all_room_ids = [room_id]
        
        # Collect room details
        rooms_data = []
        
        for rid in sorted(all_room_ids):
            room_file = rooms_dir / f"{rid}.json"
            
            # Load room messages
            try:
                with open(room_file, 'r', encoding='utf-8') as f:
                    messages = json.load(f)
                if not isinstance(messages, list):
                    messages = []
            except Exception as e:
                logger.warning(f"L4.tools [tool:list_rooms] - Failed to load room {rid}: {e}")
                messages = []
            
            # Get metadata
            room_meta = meta.get(rid, {})
            title = room_meta.get('title', 'New Chat')
            room_type = room_meta.get('room_type', 'chat')
            updated_at = room_meta.get('updated_at', 0)
            
            # Get message count
            message_count = len(messages)
            
            # Extract last message timestamp
            last_timestamp = 0
            if messages:
                last_timestamp = messages[-1].get('timestamp', 0)
            
            # Extract first and last exchanges
            first_exchanges = _extract_exchanges(messages, count=3, from_start=True)
            last_exchanges = _extract_exchanges(messages, count=3, from_start=False)
            
            rooms_data.append({
                'room_id': rid,
                'title': title,
                'room_type': room_type,
                'message_count': message_count,
                'last_timestamp': last_timestamp,
                'updated_at': updated_at,
                'first_exchanges': first_exchanges,
                'last_exchanges': last_exchanges,
            })
        
        # Format output
        if not rooms_data:
            return (
                "No rooms found.",
                {"success": True, "rooms": [], "count": 0}
            )
        
        # Build formatted text output
        lines = [f"Found {len(rooms_data)} room(s):\n"]
        
        for room_data in rooms_data:
            rid = room_data['room_id']
            title = room_data['title']
            room_type = room_data['room_type']
            msg_count = room_data['message_count']
            
            lines.append(f"Room: {rid}")
            lines.append(f"  Title: {title}")
            lines.append(f"  Type: {room_type}")
            lines.append(f"  Messages: {msg_count}")
            
            if msg_count > 0:
                # Show first exchanges
                if room_data['first_exchanges']:
                    lines.append(f"\n  First exchanges:")
                    for idx, exchange in enumerate(room_data['first_exchanges'], 1):
                        lines.append(_format_exchange(exchange, idx))
                
                # Show last exchanges (only if different from first)
                if room_data['last_exchanges'] and msg_count > 6:
                    lines.append(f"\n  Last exchanges:")
                    for idx, exchange in enumerate(room_data['last_exchanges'], 1):
                        lines.append(_format_exchange(exchange, idx))
            else:
                lines.append("  (No messages yet)")
            
            lines.append("")  # Blank line between rooms
        
        result_text = '\n'.join(lines)
        
        logger.debug(f"L4.tools [tool:list_rooms] - Retrieved {len(rooms_data)} room(s)")
        
        return (
            result_text,
            {
                "success": True,
                "rooms": rooms_data,
                "count": len(rooms_data)
            }
        )
        
    except Exception as e:
        logger.error(f"L4.tools [tool:list_rooms] - Error listing rooms: {e}", exc_info=True)
        return (
            f"ERROR: Failed to list rooms: {str(e)}",
            {"success": False, "error": str(e)}
        )
