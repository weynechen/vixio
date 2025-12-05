"""
Signal Handler Middleware

Handles signal chunks (CONTROL_INTERRUPT, etc.) and triggers appropriate actions.
"""

import asyncio
from typing import AsyncIterator, Callable, Optional, Awaitable
from vixio.core.middleware import SignalMiddleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType


class SignalHandlerMiddleware(SignalMiddleware):
    """
    Handles signal chunks and triggers callbacks.
    
    Data chunks are passed through unchanged.
    
    Signals trigger side effects like:
    - Resetting state on CONTROL_INTERRUPT
    - Closing streaming tasks
    - Cleaning up resources
    """
    
    def __init__(
        self,
        on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,
        cancel_streaming: bool = True,
        name: str = "SignalHandler"
    ):
        """
        Initialize signal handler middleware.
        
        Args:
            on_interrupt: Callback function to call on interrupt (async)
            cancel_streaming: Whether to cancel active streaming tasks on interrupt
            name: Middleware name
        """
        super().__init__(name)
        self.on_interrupt = on_interrupt
        self.cancel_streaming = cancel_streaming
        self._streaming_task: Optional[asyncio.Task] = None
    
    async def process_signal(self, chunk: Chunk, next_handler: NextHandler) -> AsyncIterator[Chunk]:
        """
        Process signal chunk and passthrough.
        
        Args:
            chunk: Signal chunk (not data)
            next_handler: Next handler in chain
            
        Yields:
            Signal chunk (passed through) and any additional chunks from vixio.core logic
        """
        # Handle CONTROL_INTERRUPT signal
        if chunk.type == ChunkType.CONTROL_INTERRUPT:
            self.logger.info("Received CONTROL_INTERRUPT signal")
            
            # Cancel active streaming if enabled
            if self.cancel_streaming and self._streaming_task is not None:
                try:
                    self._streaming_task.cancel()
                    self.logger.info("Cancelled active streaming task")
                except Exception as e:
                    self.logger.warning(f"Error cancelling stream: {e}")
                finally:
                    self._streaming_task = None
            
            # Call interrupt callback if provided
            if self.on_interrupt:
                try:
                    await self.on_interrupt()
                    self.logger.debug("Interrupt callback executed")
                except Exception as e:
                    self.logger.error(f"Error in interrupt callback: {e}")
        
        # Always pass through to next handler (for all chunks, including signals)
        # This allows core logic to handle signals like EVENT_TURN_END
        async for result in next_handler(chunk):
            yield result
    
    def set_streaming_task(self, task: Optional[asyncio.Task]) -> None:
        """
        Set the current streaming task for cancellation.
        
        Args:
            task: Streaming task to track
        """
        self._streaming_task = task

