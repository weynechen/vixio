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
from core.station import Station
from core.chunk import Chunk, ChunkType, TextChunk
from core.middleware import with_middlewares
from stations.middlewares import SignalHandlerMiddleware, ErrorHandlerMiddleware


@with_middlewares(
    # Handle signals (CONTROL_INTERRUPT - clear buffer)
    SignalHandlerMiddleware(
        on_interrupt=lambda: None,  # Will be set in __init__
        cancel_streaming=False
    ),
    # Handle errors
    ErrorHandlerMiddleware(
        emit_error_event=True,
        suppress_errors=False
    )
)
class TextAggregatorStation(Station):
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
                yield TextChunk(
                    type=ChunkType.TEXT,
                    content=self._text_buffer,
                    source=self._source or "aggregator",
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
                
                # Clear buffer
                self._text_buffer = ""
                self._source = ""
            else:
                self.logger.debug("No text to aggregate")
            
            # Passthrough signal
            yield chunk
            return
        
        # Handle other signals (passthrough)
        if chunk.is_signal():
            yield chunk
            return
        
        # Accumulate TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            delta = chunk.delta if hasattr(chunk, 'delta') else str(chunk.data or "")
            
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

