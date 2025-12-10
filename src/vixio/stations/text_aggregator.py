"""
TextAggregatorStation - Aggregates TEXT_DELTA into complete TEXT

Input: TEXT_DELTA (streaming), CompletionSignal (trigger from ASR)
Output: TEXT (aggregated complete text) + CompletionSignal

Completion Contract:
- AWAITS_COMPLETION: True (triggered by ASR's completion signal)
- EMITS_COMPLETION: True (emits completion after aggregation, triggers Agent)

Use case: Aggregate ASR streaming output before sending to Agent.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator
from vixio.core.station import BufferStation, StationRole
from vixio.core.chunk import Chunk, ChunkType, TextChunk, CompletionChunk, CompletionSignal
from vixio.core.middleware import with_middlewares


@with_middlewares(
    # Note: BufferStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - ErrorHandlerMiddleware (error handling)
)
class TextAggregatorStation(BufferStation):
    """
    Text aggregator: Aggregates TEXT_DELTA into complete TEXT.
    
    Input: TEXT_DELTA (streaming), CompletionSignal (trigger)
    Output: TEXT (complete aggregated text) + CompletionSignal
    
    Completion Contract:
    - Awaits completion from ASR (triggers output)
    - Emits completion after aggregation (for downstream if needed)
    
    Workflow:
    1. Accumulate TEXT_DELTA chunks into buffer
    2. On completion signal: Emit complete text as TEXT + completion
    """
    
    # Station role
    ROLE = StationRole.BUFFER
    
    # BufferStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT_DELTA]
    
    # Completion contract: await ASR completion, emit aggregated text + completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = True
    
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
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (TEXT_DELTA)
        - Do NOT passthrough - DAG handles routing to downstream nodes
        - Accumulate text into buffer (output triggered by on_completion)
        
        Core logic:
        - Accumulate TEXT_DELTA chunks into buffer
        - Output is triggered by on_completion() when upstream sends CompletionSignal
        
        Note: SignalHandlerMiddleware handles CONTROL_STATE_RESET (clears buffer via _handle_interrupt)
        """
        # Accumulate TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            # Extract text from data attribute (unified API)
            delta = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
            
            if delta:
                self._text_buffer += delta
                self.logger.debug(f"Accumulated {len(delta)} chars, total: {len(self._text_buffer)} chars")
        
        # Must be async generator (yield nothing if just buffering)
        return
        yield  # Makes this an async generator
    
    async def on_completion(self, signal: CompletionChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion signal from upstream (ASR).
        
        Emits aggregated text as TEXT chunk and completion signal.
        
        Args:
            signal: CompletionChunk from ASR
            
        Yields:
            TEXT chunk + CompletionSignal
        """
        if self._text_buffer.strip():
            self.logger.info(f"Aggregated text: '{self._text_buffer[:50]}...'")
            
            # Emit complete text as TEXT
            yield TextChunk(
                type=ChunkType.TEXT,
                data=self._text_buffer,
                source=self.name,
                session_id=signal.session_id,
                turn_id=signal.turn_id
            )
            
            # Clear buffer
            self._text_buffer = ""
        else:
            self.logger.debug("No text to aggregate - buffer is empty, not emitting TEXT chunk")
        
        # Emit completion signal for downstream (if any)
        yield self.emit_completion(
            session_id=signal.session_id,
            turn_id=signal.turn_id
        )

