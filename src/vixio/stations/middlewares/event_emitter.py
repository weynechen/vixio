"""
Event Emitter Middleware

Emits events before/after processing (START, STOP, etc.).
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, Dict, Any
from vixio.core.middleware import UniversalMiddleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType, EventChunk


class EventEmitterMiddleware(UniversalMiddleware):
    """
    Emits events before and after processing both data and signal chunks.
    
    Automatically emits:
    - START event before processing
    - STOP event after processing (with success/interrupted status)
    - Custom events based on configuration
    """
    
    def __init__(
        self,
        start_event: Optional[ChunkType] = None,
        stop_event: Optional[ChunkType] = None,
        start_data: Optional[Dict[str, Any]] = None,
        stop_data: Optional[Dict[str, Any]] = None,
        emit_on_interrupt: bool = True,
        name: str = "EventEmitter"
    ):
        """
        Initialize event emitter middleware.
        
        Args:
            start_event: Event type to emit before processing
            stop_event: Event type to emit after processing
            start_data: Additional data for start event
            stop_data: Additional data for stop event
            emit_on_interrupt: Whether to emit stop event if interrupted
            name: Middleware name
        """
        super().__init__(name)
        self.start_event = start_event
        self.stop_event = stop_event
        self.start_data = start_data or {}
        self.stop_data = stop_data or {}
        self.emit_on_interrupt = emit_on_interrupt
    
    async def _emit_events(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Common event emission logic for both data and signal chunks.
        
        Args:
            chunk: Input chunk (data or signal)
            next_handler: Next handler in chain
            
        Yields:
            START event, processed chunks, STOP event
        """
        # Emit START event
        if self.start_event:
            event_data = dict(self.start_data)
            
            # Add input info to start event
            if hasattr(chunk, 'content'):
                event_data['input_preview'] = str(chunk.content)[:100]
            
            yield EventChunk(
                type=self.start_event,
                event_data=event_data,
                source=self.station.name if self.station else "Unknown",
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            self.logger.debug(f"Emitted {self.start_event.name} event")
        
        # Process through chain
        interrupted = False
        output_count = 0
        
        try:
            async for result in next_handler(chunk):
                output_count += 1
                yield result
        except StopAsyncIteration:
            # Normal completion
            pass
        except Exception as e:
            # Check if it's an interruption
            if "interrupted" in str(e).lower() or "cancelled" in str(e).lower():
                interrupted = True
            raise
        
        # Emit STOP event
        if self.stop_event and (not interrupted or self.emit_on_interrupt):
            event_data = dict(self.stop_data)
            event_data['output_count'] = output_count
            event_data['interrupted'] = interrupted
            
            yield EventChunk(
                type=self.stop_event,
                event_data=event_data,
                source=self.station.name if self.station else "Unknown",
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            self.logger.debug(
                f"Emitted {self.stop_event.name} event "
                f"(count={output_count}, interrupted={interrupted})"
            )
    
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process data chunk with event emission.
        
        Args:
            chunk: Data chunk
            next_handler: Next handler in chain
            
        Yields:
            START event, processed chunks, STOP event
        """
        async for result in self._emit_events(chunk, next_handler):
            yield result
    
    async def process_signal(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process signal chunk with event emission.
        
        Args:
            chunk: Signal chunk
            next_handler: Next handler in chain
            
        Yields:
            START event, processed chunks, STOP event
        """
        async for result in self._emit_events(chunk, next_handler):
            yield result

