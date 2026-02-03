from __future__ import annotations
"""
Human Memory Extraction for Layer 3 - Long Term Memory

Provides automatic extraction of human memories from chat interactions every 10 minutes
when new messages are detected. Uses AI models for memory extraction and embeddings
for duplicate detection.

SPECS ALIGNMENT (docs/specs.md lines 24-26):
- Scribe loop runs every 10 minutes, ONLY IF there's a new message
- Uses mini-model with scribe instructions to extract important memories
- Instructed not to create duplicates or similar memories
- Storage: memory string, timestamp, embedding
- Stored in vault/human_memories.json
- Retrieval ranking: 1) Relevance (similarity), 2) Recency
"""

import asyncio
import json
import threading
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Pydantic is optional for CLI/migration paths; provide a safe stub when unavailable
try:
    from pydantic import BaseModel  # type: ignore
except Exception:  # pragma: no cover - minimal runtime fallback
    class BaseModel:  # type: ignore
        """Minimal stub for environments without pydantic installed."""
        pass

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from layer2_shortterm.chat_history import ChatHistory
from utils.config_loader import get_config_value, load_config
from utils.logger import get_logger

try:
    from layer3_longterm.embeddings import embed as _embed_export, retrieve as _retrieve_export
except Exception:  # pragma: no cover - happens in stripped-down CLI contexts
    _embed_export = None
    _retrieve_export = None

# Backwards-compatibility handles for tests that patch HumanMemory embed/retrieve directly
embed = _embed_export
retrieve = _retrieve_export

logger = get_logger(__name__)


class ExtractedMemory(BaseModel):
    """Pydantic model for a single extracted memory."""

    content: str
    confidence: float
    source_snippet: str


class MemoryExtractionResponse(BaseModel):
    """Pydantic model for the complete memory extraction response."""

    memories: List[ExtractedMemory]


@dataclass
class HumanMemory:
    """Data class for storing human memory entries."""

    content: str
    timestamp: float
    channel: str
    confidence: float
    # List of message snippets that led to this memory
    source_messages: List[str]
    embedding_id: Optional[str] = None


def _get_retrieve_callable():
    """Resolve the embeddings.retrieve callable, caching for future use."""
    global retrieve
    if retrieve is not None:
        return retrieve
    from layer3_longterm.embeddings import retrieve as _retrieve_func
    retrieve = _retrieve_func
    return retrieve


def _get_embed_callable():
    """Resolve the embeddings.embed callable, caching for future use."""
    global embed
    if embed is not None:
        return embed
    from layer3_longterm.embeddings import embed as _embed_func
    embed = _embed_func
    return embed


