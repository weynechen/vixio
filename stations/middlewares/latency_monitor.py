"""
Latency Monitor Middleware

Monitors and records latency metrics (TTFT, etc.).
"""

from typing import AsyncIterator
from core.middleware import Middleware, NextHandler
from core.chunk import Chunk
from utils import get_latency_monitor


class LatencyMonitorMiddleware(Middleware):
    """
    Monitors latency metrics during processing.
    
    Records:
    - TTFT (Time To First Token): Time until first output chunk
    - Total processing time
    - Other custom metrics
    """
    
    def __init__(
        self,
        record_first_token: bool = True,
        metric_name: str = "first_token",
        name: str = "LatencyMonitor"
    ):
        """
        Initialize latency monitor middleware.
        
        Args:
            record_first_token: Whether to record first token latency
            metric_name: Name of the latency metric to record
            name: Middleware name
        """
        super().__init__(name)
        self.record_first_token = record_first_token
        self.metric_name = metric_name
        self._latency_monitor = get_latency_monitor()
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncIterator[Chunk]:
        """
        Process with latency monitoring.
        
        Args:
            chunk: Input chunk
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks
        """
        first_output = True
        
        async for result in next_handler(chunk):
            # Record first token latency
            if first_output and self.record_first_token:
                self._latency_monitor.record(
                    chunk.session_id,
                    chunk.turn_id,
                    self.metric_name
                )
                self.logger.debug(f"Recorded latency metric: {self.metric_name}")
                first_output = False
            
            yield result

