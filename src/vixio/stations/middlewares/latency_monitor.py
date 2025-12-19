"""
Latency Monitor Middleware

Monitors and records latency metrics (TTFT, etc.).
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, List
from vixio.core.middleware import Middleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType
from vixio.utils import get_latency_monitor


class LatencyMonitorMiddleware(Middleware):
    """
    Monitors latency metrics for station outputs.
    
    Records:
    - TTFT (Time To First Token): Time until first output chunk from this station
    - Only records once per turn_id
    - Only monitors outputs where source matches station name
    - Monitors ALL outputs regardless of input type (data or signal)
    
    Design:
    - Uses turn_id tracking (not set) to minimize memory usage
    - Filters by source to avoid recording passthrough chunks
    - Inherits from Middleware (not DataMiddleware) to monitor outputs from signal inputs
    """
    
    def __init__(
        self,
        record_first_token: bool = True,
        metric_name: str = "first_token",
        output_types: Optional[List[ChunkType]] = None,
        name: str = "LatencyMonitor"
    ):
        """
        Initialize latency monitor middleware.
        
        Args:
            record_first_token: Whether to record first token latency
            metric_name: Name of the latency metric to record
            output_types: List of output chunk types to monitor (None = all types)
            name: Middleware name
        """
        super().__init__(name)
        self.record_first_token = record_first_token
        self.metric_name = metric_name
        self.output_types = output_types
        self._latency_monitor = get_latency_monitor()
        
        # Track last recorded turn to avoid duplicate recording
        # turn_id is monotonically increasing, so we only need to store the last one
        self._last_recorded_turn_id = -1
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Monitor latency for station outputs.
        
        Monitors ALL outputs regardless of input chunk type (data or signal).
        This is important because stations like ASR output data chunks (TEXT_DELTA)
        in response to completion signals.
        
        Only records latency for:
        1. First output of this turn (turn_id > last_recorded_turn_id)
        2. Output from this station (result.source == station.name)
        
        Args:
            chunk: Input chunk (data or signal)
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks
        """
        # Check if we should monitor this turn
        should_monitor_turn = (
            self.record_first_token and 
            chunk.turn_id > self._last_recorded_turn_id
        )
        
        station_name = self.station.name if self.station else None
        
        async for result in next_handler(chunk):
            # Record latency if:
            # 1. This turn hasn't been recorded yet
            # 2. Output source matches station name (this station's output, not passthrough)
            # 3. Output type matches filter (if specified)
            if should_monitor_turn:
                result_source = getattr(result, 'source', None)
                result_type = getattr(result, 'type', None)
                
                # Check if output source matches station
                if result_source == station_name:
                    # Check if output type matches filter (if specified)
                    if self.output_types is None or result_type in self.output_types:
                        self._latency_monitor.record(
                            chunk.session_id,
                            chunk.turn_id,
                            self.metric_name
                        )
                        self.logger.debug(
                            f"✅ Recorded latency: {self.metric_name} for turn {chunk.turn_id} "
                            f"(source={result_source}, type={result_type})"
                        )
                        
                        # Mark this turn as recorded
                        self._last_recorded_turn_id = chunk.turn_id
                        should_monitor_turn = False  # Only record once per turn
            
            yield result

