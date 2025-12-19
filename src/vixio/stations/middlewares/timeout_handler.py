"""
Timeout Handler Middleware

Handles processing timeouts and emits timeout events.
"""

import asyncio
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional
from vixio.core.middleware import DataMiddleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType, EventChunk


class TimeoutHandlerMiddleware(DataMiddleware):
    """
    Handles data processing timeouts.
    
    Signal chunks are passed through unchanged.
    
    Monitors data processing time and:
    - Interrupts processing if timeout exceeded
    - Emits timeout event
    - Sends interrupt signal via control bus
    """
    
    def __init__(
        self,
        timeout_seconds: Optional[float] = None,
        emit_timeout_event: bool = True,
        send_interrupt_signal: bool = True,
        name: str = "TimeoutHandler"
    ):
        """
        Initialize timeout handler middleware.
        
        Args:
            timeout_seconds: Timeout in seconds (None = no timeout)
            emit_timeout_event: Whether to emit EVENT_TIMEOUT
            send_interrupt_signal: Whether to send interrupt signal via control bus
            name: Middleware name
        """
        super().__init__(name)
        self.timeout_seconds = timeout_seconds
        self.emit_timeout_event = emit_timeout_event
        self.send_interrupt_signal = send_interrupt_signal
    
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process data chunk with timeout monitoring.
        
        Uses asyncio.wait_for to wrap each iteration of the async generator,
        ensuring timeout is enforced even if the handler blocks without yielding.
        
        Args:
            chunk: Data chunk (not signal)
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks or timeout event
        """
        if not self.timeout_seconds:
            # No timeout, just pass through
            async for result in next_handler(chunk):
                yield result
            return
        
        # Monitor processing time
        start_time = asyncio.get_event_loop().time()
        gen = next_handler(chunk)
        
        try:
            while True:
                # Calculate remaining timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                remaining = self.timeout_seconds - elapsed
                
                if remaining <= 0:
                    # Timeout already exceeded
                    raise asyncio.TimeoutError()
                
                try:
                    # Wait for next result with remaining timeout
                    # This ensures timeout works even if generator never yields
                    result = await asyncio.wait_for(
                        gen.__anext__(),
                        timeout=remaining
                    )
                    yield result
                    
                except StopAsyncIteration:
                    # Generator exhausted normally - exit cleanly
                    break
        
        except asyncio.TimeoutError:
            # Timeout occurred
            elapsed = asyncio.get_event_loop().time() - start_time
            self.logger.error(
                f"Processing timed out after {elapsed:.1f}s "
                f"(limit: {self.timeout_seconds}s)"
            )
            
            # Close the generator to cleanup resources
            try:
                await gen.aclose()
            except Exception as e:
                self.logger.warning(f"Error closing generator after timeout: {e}")
            
            # Send interrupt signal via control bus
            if self.send_interrupt_signal and self.station and self.station.control_bus:
                await self.station.control_bus.send_interrupt(
                    source=self.station.name,
                    reason="timeout",
                    metadata={
                        "timeout_seconds": self.timeout_seconds,
                        "elapsed_seconds": elapsed
                    }
                )
            
            # Emit timeout event
            if self.emit_timeout_event:
                yield EventChunk(
                    type=ChunkType.EVENT_TIMEOUT,
                    event_data={
                        "source": self.station.name if self.station else "Unknown",
                        "timeout_seconds": self.timeout_seconds,
                        "elapsed_seconds": elapsed
                    },
                    source=self.station.name if self.station else "Unknown",
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
        
        finally:
            # Ensure generator is closed on any exit path
            try:
                await gen.aclose()
            except Exception:
                pass

