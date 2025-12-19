"""
Interrupt Detector Middleware

Detects interrupts during processing (turn ID changes).
"""

from collections.abc import AsyncIterator, AsyncGenerator
from vixio.core.middleware import DataMiddleware, NextHandler
from vixio.core.chunk import Chunk


class InterruptDetectorMiddleware(DataMiddleware):
    """
    Detects interrupts during data streaming processing.
    
    Signal chunks are passed through unchanged.
    
    Monitors turn ID changes via control bus and stops processing
    when a new turn is detected (indicating user interruption).
    """
    
    def __init__(self, check_interval: int = 1, name: str = "InterruptDetector"):
        """
        Initialize interrupt detector middleware.
        
        Args:
            check_interval: Check turn ID every N chunks (1 = every chunk)
            name: Middleware name
        """
        super().__init__(name)
        self.check_interval = check_interval
        self._chunk_count = 0
    
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process data chunk with interrupt detection.
        
        Args:
            chunk: Data chunk (not signal)
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks (stops if interrupted)
        """
        self._chunk_count = 0
        
        async for result in next_handler(chunk):
            # Check for interrupt periodically
            self._chunk_count += 1
            if self._chunk_count % self.check_interval == 0:
                if self._is_interrupted():
                    self.logger.info(
                        f"Interrupt detected: turn {self.station.current_turn_id} -> "
                        f"{self.station.control_bus.get_current_turn_id()}"
                    )
                    break
            
            yield result
    
    def _is_interrupted(self) -> bool:
        """
        Check if processing has been interrupted.
        
        Returns:
            True if current turn has been superseded by a new turn
        """
        if not self.station or not self.station.control_bus:
            return False
        
        current_turn = self.station.control_bus.get_current_turn_id()
        return current_turn > self.station.current_turn_id

