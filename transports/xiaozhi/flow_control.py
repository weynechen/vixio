"""
Audio flow controller for Xiaozhi protocol

Implements flow control strategy for smooth audio playback
"""

import asyncio
import time


class AudioFlowController:
    """
    Audio flow control for smooth playback.
    
    Strategy:
    - First N packets: fast pre-buffering
    - Subsequent packets: timed intervals
    
    This ensures:
    1. Client has enough buffer to start playback quickly
    2. Audio frames are sent at steady rate to avoid network congestion
    """
    
    def __init__(self, pre_buffer_count: int = 5, frame_duration_ms: int = 60):
        """
        Initialize flow controller.
        
        Args:
            pre_buffer_count: Number of packets to send quickly (pre-buffer)
            frame_duration_ms: Frame duration in milliseconds
        """
        self.pre_buffer_count = pre_buffer_count
        self.frame_duration_s = frame_duration_ms / 1000.0
        self.packet_count = 0
        self.start_time = time.time()
    
    async def wait_for_next_frame(self) -> None:
        """
        Wait until next packet should be sent.
        
        Applies flow control timing based on packet count:
        - Pre-buffer phase (first N packets): send immediately
        - Timed phase: maintain steady intervals
        """
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
        """Reset for new audio stream"""
        self.packet_count = 0
        self.start_time = time.time()

