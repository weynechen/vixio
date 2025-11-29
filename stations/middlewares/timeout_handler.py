"""
Timeout Handler Middleware

Handles processing timeouts and emits timeout events.
"""

import asyncio
from typing import AsyncIterator, Optional
from core.middleware import DataMiddleware, NextHandler
from core.chunk import Chunk, ChunkType, EventChunk


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
    
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncIterator[Chunk]:
        """
        Process data chunk with timeout monitoring.
        
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
        
        try:
            async for result in next_handler(chunk):
                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > self.timeout_seconds:
                    self.logger.warning(
                        f"Processing timeout after {elapsed:.1f}s "
                        f"(limit: {self.timeout_seconds}s)"
                    )
                    
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
                    
                    # Stop processing
                    break
                
                yield result
        
        except asyncio.TimeoutError:
            self.logger.error(f"Processing timed out after {self.timeout_seconds}s")
            
            # Send interrupt signal
            if self.send_interrupt_signal and self.station and self.station.control_bus:
                await self.station.control_bus.send_interrupt(
                    source=self.station.name,
                    reason="timeout",
                    metadata={"timeout_seconds": self.timeout_seconds}
                )
            
            # Emit timeout event
            if self.emit_timeout_event:
                yield EventChunk(
                    type=ChunkType.EVENT_TIMEOUT,
                    event_data={
                        "source": self.station.name if self.station else "Unknown",
                        "timeout_seconds": self.timeout_seconds
                    },
                    source=self.station.name if self.station else "Unknown",
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )

