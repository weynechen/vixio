"""
TextAggregatorStation - Aggregates TEXT_DELTA into complete TEXT

Input: TEXT_DELTA (streaming)
Output: TEXT (aggregated complete text)

This station aggregates streaming text deltas and emits complete text
as a single TEXT chunk when a termination signal is received.

Use case: Aggregate ASR streaming output before sending to Agent.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator
from core.station import BufferStation
from core.chunk import Chunk, ChunkType, TextChunk
from core.middleware import with_middlewares


@with_middlewares(
    # Note: BufferStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_INTERRUPT)
    # - ErrorHandlerMiddleware (error handling)
)
class TextAggregatorStation(BufferStation):
    """
    Text aggregator: Aggregates TEXT_DELTA into complete TEXT.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete aggregated text)
    
    Workflow:
    1. Accumulate TEXT_DELTA chunks
    2. On EVENT_TEXT_COMPLETE: Emit complete text as TEXT
    3. Pass through all other chunks
    """
    
    # BufferStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT_DELTA]
    """
    Text aggregator: Aggregates TEXT_DELTA into complete TEXT.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete aggregated text)
    
    Workflow:
    1. Accumulate TEXT_DELTA chunks
    2. On EVENT_TEXT_COMPLETE: Emit complete text as TEXT
    3. Pass through all other chunks
    """
    
    def __init__(self, name: str = "TextAggregator"):
        """
        Initialize text aggregator station.
        
        Args:
            name: Station name
        """
        super().__init__(name=name)
        self._text_buffer = ""
        self._source = ""  # Remember the source of accumulated text
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """Hook to configure middlewares."""
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """Handle interrupt signal - clear buffer."""
        if self._text_buffer:
            self.logger.debug("Clearing text buffer on interrupt")
            self._text_buffer = ""
            self._source = ""
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through text aggregator - CORE LOGIC ONLY.
        
        Middlewares handle: signal processing (CONTROL_INTERRUPT), error handling.
        
        Core logic:
        - Accumulate TEXT_DELTA chunks into buffer
        - On EVENT_TEXT_COMPLETE: Emit complete TEXT chunk, clear buffer
        - Passthrough all chunks
        
        Note: SignalHandlerMiddleware handles CONTROL_INTERRUPT (clears buffer via _handle_interrupt)
        """
        # Handle EVENT_TEXT_COMPLETE signal (emit aggregated text)
        if chunk.type == ChunkType.EVENT_TEXT_COMPLETE:
            if self._text_buffer.strip():
                self.logger.info(f"Aggregated text: '{self._text_buffer[:50]}...'")
                
                # Emit complete text as TEXT for Agent
            # Use data instead of content (step towards unified Chunk API)
                yield TextChunk(
                    type=ChunkType.TEXT,
                    data=self._text_buffer,  # ← Use data
                    source=self._source or "aggregator",
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
                
                # Clear buffer
                self._text_buffer = ""
                self._source = ""
            else:
                self.logger.debug("No text to aggregate - buffer is empty, not emitting TEXT chunk")
        
            # Passthrough signal
            yield chunk
            return
        
        # Handle other signals (passthrough)
        if chunk.is_signal():
            yield chunk
            return
        
        # Accumulate TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            # Extract text from data attribute (unified API)
            delta = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
            
            if delta:
                self._text_buffer += delta
                
                # Remember source of first chunk
                if not self._source and chunk.source:
                    self._source = chunk.source
                
                self.logger.debug(f"Accumulated {len(delta)} chars, total: {len(self._text_buffer)} chars")
            
            # Passthrough TEXT_DELTA
            yield chunk
        else:
            # Passthrough all other chunks
            yield chunk