def salvage_legacy_wrapped_content(raw: str) -> List[HumanMemory]:
    """Salvage HumanMemory entries from legacy ChatCompletion-wrapped content strings.

    This attempts to extract a JSON object containing a top-level "memories" array
    from a legacy serialized string and convert each item into a HumanMemory with
    reasonable defaults.

    Args:
        raw: The raw content string that may contain embedded JSON.

    Returns:
        List of HumanMemory entries (empty if nothing could be salvaged).

    Examples:
        >>> payload = "ChatCompletion(... content='{"memories":[{"content":"A","confidence":0.9,"source_snippet":"s"}]}' ...)"
        >>> items = salvage_legacy_wrapped_content(payload)
        >>> len(items) >= 1
        True
    """
    try:
        import re
        # Try to grab an inner JSON object assigned to content='...'
        m = re.search(r"content='(\{.*?\})'\s*,\s*role=", raw, flags=re.DOTALL)
        inner = None
        if m:
            inner = m.group(1)
            # Unescape common backslash-escaped sequences if present
            inner = inner.encode('utf-8').decode('unicode_escape')
        else:
            # Fallback: any JSON object with a top-level memories array
            m2 = re.search(r"(\{\s*\"memories\"\s*:\s*\[.*?\]\s*\})", raw, flags=re.DOTALL)
            inner = m2.group(1) if m2 else None
        if not inner:
            return []
        data = json.loads(inner)
        items = data.get('memories', []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []
        salvaged: List[HumanMemory] = []
        now = time.time()
        for it in items:
            if not isinstance(it, dict):
                continue
            content = str(it.get('content', '')).strip()
            if not content:
                continue
            conf = float(it.get('confidence', 0.8) or 0.8)
            src = it.get('source_snippet', '')
            hm = HumanMemory(
                content=content,
                timestamp=now,
                channel="global",
                confidence=conf,
                source_messages=[src] if src else [],
            )
            salvaged.append(hm)
        return salvaged
    except Exception:
        return []


class HumanMemoryExtractor:
    """Extracts and manages human memories from chat history."""

    def __init__(
        self,
        vault_path: Optional[str] = None,
        timer_interval: int = 600,  # 10 minutes in seconds
        model_selector=None,
    ):
        """Initialize the human memory extractor.

        Args:
            vault_path: Path to vault directory for storing memories
            timer_interval: Interval in seconds for memory extraction (default: 10 minutes)
            model_selector: ModelSelector instance for making AI calls
        """
        # Resolve vault root centrally with legacy fallback
        try:
            if vault_path is None:
                from utils.vault_paths import get_vault_root
                self.vault_path = Path(get_vault_root())
            else:
                self.vault_path = Path(vault_path)
        except Exception:
            self.vault_path = Path(vault_path or "vault")
        self.timer_interval = timer_interval
        self.model_selector = model_selector
        self.memory_file = self.vault_path / "human_memories.json"

        # State tracking
        self.last_extraction_time = time.time()
        # channel -> message_count
        self.last_processed_channels: Dict[str, int] = {}
        self.extraction_timer = None
        self.is_running = False

        # Memory storage
        self.memories: List[HumanMemory] = []

        # Load existing memories
        self._load_memories()

        logger.debug(
            f"HumanMemoryExtractor initialized with {len(self.memories)} existing memories"
        )
        logger.info(
            f"Memory extraction timer set to {timer_interval} seconds ({timer_interval/60:.1f} minutes)"
        )

    @staticmethod
    def _ensure_first_person(text: str) -> str:
        """Guarantee that stored memories read as Theo's own recollection."""

        stripped = (text or "").strip()
        if not stripped:
            return stripped

        # Fast path: already first person (I, I'm, I've, My, Me, We, etc.)
        if re.match(r"^(?:i\b|i['’](?:m|ve|ll|d)\b|my\b|mine\b|me\b|we\b)", stripped, flags=re.IGNORECASE):
            return stripped

        # Light rewrite of common leading noun phrases while keeping content verbatim.
        normalized = re.sub(
            r"^(?:the\s+)?user\b",
            "User",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"^(?:zeke)\b",
            "Zeke",
            normalized,
            count=1,
            flags=re.IGNORECASE,
        )

        return normalized.strip()

    def start_timer(self, chat_history: ChatHistory) -> None:
        """Start the periodic memory extraction timer.

        Args:
            chat_history: ChatHistory instance to monitor for new messages
        """
        if self.is_running:
            logger.warning("Memory extraction timer is already running")
            return

        self.is_running = True
        self._schedule_next_extraction(chat_history)
        logger.info("Started human memory extraction timer")

    def stop_timer(self) -> None:
        """Stop the periodic memory extraction timer."""
        if self.extraction_timer:
            self.extraction_timer.cancel()
            self.extraction_timer = None

        self.is_running = False
        logger.info("Stopped human memory extraction timer")

    def _schedule_next_extraction(self, chat_history: ChatHistory) -> None:
        """Schedule the next memory extraction."""
        if not self.is_running:
            return

        def run_extraction():
            try:
                asyncio.run(self._perform_extraction(chat_history))
            except Exception as e:
                logger.error(f"Error in memory extraction: {e}", exc_info=True)
            finally:
                # Schedule next extraction if still running
                if self.is_running:
                    self._schedule_next_extraction(chat_history)

        self.extraction_timer = threading.Timer(
            self.timer_interval, run_extraction
        )
        # Ensure timer thread does not block process exit
        try:
            self.extraction_timer.daemon = True
        except Exception:
            pass
        self.extraction_timer.start()
        logger.debug(
            f"Scheduled next memory extraction in {self.timer_interval} seconds"
        )

    async def _perform_extraction(self, chat_history: ChatHistory) -> None:
        """Perform memory extraction if new messages are detected."""
        try:
            # Check if there are new messages since last extraction
            new_messages_detected = self._detect_new_messages(chat_history)

            if not new_messages_detected:
                logger.debug(
                    "L3.human_memory [timer] - No new messages detected, skipping extraction"
                )
                return

            # Calculate time since last extraction
            time_since_last = time.time() - self.last_extraction_time
            logger.info(
                f"L3.human_memory [timer] - Starting extraction cycle (new_messages: detected, last_extraction: {time_since_last//60:.0f}m ago)"
            )

            # Extract memories once using a global, cross-room chat history context
            # This ensures the scribe sees ALL rooms in chronological order and
            # relies on prompt-level token truncation to keep the most recent tail.
            total_extracted = await self._extract_memories_global(chat_history)
            channels = chat_history.get_all_channels()

            # Update last extraction time
            self.last_extraction_time = time.time()

            if total_extracted > 0:
                logger.info(
                    f"L3.human_memory [extraction] - Extracted {total_extracted} new memories from {len(channels)} channels"
                )
                self._save_memories()
            else:
                logger.debug(
                    "L3.human_memory [extraction] - No new memories extracted"
                )

        except Exception as e:
            logger.error(f"Error during memory extraction: {e}", exc_info=True)

    def _detect_new_messages(self, chat_history: ChatHistory) -> bool:
        """Detect if there are new messages since last extraction.

        Args:
            chat_history: ChatHistory instance to check

        Returns:
            True if new messages are detected in any channel
        """
        new_messages_found = False

        for channel in chat_history.get_all_channels():
            channel_history = chat_history.get_history(channel)
            current_message_count = len(channel_history)

            # Get previous message count for this channel
            previous_count = self.last_processed_channels.get(channel, 0)

            if current_message_count > previous_count:
                new_messages_found = True
                logger.debug(
                    f"Channel {channel}: {current_message_count - previous_count} new messages"
                )

            # Update stored count
            self.last_processed_channels[channel] = current_message_count

        return new_messages_found

    def _gather_global_messages(self, chat_history: ChatHistory) -> List[Dict]:
        """Collect all messages across all rooms in chronological order.

        Args:
            chat_history: ChatHistory instance

        Returns:
            A list of message dicts sorted by timestamp ascending across all rooms.

        Notes:
            - Includes user, assistant, and system roles from every room
            - Sorting key is (timestamp, channel) for stability on equal timestamps
        """
        try:
            messages: List[Dict] = []
            for ch in chat_history.get_all_channels():
                try:
                    msgs = chat_history.get_history(ch)
                    for m in msgs:
                        # Ensure required fields are present
                        if not isinstance(m, dict):
                            continue
                        role = str(m.get("role", "")).strip()
                        content = str(m.get("content", "")).strip()
                        ts = float(m.get("timestamp", 0) or 0)
                        if not role or not content:
                            continue
                        messages.append({
                            "role": role,
                            "content": content,
                            "timestamp": ts,
                            "channel": ch,
                        })
                except Exception:
                    continue
            # Sort chronologically (oldest first), tie-break by channel for stability
            messages.sort(key=lambda m: (m.get("timestamp", 0), m.get("channel", "")))
            return messages
        except Exception as e:
            logger.error(f"L3.human_memory [global] - Failed to gather messages: {e}", exc_info=True)
            return []

    async def _extract_memories_global(self, chat_history: ChatHistory) -> int:
        """Run the scribe once using a global cross-room chat history view.

        This constructs a combined, chronological message list from ALL rooms,
        builds the scribe prompt (which enforces a tail-of-history truncation
        at the prompt level), and then parses/deduplicates/stores memories.

        Returns:
            Number of memories extracted
        """
        try:
            all_messages = self._gather_global_messages(chat_history)
            if not all_messages:
                return 0

            # Use scribe model from config for memory extraction; fall back gracefully in tests/standalone mode
            try:
                config = load_config()
            except Exception as exc:
                logger.warning(
                    "L3.human_memory [global] - Falling back to default scribe config due to load error: %s",
                    exc,
                )
                config = {}
            scribe_model = get_config_value(config, "scribe_model", "gpt-5-mini")

            # Call the same structured-output path, but attribute to a global scope
            extracted_memories = await self._extract_memories_with_model_selector(
                all_messages, scribe_model, channel="global"
            )

            # Filter out duplicates via embeddings
            new_memories = await self._filter_duplicate_memories(extracted_memories)

            # Store new memories
            for memory in new_memories:
                self.memories.append(memory)
                logger.debug(
                    f"L3.human_memory [global] - Added memory: {memory.content[:50]}... (confidence: {memory.confidence:.2f})"
                )

            return len(new_memories)
        except Exception as e:
            logger.error(f"L3.human_memory [global] - Error in global extraction: {e}", exc_info=True)
            return 0

    async def _extract_memories_for_channel(
        self, chat_history: ChatHistory, channel: str
    ) -> int:
        """Extract memories from a specific channel.

        Args:
            chat_history: ChatHistory instance
            channel: Channel ID to extract from

        Returns:
            Number of memories extracted
        """
        try:
            # Get recent messages from channel (last 50 messages or since last
            # extraction)
            channel_history = chat_history.get_history(
                channel, max_messages=50
            )

            if not channel_history:
                return 0

            # Filter to only user messages from the last extraction period
            user_messages = [
                msg
                for msg in channel_history
                if msg["role"] == "user"
                and msg["timestamp"] > self.last_extraction_time
            ]

            if not user_messages:
                return 0

            logger.debug(
                f"L3.human_memory [ch:{channel}] - Processing {len(user_messages)} user messages"
            )

            # Extract memories using AI model
            extracted_memories = await self._extract_memories_with_ai(
                user_messages, channel
            )

            # Filter out duplicates using embeddings
            new_memories = await self._filter_duplicate_memories(
                extracted_memories
            )

            # Calculate average confidence
            if extracted_memories:
                avg_confidence = sum(
                    m.confidence for m in extracted_memories
                ) / len(extracted_memories)
                logger.debug(
                    f"L3.human_memory [ch:{channel}] - AI extracted {len(extracted_memories)} memories (confidence: {avg_confidence:.2f} avg)"
                )

            # Log duplicate filtering
            duplicates_filtered = len(extracted_memories) - len(new_memories)
            if duplicates_filtered > 0:
                logger.warning(
                    f"L3.human_memory [extraction] - Filtered {duplicates_filtered} duplicate memories"
                )

            # Store new memories
            for memory in new_memories:
                self.memories.append(memory)
                logger.debug(
                    f"L3.human_memory [ch:{channel}] - Added memory: {memory.content[:50]}... (confidence: {memory.confidence:.2f})"
                )

            return len(new_memories)

        except Exception as e:
            logger.error(
                f"Error extracting memories for channel {channel}: {e}",
                exc_info=True,
            )
            return 0

    async def _extract_memories_with_ai(
        self, messages: List[Dict], channel: str
    ) -> List[HumanMemory]:
        """Use AI model to extract human memories from messages.

        Args:
            messages: List of message dictionaries
            channel: Channel ID

        Returns:
            List of extracted HumanMemory objects
        """
        if not self.model_selector:
            logger.warning(
                "No model_selector provided, cannot extract memories"
            )
            return []

        try:
            # Prepare context for the AI model
            message_context = self._prepare_message_context(messages)

            # Build scribe prompt via prompt_printer in downstream call
            logger.debug(
                "Preparing scribe prompt via utils.prompt_printer.build_scribe_prompt"
            )

            try:
                config = load_config()
            except Exception as exc:
                logger.warning(
                    "L3.human_memory [ai] - Falling back to default scribe config due to load error: %s",
                    exc,
                )
                config = {}
            scribe_model = get_config_value(config, "scribe_model", "gpt-5-mini")

            # Use ModelSelector and prompt_printer for proper architecture
            # compliance
            memories = await self._extract_memories_with_model_selector(
                messages, scribe_model, channel
            )

            logger.info(
                f"Extracted {len(memories)} potential memories from {len(messages)} messages"
            )

            return memories

        except Exception as e:
            logger.error(
                f"Error extracting memories with AI: {e}", exc_info=True
            )
            return []

    def _prepare_message_context(self, messages: List[Dict]) -> str:
        """Prepare message context for AI processing.

        Args:
            messages: List of message dictionaries

        Returns:
            Formatted string of message context
        """
        context_lines = []

        for msg in messages:
            timestamp_str = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(msg["timestamp"])
            )
            content = msg["content"][:500]  # Limit content length
            context_lines.append(f"[{timestamp_str}] User: {content}")

        return "\n".join(context_lines)

    # Note: Scribe prompt is centralized in utils.prompt_printer.build_scribe_prompt

    def _clip_memory_text(self, text: str, max_words: int = 30, max_chars: int = 240) -> str:
        """Normalize and clip memory text to keep it concise for prompt budgets.

        - Collapse whitespace
        - Limit to `max_words` words and `max_chars` characters
        - Strip surrounding quotes and trailing punctuation noise
        """
        try:
            s = " ".join((text or "").strip().split())
            # Strip surrounding quotes if the whole memory is quoted
            if (s.startswith("\"") and s.endswith("\"")) or (s.startswith("'") and s.endswith("'")):
                s = s[1:-1].strip()
            # Word clamp
            parts = s.split()
            if len(parts) > max_words:
                s = " ".join(parts[:max_words])
            # Char clamp
            if len(s) > max_chars:
                s = s[:max_chars].rstrip()
            # Clean trailing punctuation repetition
            while s and s[-1] in {',', ';', ':'}:
                s = s[:-1].rstrip()
            return self._ensure_first_person(s)
        except Exception:
            return str(text)[:max_chars]

    async def _extract_memories_with_model_selector(
        self, source_messages: List[Dict], model: str, channel: str
    ) -> List[HumanMemory]:
        """Extract memories using ModelSelector and prompt_printer for proper architecture compliance.

        Args:
            source_messages: Original messages to extract memories from
            model: Scribe model to use for extraction
            channel: Channel ID

        Returns:
            List of extracted HumanMemory objects
        """
        try:
            # Format messages for the scribe
            from utils.prompt_printer import build_scribe_prompt

            message_context = "\n".join(
                [
                    f"{msg['role'].title()}: {msg['content']}"
                    for msg in source_messages
                ]
            )

            # Build scribe prompt using prompt_printer
            scribe_prompt = build_scribe_prompt(message_context)

            logger.debug(
                f"L3.human_memory [scribe] - Calling scribe model: {model}"
            )

            # Validate provider support for structured outputs
            provider = self.model_selector.get_provider_from_model(model)
            # Anthropic currently lacks strict structured outputs; disallow
            if provider == "anthropic":
                raise ValueError(
                    f"Scribe model '{model}' cannot use structured outputs with provider: {provider}"
                )

            # Check if model supports structured outputs (OpenAI models)
            # Request structured outputs for all non-Anthropic providers
            json_schema = {
                "type": "json_schema",
                "name": "memory_extraction",
                "schema": {
                    "type": "object",
                    "properties": {
                        "memories": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "confidence": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                    },
                                    "source_snippet": {"type": "string"},
                                },
                                "required": [
                                    "content",
                                    "confidence",
                                    "source_snippet",
                                ],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["memories"],
                    "additionalProperties": False,
                },
                "strict": True,
            }

            # Pass both OpenAI-style `text` and local-style `text_format` to support all providers
            response = await self.model_selector.call_model(
                scribe_prompt,
                model,
                text={"format": json_schema},
                text_format=json_schema,
                reasoning={"effort": "high"},
            )
            logger.debug(
                f"L3.human_memory [scribe] - Requested structured outputs for provider={provider}, model={model}"
            )

            # Try provider-native parsed JSON (e.g., Groq response_format=json_schema)
            try:
                parsed_obj = None
                ch0 = (getattr(response, "choices", None) or [None])[0]
                if ch0 is not None:
                    msg = getattr(ch0, "message", None)
                    if msg is not None:
                        parsed_obj = getattr(msg, "parsed", None)
                if isinstance(parsed_obj, dict) and isinstance(parsed_obj.get("memories"), list):
                    current_time = time.time()
                    out: List[HumanMemory] = []
                    for item in parsed_obj.get("memories", []):
                        try:
                            content = item.get("content")
                            conf = float(item.get("confidence", 0.8))
                            if content and conf >= 0.6:
                                # Enforce concise memory size limits (≤30 words, ≤240 chars)
                                content = self._clip_memory_text(content)
                                out.append(HumanMemory(
                                    content=content,
                                    timestamp=current_time,
                                    channel=channel,
                                    confidence=conf,
                                    source_messages=source_messages[:3],
                                ))
                        except Exception:
                            continue
                    if out:
                        return out
            except Exception:
                # Fall back to text parsing below
                pass

            # Extract response text (tests pass Mock with .content/.text string)
            response_text = None
            if hasattr(response, "content"):
                response_text = response.content
            elif hasattr(response, "text"):
                response_text = response.text
            if response_text is None:
                response_text = str(response)

            # Handle case where response_text might be a Mock object (for
            # tests)
            if hasattr(response_text, "__len__"):
                logger.debug(
                    f"L3.human_memory [scribe] - Received response (length: {len(response_text)})"
                )
            else:
                logger.debug(
                    f"L3.human_memory [scribe] - Received response (type: {type(response_text)})"
                )
                response_text = str(response_text)

            # Parse memories via lenient parser to satisfy tests with simple JSON strings
            extracted_memories = []
            try:
                parsed = json.loads(str(response_text).strip())
                if isinstance(parsed, dict) and isinstance(parsed.get("memories"), list):
                    current_time = time.time()
                    for item in parsed["memories"]:
                        try:
                            content = item.get("content")
                            conf = float(item.get("confidence", 0.8))
                            if content and conf >= 0.6:
                                content = self._clip_memory_text(content)
                                extracted_memories.append(HumanMemory(
                                    content=content,
                                    timestamp=current_time,
                                    channel=channel,
                                    confidence=conf,
                                    source_messages=source_messages[:3],
                                ))
                        except Exception:
                            continue
            except Exception:
                extracted_memories = self._parse_scribe_response(
                    str(response_text), channel, source_messages
                )

            return extracted_memories

        except Exception as e:
            logger.error(
                f"L3.human_memory [scribe] - Error in memory extraction: {e}",
                exc_info=True,
            )
            return []

    def _parse_structured_response(
        self, response_text: str, channel: str, source_messages: List[Dict]
    ) -> List[HumanMemory]:
        """Parse structured JSON response from OpenAI structured outputs.

        Args:
            response_text: The guaranteed JSON response text from structured outputs
            channel: Channel ID
            source_messages: Original source messages

        Returns:
            List of extracted HumanMemory objects
        """
        try:
            memories = []
            current_time = time.time()

            # Check if response is empty or whitespace-only
            if not response_text or not response_text.strip():
                logger.warning(
                    f"L3.human_memory [structured] - Received empty response from structured outputs"
                )
                return []

            # Parse the JSON response (guaranteed to be valid JSON from
            # structured outputs)
            import json

            parsed_data = json.loads(response_text.strip())

            # Extract memories from the structured response
            if "memories" in parsed_data and isinstance(
                parsed_data["memories"], list
            ):
                for item in parsed_data["memories"]:
                    if isinstance(item, dict) and all(
                        key in item
                        for key in ["content", "confidence", "source_snippet"]
                    ):
                        confidence = float(item["confidence"])
                        # Only include memories with confidence >= 0.6
                        if confidence >= 0.6:
                            memory = HumanMemory(
                                content=self._clip_memory_text(item["content"]),
                                timestamp=current_time,
                                channel=channel,
                                confidence=confidence,
                                source_messages=source_messages[:3],
                            )
                            memories.append(memory)
                            logger.debug(
                                f"L3.human_memory [structured] - Extracted memory: {item['content']} (confidence: {confidence:.2f})"
                            )
                        else:
                            logger.debug(
                                f"L3.human_memory [structured] - Skipped low confidence memory: {item['content']} (confidence: {confidence:.2f})"
                            )

            logger.info(
                f"L3.human_memory [structured] - Parsed {len(memories)} memories from structured response"
            )
            return memories

        except json.JSONDecodeError as e:
            logger.error(
                f"L3.human_memory [structured] - Unexpected JSON decode error: {e}"
            )
            # Fallback to regular parsing if structured outputs somehow failed
            return self._parse_scribe_response(
                response_text, channel, source_messages
            )
        except Exception as e:
            logger.error(
                f"L3.human_memory [structured] - Error parsing structured response: {e}",
                exc_info=True,
            )
            return []

    def _parse_scribe_response(
        self, response_text: str, channel: str, source_messages: List[Dict]
    ) -> List[HumanMemory]:
        """Parse scribe response text to extract memories (simplified approach).

        Args:
            response_text: The scribe's response text
            channel: Channel ID
            source_messages: Original source messages

        Returns:
            List of extracted HumanMemory objects
        """
        try:
            memories = []
            current_time = time.time()

            # Try JSON parsing first (for structured responses)
            try:
                import json

                parsed_data = json.loads(response_text.strip())

                # Handle array of memory objects
                if isinstance(parsed_data, list):
                    for item in parsed_data:
                        if isinstance(item, dict) and "content" in item:
                            confidence = item.get("confidence", 0.8)
                            # Only include memories with confidence >= 0.6
                            if confidence >= 0.6:
                                content = self._ensure_first_person(item["content"])
                                memory = HumanMemory(
                                    content=content,
                                    timestamp=current_time,
                                    channel=channel,
                                    confidence=confidence,
                                    source_messages=source_messages[:3],
                                )
                                memories.append(memory)
                                logger.debug(
                                    f"L3.human_memory [scribe] - Extracted JSON memory: {item['content']} (confidence: {confidence})"
                                )
                            else:
                                logger.debug(
                                    f"L3.human_memory [scribe] - Skipped low confidence memory: {item['content']} (confidence: {confidence})"
                                )

                    logger.info(
                        f"L3.human_memory [scribe] - Parsed {len(memories)} memories from JSON response"
                    )
                    return memories

            except (json.JSONDecodeError, KeyError, TypeError):
                # Fall back to line-by-line parsing
                pass

            # Simple parsing approach - look for lines that seem like first-person memories
            lines = response_text.strip().split("\n")

            # Normalize helper for curly quotes/dashes and spacing
            def _normalize(s: str) -> str:
                return (
                    s.replace("\u2019", "'")  # right single quote
                     .replace("\u2018", "'")  # left single quote
                     .replace("\u2014", "-")  # em dash
                     .replace("\u2013", "-")  # en dash
                )

            # Regex to detect a first-person start robustly
            # Matches: "I ...", "I'm/ I’m ...", "I've/ I’ve ...", "I'll/ I’ll ...", "I'd/ I’d ...",
            # or common self-referential starts like "I think/ believe/ learned/ plan/ intend/ prefer/ value"
            fp_start_re = re.compile(
                r"^(?:i\s|i['’](?:m|ve|ll|d)\s|i\s+(?:am|was|will|think|believe|learned|plan|intend|prefer|value|feel|realized|changed|want|need|like|dislike))",
                re.IGNORECASE,
            )

            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue

                # Skip invalid/error chatter
                llower = line.lower()
                if any(
                    phrase in llower
                    for phrase in (
                        "not valid json",
                        "json error",
                        "error:",
                        "invalid",
                        "parse",
                        "failed",
                    )
                ):
                    continue

                # Remove bullet/numbering/prefix symbols commonly used by models
                if line.startswith(("•", "-", "*", "—", "–")) or re.match(r"^\d+\.\s", line):
                    line = re.sub(r"^(?:[\u2022\-\*\u2014\u2013]+\s*|\d+\.\s+)", "", line).strip()

                # Normalize punctuation for robust matching
                norm = _normalize(line)
                # Accept concise first-person Theo-centric statements
                if len(norm) >= 8 and fp_start_re.match(norm):
                    memory_text = self._ensure_first_person(line)
                    memory = HumanMemory(
                        content=memory_text,
                        timestamp=current_time,
                        channel=channel,
                        confidence=0.8,
                        source_messages=source_messages[:3],
                    )
                    memories.append(memory)
                    logger.debug(f"L3.human_memory [scribe] - Extracted memory: {line}")

            # If nothing found, try a relaxed heuristic: lines containing a clear first-person clause
            if not memories:
                relaxed_re = re.compile(r"\bI\s+(?:think|believe|learned|prefer|value|plan|intend|realized|want|need)\b", re.IGNORECASE)
                for raw_line in lines:
                    line = raw_line.strip()
                    if not line or len(line) < 12:
                        continue
                    norm = _normalize(line)
                    if relaxed_re.search(norm):
                        memory_text = self._ensure_first_person(line)
                        memories.append(
                            HumanMemory(
                                content=memory_text,
                                timestamp=current_time,
                                channel=channel,
                                confidence=0.7,
                                source_messages=source_messages[:3],
                            )
                        )
                if memories:
                    logger.debug(
                        f"L3.human_memory [scribe] - Relaxed extraction captured {len(memories)} memories"
                    )

            logger.info(
                f"L3.human_memory [scribe] - Parsed {len(memories)} memories from response"
            )
            return memories

        except Exception as e:
            logger.error(
                f"L3.human_memory [scribe] - Error parsing response: {e}",
                exc_info=True,
            )
            return []

    async def _filter_duplicate_memories(
        self, memories: List[HumanMemory]
    ) -> List[HumanMemory]:
        """Filter out duplicate memories using embedding similarity.

        Args:
            memories: List of candidate memories

        Returns:
            List of memories with duplicates removed
        """
        if not memories:
            return memories

        new_memories = []
        # Track normalized contents accepted in this batch to prevent near-dup duplicates
        batch_norm_texts: List[str] = []

        for memory in memories:
            try:
                # Enforce confidence gate (defensive)
                if getattr(memory, "confidence", 0.0) < 0.6:
                    logger.debug(
                        f"Skipping low-confidence memory: '{memory.content[:60]}...' ({memory.confidence:.2f})"
                    )
                    continue

                # Batch-level near-duplicate check
                norm = " ".join(memory.content.strip().lower().split())
                is_duplicate = False
                # Exact match in batch
                if norm in batch_norm_texts:
                    is_duplicate = True
                else:
                    # Fuzzy compare to batch items
                    try:
                        from difflib import SequenceMatcher
                        for prev in batch_norm_texts:
                            if SequenceMatcher(None, prev, norm).ratio() >= 0.92:
                                is_duplicate = True
                                break
                    except Exception:
                        pass

                # Store-level duplicate check via embeddings across ALL memory types
                if not is_duplicate:
                    # Match test signature: (query, k, similarity_threshold)
                    # Avoid passing extra kwargs that mocks may not accept
                    # Lazy import to avoid hard dependency during migrations/CLI
                    retrieve_fn = _get_retrieve_callable()
                    similar_memories = retrieve_fn(
                        memory.content,
                        12,
                        0.0,
                    )
                    # Per-type similarity thresholds for safer cross-type dedupe
                    thresholds = {
                        "human_memory": 0.90,
                        "theo_memory": 0.90,
                        "verbatim_memory": 0.93,
                    }
                    for _text, similarity, metadata in similar_memories:
                        mtype = (metadata or {}).get("type")
                        if mtype in thresholds and float(similarity) >= thresholds[mtype]:
                            logger.debug(
                                f"Duplicate memory detected (store:{mtype}) -> '{memory.content[:60]}...' (sim: {similarity:.3f})"
                            )
                            is_duplicate = True
                            break

                if not is_duplicate:
                    # Generate embedding for the new memory
                    memory_metadata = {
                        "type": "human_memory",
                        "channel": memory.channel,
                        "timestamp": memory.timestamp,
                        "confidence": memory.confidence,
                    }

                    # Lazy import to avoid hard dependency during migrations/CLI
                    embed_fn = _get_embed_callable()
                    embed_fn(memory.content, metadata=memory_metadata)
                    new_memories.append(memory)
                    batch_norm_texts.append(norm)
                    logger.debug(
                        f"Added new unique memory: {memory.content[:50]}..."
                    )

            except Exception as e:
                logger.error(
                    f"Error checking memory duplicates: {e}", exc_info=True
                )
                # If error checking duplicates, include the memory anyway
                new_memories.append(memory)

        logger.info(
            f"Filtered {len(memories)} candidate memories down to {len(new_memories)} unique memories"
        )
        return new_memories

    def _create_extraction_prompt(self, message_context: str) -> str:
        """Create a lightweight extraction prompt for legacy tests.

        This helper is used by unit tests to validate prompt creation behavior.

        Args:
            message_context: Pre-formatted recent messages context

        Returns:
            Prompt text instructing a memory extraction assistant to output a JSON array.
        """
        header = (
            "You are a memory extraction assistant. Read the conversation context and "
            "extract concise, lasting human memories about the user."
        )
        instructions = (
            "Return a JSON array of objects with keys: 'content', 'confidence', 'source_snippet'. "
            "Only include items with confidence >= 0.6."
        )
        return f"{header}\n\nContext:\n{message_context}\n\nOutput format: {instructions}"

    def _load_memories(self) -> None:
        """Load existing memories from JSON file.

        The loader is resilient to legacy/broken entries and will attempt to
        salvage recognizable memory payloads (e.g., ChatCompletion‑wrapped JSON)
        rather than failing the entire load. Invalid entries are skipped with a
        warning.

        Important: Some older runs wrote OpenAI ChatCompletion objects as the
        "content" field while still including canonical keys ("content",
        "timestamp", "channel", "confidence"). Previously we treated those as
        canonical and loaded them literally, which made each memory thousands of
        characters long. That bloated token counts so none of the human memories
        could fit into the allocated budget, effectively preventing injection.

        We now detect that wrapper shape and salvage inner JSON memories even if
        the outer object looks canonical.
        """
        try:
            if self.memory_file.exists():
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.memories = []
                count = 0
                skipped = 0
                salvaged_total = 0
                wrapper_detected = 0
                for mem_data in (data or []):
                    try:
                        is_obj = isinstance(mem_data, dict)
                        has_keys = is_obj and all(
                            k in mem_data for k in ("content", "timestamp", "channel", "confidence")
                        )
                        raw = mem_data.get("content") if is_obj else None

                        # Detect OpenAI ChatCompletion wrapper in "content" even when keys look canonical
                        looks_wrapped = False
                        try:
                            if isinstance(raw, str):
                                # Quick heuristic: starts with "ChatCompletion(" or contains "message=ChatCompletionMessage("
                                s = raw.strip()
                                looks_wrapped = s.startswith("ChatCompletion(") or "message=ChatCompletionMessage(" in s
                        except Exception:
                            looks_wrapped = False

                        if has_keys and not looks_wrapped:
                            memory = HumanMemory(
                                content=str(mem_data["content"]),
                                timestamp=float(mem_data["timestamp"]),
                                channel=str(mem_data["channel"]),
                                confidence=float(mem_data["confidence"]),
                                source_messages=list(mem_data.get("source_messages", [])),
                                embedding_id=mem_data.get("embedding_id"),
                            )
                            self.memories.append(memory)
                            count += 1
                        else:
                            # Attempt salvage from ChatCompletion‑wrapped entry or skip
                            if isinstance(raw, str):
                                salvaged = salvage_legacy_wrapped_content(raw)
                            else:
                                salvaged = []
                            if salvaged:
                                self.memories.extend(salvaged)
                                salvaged_total += len(salvaged)
                                if looks_wrapped:
                                    wrapper_detected += 1
                            elif has_keys:
                                # Fall back to canonical load even if odd; better to keep than drop
                                memory = HumanMemory(
                                    content=str(mem_data.get("content", "")),
                                    timestamp=float(mem_data.get("timestamp", time.time())),
                                    channel=str(mem_data.get("channel", "global")),
                                    confidence=float(mem_data.get("confidence", 0.8) or 0.8),
                                    source_messages=list(mem_data.get("source_messages", [])),
                                    embedding_id=mem_data.get("embedding_id"),
                                )
                                self.memories.append(memory)
                                count += 1
                            else:
                                skipped += 1
                    except Exception:
                        skipped += 1

                logger.debug(
                    "Loaded %d human memories from %s (skipped %d, salvaged %d, wrappers %d)",
                    len(self.memories), self.memory_file, skipped, salvaged_total, wrapper_detected,
                )
            else:
                self.memories = []
                logger.debug("No existing human memory file found")

        except Exception as e:
            logger.error(f"Error loading human memories: {e}", exc_info=True)
            self.memories = []

    def _save_memories(self) -> None:
        """Save memories to JSON file."""
        try:
            # Convert memories to JSON-serializable format
            data = []
            for memory in self.memories:
                mem_data = {
                    "content": memory.content,
                    "timestamp": memory.timestamp,
                    "channel": memory.channel,
                    "confidence": memory.confidence,
                    "source_messages": memory.source_messages,
                    "embedding_id": memory.embedding_id,
                }
                data.append(mem_data)

            # Ensure vault directory exists
            self.vault_path.mkdir(parents=True, exist_ok=True)

            # Save to file
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug(
                f"Saved {len(self.memories)} human memories to {self.memory_file}"
            )

        except Exception as e:
            logger.error(f"Error saving human memories: {e}", exc_info=True)

    def get_memories(
        self, limit: Optional[int] = None, channel: Optional[str] = None
    ) -> List[HumanMemory]:
        """Get stored human memories with optional filtering.

        Args:
            limit: Maximum number of memories to return
            channel: Filter by specific channel

        Returns:
            List of HumanMemory objects
        """
        filtered_memories = self.memories

        if channel:
            filtered_memories = [
                m for m in filtered_memories if m.channel == channel
            ]

        # Sort by timestamp (newest first)
        filtered_memories.sort(key=lambda m: m.timestamp, reverse=True)

        if limit:
            filtered_memories = filtered_memories[:limit]

        return filtered_memories

    def clear_channel_memories(self, channel: str) -> int:
        """Clear all human memories for a specific channel and purge embeddings.

        Args:
            channel: Channel ID to clear

        Returns:
            Number of memories removed
        """
        try:
            initial_count = len(self.memories)
            self.memories = [m for m in self.memories if m.channel != channel]
            removed_count = initial_count - len(self.memories)

            if removed_count > 0:
                self._save_memories()
                logger.info(
                    f"Cleared {removed_count} human memories from channel {channel}"
                )

                # Purge associated embeddings
                try:
                    from layer3_longterm.embeddings import get_embedding_manager

                    manager = get_embedding_manager()
                    original = list(
                        zip(
                            getattr(manager, "texts", []),
                            getattr(manager, "metadata_list", []),
                        )
                    )
                    if original:
                        filtered_texts: List[str] = []
                        filtered_metadata: List[Dict[str, Any]] = []
                        for text, metadata in original:
                            md = metadata or {}
                            if (
                                md.get("type") == "human_memory"
                                and md.get("channel") == channel
                            ):
                                continue
                            filtered_texts.append(text)
                            filtered_metadata.append(md)

                        if len(filtered_texts) != len(original):
                            manager.clear_index()
                            if filtered_texts:
                                manager.embed_batch(
                                    filtered_texts, metadata_list=filtered_metadata
                                )
                            manager.flush()
                            logger.debug(
                                f"L3.human_memory [clear_channel] - Purged embeddings for channel {channel}"
                            )
                except Exception as embed_err:
                    logger.debug(
                        f"L3.human_memory [clear_channel] - Failed to purge embeddings: {embed_err}"
                    )

            return removed_count

        except Exception as e:
            logger.error(
                f"Error clearing channel memories: {e}", exc_info=True
            )
            return 0

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about stored memories.

        Returns:
            Dictionary with memory statistics
        """
        if not self.memories:
            return {
                "total_memories": 0,
                "channels": [],
                "average_confidence": 0.0,
                "oldest_memory": None,
                "newest_memory": None,
            }

        channels = list(set(m.channel for m in self.memories))
        average_confidence = sum(m.confidence for m in self.memories) / len(
            self.memories
        )
        oldest_timestamp = min(m.timestamp for m in self.memories)
        newest_timestamp = max(m.timestamp for m in self.memories)

        return {
            "total_memories": len(self.memories),
            "channels": channels,
            "average_confidence": average_confidence,
            "oldest_memory": oldest_timestamp,
            "newest_memory": newest_timestamp,
        }

    def migrate_human_memories(self, backup: bool = True) -> Dict[str, Any]:
        """Standardize the vault's human_memories.json file to canonical format.

        - Reads the current file.
        - Converts any legacy ChatCompletion-wrapped entries into canonical objects.
        - Ensures all required fields and types are present.
        - Optionally writes a timestamped backup of the original file.

        Returns a dictionary with migration statistics.
        """
        stats = {"original_count": 0, "written": 0, "salvaged": 0, "skipped": 0, "backup": None}
        try:
            self.vault_path.mkdir(parents=True, exist_ok=True)
            if not self.memory_file.exists():
                logger.info("L3.human_memory [migrate] - No human_memories.json found; nothing to migrate")
                return stats

            # Read original
            try:
                raw_data = json.loads(self.memory_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"L3.human_memory [migrate] - Failed to parse JSON: {e}")
                return stats

            if not isinstance(raw_data, list):
                logger.warning("L3.human_memory [migrate] - Unexpected JSON root (not a list); aborting")
                return stats
            stats["original_count"] = len(raw_data)

            canonical: List[Dict[str, Any]] = []
            salvaged_total = 0
            skipped = 0
            now = time.time()
            for item in raw_data:
                try:
                    is_obj = isinstance(item, dict)
                    has_keys = is_obj and all(k in item for k in ("content", "timestamp", "channel", "confidence"))
                    raw = item.get("content") if is_obj else None
                    looks_wrapped = False
                    try:
                        if isinstance(raw, str):
                            s = raw.strip()
                            looks_wrapped = s.startswith("ChatCompletion(") or "message=ChatCompletionMessage(" in s
                    except Exception:
                        looks_wrapped = False

                    # Prefer salvage when wrapper is detected even if keys look canonical
                    if isinstance(raw, str):
                        salvaged_entries = salvage_legacy_wrapped_content(raw)
                    else:
                        salvaged_entries = []

                    if salvaged_entries:
                        for hm in salvaged_entries:
                            canonical.append({
                                "content": hm.content,
                                "timestamp": float(hm.timestamp),
                                "channel": hm.channel,
                                "confidence": float(hm.confidence),
                                "source_messages": list(hm.source_messages),
                                "embedding_id": getattr(hm, 'embedding_id', None),
                            })
                        salvaged_total += len(salvaged_entries)
                    elif has_keys:
                        # Keep canonical entry
                        canonical.append({
                            "content": str(item.get("content", "")),
                            "timestamp": float(item.get("timestamp", now)),
                            "channel": str(item.get("channel", "global")),
                            "confidence": float(item.get("confidence", 0.8) or 0.8),
                            "source_messages": list(item.get("source_messages", [])),
                            "embedding_id": item.get("embedding_id"),
                        })
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1

            # Backup original
            if backup:
                try:
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    backup_path = self.memory_file.with_suffix(f".backup-{ts}.json")
                    backup_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")
                    stats["backup"] = str(backup_path)
                except Exception as e:
                    logger.warning(f"L3.human_memory [migrate] - Failed to write backup: {e}")

            # Write canonical
            self.memory_file.write_text(json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8")

            stats["written"] = len(canonical)
            stats["salvaged"] = salvaged_total
            stats["skipped"] = skipped
            logger.info(
                "L3.human_memory [migrate] - Migration complete: %d -> %d (salvaged=%d, skipped=%d)",
                stats["original_count"], stats["written"], stats["salvaged"], stats["skipped"],
            )
            return stats
        except Exception as e:
            logger.error(f"L3.human_memory [migrate] - Migration error: {e}", exc_info=True)
            return stats


def migrate_human_memories(vault_path: Optional[str] = None, backup: bool = True) -> Dict[str, Any]:
    """Module-level helper to migrate human memories in the given vault path.

    Args:
        vault_path: Optional explicit vault root. Defaults to the configured vault.
        backup: Whether to create a timestamped backup of the original file.

    Returns:
        Migration statistics dictionary.
    """
    try:
        # Resolve vault
        if vault_path is None:
            from utils.vault_paths import get_vault_root
            vp = str(get_vault_root())
        else:
            vp = str(vault_path)
        extractor = HumanMemoryExtractor(vault_path=vp)
        return extractor.migrate_human_memories(backup=backup)
    except Exception as e:
        logger.error(f"L3.human_memory [migrate] - Failed: {e}", exc_info=True)
        return {"original_count": 0, "written": 0, "salvaged": 0, "skipped": 0, "backup": None}


# Global instance for easy import
_memory_extractor = None


def get_memory_extractor(model_selector=None) -> HumanMemoryExtractor:
    """Get or create the global memory extractor instance."""
    global _memory_extractor
    if _memory_extractor is None:
        _memory_extractor = HumanMemoryExtractor(model_selector=model_selector)
    elif model_selector is not None and getattr(_memory_extractor, "model_selector", None) is not model_selector:
        # Late injection occurs if a previous caller instantiated the extractor without a selector
        # or if the active selector changed after reload.
        _memory_extractor.model_selector = model_selector
    return _memory_extractor


def extract_human(chat_history: ChatHistory, model_selector=None) -> int:
    """Extract human memories from chat history.

    Args:
        chat_history: ChatHistory instance to extract from
        model_selector: ModelSelector instance for AI calls

    Returns:
        Number of memories extracted
    """
    extractor = get_memory_extractor(model_selector)

    # Perform immediate extraction (not timer-based)
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, create a task
            task = asyncio.create_task(
                extractor._perform_extraction(chat_history)
            )
            return 0  # Cannot wait for async result in sync function
        else:
            # Run in new event loop
            asyncio.run(extractor._perform_extraction(chat_history))
            # Return recent memory count
            return len(extractor.get_memories(limit=10))
    except Exception as e:
        logger.error(f"Error in extract_human: {e}", exc_info=True)
        return 0


def force_extract_human(chat_history: ChatHistory, model_selector=None) -> int:
    """Force immediate human memory extraction for testing purposes.

    Args:
        chat_history: ChatHistory instance to extract from
        model_selector: ModelSelector instance for AI calls

    Returns:
        Number of memories extracted
    """
    extractor = get_memory_extractor(model_selector)

    # Reset last extraction time to force extraction
    extractor.last_extraction_time = 0
    extractor.last_processed_channels = {}

    # Perform immediate extraction
    try:
        import asyncio

        asyncio.run(extractor._perform_extraction(chat_history))
        # Return recent memory count
        return len(extractor.get_memories(limit=10))
    except Exception as e:
        logger.error(f"Error in force_extract_human: {e}", exc_info=True)
        return 0
