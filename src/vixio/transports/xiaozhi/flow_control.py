"""
Audio flow controller for Xiaozhi protocol

Implements flow control strategy for smooth audio playback
"""

import asyncio
import time
from typing import Optional

from vixio.core.output_controller import FlowControllerBase


class AudioFlowController(FlowControllerBase):
    """
    Xiaozhi audio flow controller - Upper layer actively controls send rhythm.
    
    Strategy:
    - First N packets: Fast pre-buffering (no wait)
    - Subsequent packets: Send at timed intervals
    
    This ensures:
    1. Client has enough buffer to start playback quickly
    2. Audio frames are sent at steady rate to avoid network congestion
    """
    
    def __init__(self, pre_buffer_count: int = 5, frame_duration_ms: int = 60):
        """
        Initialize flow controller.
        
        Args:
            pre_buffer_count: Number of packets for fast pre-buffering
            frame_duration_ms: Frame duration (milliseconds)
        """
        self.pre_buffer_count = pre_buffer_count
        self.frame_duration_s = frame_duration_ms / 1000.0
        self.packet_count = 0
        self.start_time: Optional[float] = None
    
    async def wait_for_next(self) -> None:
        """
        Wait for next send opportunity.
        
        Apply flow control based on packet count:
        - Pre-buffer phase (first N packets): Send immediately
        - Timed phase: Maintain steady intervals
        """
        # First send, record start time
        if self.start_time is None:
            self.start_time = time.time()
        
        if self.packet_count < self.pre_buffer_count:
            # Pre-buffer: send immediately
            pass
        else:
            # Timed: maintain intervals
            effective_packet = self.packet_count - self.pre_buffer_count
            expected_time = self.start_time + (effective_packet * self.frame_duration_s)
            delay = expected_time - time.time()
            
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # Timing drift correction
                self.start_time += abs(delay)
        
        self.packet_count += 1
    
    def reset(self) -> None:
        """Reset flow control state (called when new turn starts)"""
        self.packet_count = 0
        self.start_time = None
    
    # Legacy interface compatibility
    async def wait_for_next_frame(self) -> None:
        """Legacy interface - please use wait_for_next()"""
        await self.wait_for_next()
