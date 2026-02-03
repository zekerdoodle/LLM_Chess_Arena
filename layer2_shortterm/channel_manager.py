"""
Channel Manager for Layer 2 - Short Term Memory

Provides channel switching logic and channel statistics tracking.
Manages the current active channel and provides channel-specific information.
"""

from typing import Dict, Optional

from utils.logger import get_logger

from .chat_history import ChatHistory

logger = get_logger(__name__)


class ChannelManager:
    """
    Manages channel switching and channel statistics.

    Tracks the current active channel and provides channel-specific information
    including history statistics and channel switching logic.
    """

    def __init__(self, chat_history: ChatHistory):
        """
        Initialize channel manager.

        Args:
            chat_history: ChatHistory instance for accessing channel data
        """
        self.chat_history = chat_history
        self.current_channel: Optional[str] = None
        self.channel_stats: Dict[str, Dict] = {}

        logger.debug("L2.channel_manager [setup] - Initialized channel manager")

    def switch_channel(self, channel_id: str, channel_name: Optional[str] = None) -> bool:
        """
        Switch to a different channel and log the switch.

        Args:
            channel_id: Channel ID to switch to
            channel_name: Optional channel name for logging
            
        Returns:
            True if channel was switched, False if already in that channel
        """
        if self.current_channel != channel_id:
            old_channel = self.current_channel
            self.current_channel = channel_id

            # Get channel history stats for logging (with fallback on error)
            try:
                history_count = len(self.chat_history.get_history(channel_id))
                total_tokens = self.chat_history.get_total_tokens(channel_id)
            except Exception as e:
                logger.debug(
                    f"L2.channel_manager [ch:{channel_id}] - Failed to get history stats: {e}"
                )
                history_count = 0
                total_tokens = 0

            # Log channel switch
            if channel_name:
                logger.info(
                    f"L2.channel_manager [ch:{channel_name}] - Switched to channel (history: {history_count} messages, {total_tokens} tokens)"
                )
            else:
                logger.info(
                    f"L2.channel_manager [ch:{channel_id}] - Switched to channel (history: {history_count} messages, {total_tokens} tokens)"
                )

            if old_channel:
                logger.debug(
                    f"L2.channel_manager [ch:{channel_id}] - Previous channel was {old_channel}"
                )
            return True
        else:
            logger.debug(
                f"L2.channel_manager [ch:{channel_id}] - Already in channel"
            )
            return False

    def get_current_channel(self) -> Optional[str]:
        """
        Get the current active channel ID.

        Returns:
            Current channel ID or None if no channel is active
        """
        return self.current_channel

    def get_channel_stats(self, channel_id: str) -> Dict:
        """
        Get comprehensive statistics for a specific channel.

        Args:
            channel_id: Channel ID to get stats for

        Returns:
            Dictionary containing channel statistics
        """
        # Get basic stats from ChatHistory
        basic_stats = self.chat_history.get_channel_stats(channel_id)
        
        # Add additional channel manager stats
        stats = {
            "channel_id": channel_id,
            "is_current": channel_id == self.current_channel,
            **basic_stats
        }

        # Add truncation stats
        truncation_stats = self.chat_history.get_truncation_stats(channel_id)
        stats.update(truncation_stats)

        return stats

    def get_all_channel_stats(self) -> Dict[str, Dict]:
        """
        Get statistics for all channels.

        Returns:
            Dictionary mapping channel IDs to their statistics
        """
        all_channels = self.chat_history.get_all_channels()
        stats = {}

        for channel_id in all_channels:
            stats[channel_id] = self.get_channel_stats(channel_id)

        return stats

    def get_channel_summary(self, channel_id: str) -> str:
        """
        Get a human-readable summary of a channel's status.

        Args:
            channel_id: Channel ID to summarize

        Returns:
            Formatted string summary of the channel
        """
        stats = self.get_channel_stats(channel_id)
        
        summary_parts = []
        
        # Current channel indicator
        if stats["is_current"]:
            summary_parts.append("(CURRENT)")
        
        # Message count
        summary_parts.append(f"{stats['message_count']} messages")
        
        # Token usage
        token_usage = f"{stats['current_tokens']}/{stats['max_tokens']} tokens"
        if stats["is_truncated"]:
            token_usage += " (TRUNCATED)"
        summary_parts.append(token_usage)
        
        return f"Channel {channel_id}: {' '.join(summary_parts)}"

    def get_current_channel_stats(self) -> Optional[Dict]:
        """
        Get statistics for the current active channel.

        Returns:
            Channel statistics or None if no channel is active
        """
        if self.current_channel is None:
            return None
        
        return self.get_channel_stats(self.current_channel)

    def is_channel_active(self, channel_id: str) -> bool:
        """
        Check if a channel is currently active.

        Args:
            channel_id: Channel ID to check

        Returns:
            True if the channel is currently active
        """
        return self.current_channel == channel_id

    def get_channel_activity_summary(self) -> str:
        """
        Get a summary of all channels and their activity status.

        Returns:
            Formatted string summary of all channels
        """
        all_stats = self.get_all_channel_stats()
        
        if not all_stats:
            return "No channels with history"
        
        summaries = []
        for channel_id, stats in all_stats.items():
            summary = self.get_channel_summary(channel_id)
            summaries.append(summary)
        
        return "\n".join(summaries)

    def clear_channel_data(self, channel_id: str) -> None:
        """
        Clear all data for a specific channel.

        Args:
            channel_id: Channel ID to clear
        """
        self.chat_history.clear_channel(channel_id)
        
        # If this was the current channel, clear it
        if self.current_channel == channel_id:
            self.current_channel = None
        
        logger.info(f"L2.channel_manager [ch:{channel_id}] - Cleared all channel data")

    def reset_current_channel(self) -> None:
        """
        Reset the current channel to None.
        Useful for cleanup or when switching contexts.
        """
        old_channel = self.current_channel
        self.current_channel = None
        
        if old_channel:
            logger.debug(f"L2.channel_manager [reset] - Reset current channel from {old_channel}")
        else:
            logger.debug("L2.channel_manager [reset] - Reset current channel (was already None)") 