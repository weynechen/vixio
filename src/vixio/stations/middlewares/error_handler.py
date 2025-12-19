"""
Error Handler Middleware

Handles exceptions during processing and emits error events.
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, Callable, Awaitable
from vixio.core.middleware import UniversalMiddleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType, EventChunk


class ErrorHandlerMiddleware(UniversalMiddleware):
    """
    Handles errors during both data and signal processing.
    
    Catches exceptions and:
    - Emits error events
    - Logs errors
    - Optionally calls error callback
    - Can suppress or re-raise exceptions
    """
    
    def __init__(
        self,
        emit_error_event: bool = True,
        suppress_errors: bool = False,
        on_error: Optional[Callable[[Exception], Awaitable[None]]] = None,
        name: str = "ErrorHandler"
    ):
        """
        Initialize error handler middleware.
        
        Args:
            emit_error_event: Whether to emit EVENT_ERROR on exceptions
            suppress_errors: Whether to suppress exceptions (don't re-raise)
            on_error: Optional error callback (async)
            name: Middleware name
        """
        super().__init__(name)
        self.emit_error_event = emit_error_event
        self.suppress_errors = suppress_errors
        self.on_error = on_error
    
    async def _handle_error(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Common error handling logic for both data and signal chunks.
        
        Args:
            chunk: Input chunk
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks or error event
        """
        try:
            async for result in next_handler(chunk):
                yield result
        
        except Exception as e:
            self.logger.error(f"Error during processing: {e}", exc_info=True)
            
            # Call error callback if provided
            if self.on_error:
                try:
                    await self.on_error(e)
                except Exception as callback_error:
                    self.logger.error(f"Error in error callback: {callback_error}")
            
            # Emit error event
            if self.emit_error_event:
                yield EventChunk(
                    type=ChunkType.EVENT_ERROR,
                    event_data={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "source": self.station.name if self.station else "Unknown"
                    },
                    source=self.station.name if self.station else "Unknown",
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
            
            # Re-raise unless suppressed
            if not self.suppress_errors:
                raise
    
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process data chunk with error handling.
        
        Args:
            chunk: Data chunk
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks or error event
        """
        async for result in self._handle_error(chunk, next_handler):
            yield result
    
    async def process_signal(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process signal chunk with error handling.
        
        Args:
            chunk: Signal chunk
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks or error event
        """
        async for result in self._handle_error(chunk, next_handler):
            yield result

