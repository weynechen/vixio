"""
Error Handler Middleware

Handles exceptions during processing and emits error events.
"""

from typing import AsyncIterator, Optional, Callable, Awaitable
from core.middleware import Middleware, NextHandler
from core.chunk import Chunk, ChunkType, EventChunk


class ErrorHandlerMiddleware(Middleware):
    """
    Handles errors during processing.
    
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
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncIterator[Chunk]:
        """
        Process with error handling.
        
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

