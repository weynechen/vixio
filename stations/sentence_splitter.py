"""
SentenceSplitterStation - Split streaming text into sentences

Input: TEXT_DELTA
Output: TEXT (complete sentences)

Refactored with middleware pattern for clean separation of concerns.
"""

import re
from typing import AsyncIterator, List
from core.station import BufferStation
from core.chunk import Chunk, ChunkType, TextChunk
from core.middleware import with_middlewares
from stations.middlewares import LatencyMonitorMiddleware


class SentenceSplitter:
    """
    Split streaming text into sentences for real-time processing.
    
    Supports Chinese and English sentence boundaries.
    """
    
    # Sentence ending punctuation
    # Chinese: 。！？；
    # English: . ! ? ;
    # Also handle ellipsis: ... …
    SENTENCE_ENDINGS = r'[。！？；.!?;]|\.{3}|…'
    
    # Pattern to detect sentence boundary
    # Sentence ends with punctuation, optionally followed by quotes/brackets
    SENTENCE_PATTERN = re.compile(
        f'({SENTENCE_ENDINGS})(["\']?[）】」』]?)',
        re.UNICODE
    )
    
    def __init__(self, min_sentence_length: int = 5):
        """
        Initialize sentence splitter.
        
        Args:
            min_sentence_length: Minimum characters to consider a valid sentence
        """
        self.min_sentence_length = min_sentence_length
        self.buffer = ""
    
    def add_chunk(self, chunk: str) -> List[str]:
        """
        Add text chunk and extract complete sentences.
        
        Args:
            chunk: New text chunk (delta)
            
        Returns:
            List of complete sentences
        """
        self.buffer += chunk
        sentences = []
        
        # Find all sentence boundaries
        matches = list(self.SENTENCE_PATTERN.finditer(self.buffer))
        
        if not matches:
            # No complete sentence yet
            return []
        
        # Extract complete sentences
        last_end = 0
        for match in matches:
            sentence_end = match.end()
            sentence = self.buffer[last_end:sentence_end].strip()
            
            # Only yield if sentence is long enough
            if len(sentence) >= self.min_sentence_length:
                sentences.append(sentence)
                last_end = sentence_end
        
        # Keep remaining text in buffer
        if last_end > 0:
            self.buffer = self.buffer[last_end:].strip()
        
        return sentences
    
    def flush(self) -> str:
        """
        Get remaining text in buffer as final sentence.
        
        Returns:
            Remaining text (may not end with punctuation)
        """
        remaining = self.buffer.strip()
        self.buffer = ""
        return remaining if len(remaining) >= self.min_sentence_length else ""
    
    def reset(self) -> None:
        """Reset buffer for new conversation."""
        self.buffer = ""


@with_middlewares(
    # Monitor first sentence latency (custom for SentenceSplitter)
    LatencyMonitorMiddleware(
        record_first_token=True,
        metric_name="first_sentence_complete"
    )
    # Note: BufferStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_INTERRUPT)
    # - ErrorHandlerMiddleware (error handling)
)
class SentenceSplitterStation(BufferStation):
    """
    Sentence splitter: Splits streaming text into complete sentences.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete sentences)
    
    This station is useful for feeding complete sentences to TTS
    instead of waiting for the entire response.
    """
    
    # BufferStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT_DELTA]
    """
    Sentence splitter: Splits streaming text into complete sentences.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete sentences)
    
    This station is useful for feeding complete sentences to TTS
    instead of waiting for the entire response.
    """
    
    def __init__(
        self,
        min_sentence_length: int = 5,
        name: str = "SentenceSplitter"
    ):
        """
        Initialize sentence splitter station.
        
        Args:
            min_sentence_length: Minimum sentence length
            name: Station name
        """
        super().__init__(name=name)
        self.min_sentence_length = min_sentence_length
        self._splitter = SentenceSplitter(min_sentence_length)
        
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """Hook to configure middlewares."""
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """Handle interrupt signal - reset splitter."""
        self._splitter.reset()
        self.logger.debug("Sentence splitter reset")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through sentence splitter - CORE LOGIC ONLY.
        
        Middlewares handle: signal processing (CONTROL_INTERRUPT), latency monitoring, error handling.
        
        Core logic:
        - Accumulate TEXT_DELTA chunks and split into sentences
        - On EVENT_AGENT_STOP: Flush remaining buffer as final sentence
        - Passthrough all chunks
        
        Note: SignalHandlerMiddleware handles CONTROL_INTERRUPT (resets splitter via _handle_interrupt)
        Note: LatencyMonitorMiddleware automatically records first sentence output
        """
        # Handle EVENT_AGENT_STOP signal (flush remaining text)
        if chunk.type == ChunkType.EVENT_AGENT_STOP:
            remaining = self._splitter.flush()
            if remaining:
                self.logger.info(f"Flushing final sentence: '{remaining[:50]}...'")
                yield TextChunk(
                    type=ChunkType.TEXT,
                data=remaining,  # ← Use data instead of content
                source="agent",
                    session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
        
            # Passthrough signal
            yield chunk
            return
        
        # Handle other signals (passthrough)
        if chunk.is_signal():
            yield chunk
            return
        
        # Process TEXT_DELTA chunks from agent
        if chunk.type == ChunkType.TEXT_DELTA:
            # Extract text from data attribute (unified API)
            delta = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
            
            if delta and chunk.source == "agent":
                # Add delta to splitter and get complete sentences
                sentences = self._splitter.add_chunk(delta)
                
                # Yield each complete sentence as TEXT chunk
                # Note: LatencyMonitorMiddleware records first sentence automatically
                for sentence in sentences:
                    self.logger.info(f"Complete sentence: '{sentence[:50]}...'")
                    
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        data=sentence,  # ← Use data instead of content
                        source=chunk.source,
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
            
            # Passthrough TEXT_DELTA
            yield chunk
        else:
            # Passthrough all other chunks
            yield chunk

