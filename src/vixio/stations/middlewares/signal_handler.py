"""
Signal Handler Middleware

Handles interrupt signals via ControlBus subscription (DAG architecture).

DAG Signal Handling:
- CONTROL signals (CONTROL_STATE_RESET) come from ControlBus, not data stream
- EVENT signals come from DAG data stream
- This middleware subscribes to ControlBus when attached to a Station
"""

import asyncio
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Callable, Optional, Awaitable, TYPE_CHECKING
from vixio.core.middleware import SignalMiddleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType

if TYPE_CHECKING:
    from vixio.core.station import Station
    from vixio.core.control_bus import InterruptSignal


class SignalHandlerMiddleware(SignalMiddleware):
    """
    Handles interrupt signals via ControlBus subscription.
    
    DAG architecture:
    - CONTROL signals (CONTROL_STATE_RESET) come from ControlBus
    - This middleware subscribes to ControlBus via register_interrupt_handler()
    - EVENT signals still flow through DAG data stream
    
    Triggers side effects like:
    - Resetting state on CONTROL_STATE_RESET
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
        self._station: Optional["Station"] = None
    
    def attach(self, station: "Station") -> None:
        """
        Attach middleware to a station and subscribe to ControlBus.
        
        Called by Station when middleware is configured.
        
        Args:
            station: Station this middleware is attached to
        """
        super().attach(station)
        self._station = station
        
        # Subscribe to ControlBus for interrupt signals
        if station.control_bus:
            station.control_bus.register_interrupt_handler(self._handle_controlbus_interrupt)
            self.logger.debug(f"Subscribed to ControlBus for {station.name}")
    
    async def _handle_controlbus_interrupt(self, signal: "InterruptSignal") -> None:
        """
        Handle interrupt signal from ControlBus.
        
        Args:
            signal: InterruptSignal from ControlBus
        """
        self.logger.debug(f"Received interrupt from ControlBus: {signal}")
        
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
    
    async def process_signal(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process signal chunk.
        
        DAG architecture:
        - CONTROL_STATE_RESET signals now come from ControlBus, not data stream
        - This method handles EVENT signals that flow through DAG
        
        Args:
            chunk: Signal chunk (not data)
            next_handler: Next handler in chain
            
        Yields:
            Signal chunk and any additional chunks from core logic
        """
        # CONTROL_STATE_RESET is now handled via ControlBus subscription
        # But still support legacy data stream path for backward compatibility
        if chunk.type == ChunkType.CONTROL_STATE_RESET:
            self.logger.debug("CONTROL_STATE_RESET received via data stream (legacy path)")
            
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
        
        # Pass to next handler (for all signals including EVENT_*)
        async for result in next_handler(chunk):
            yield result
    
    def set_streaming_task(self, task: Optional[asyncio.Task]) -> None:
        """
        Set the current streaming task for cancellation.
        
        Args:
            task: Streaming task to track
        """
        self._streaming_task = task

