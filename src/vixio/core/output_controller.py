"""
Output Controller - Output control interfaces (mutually exclusive strategies)

Strategy A: FlowController - Upper layer actively controls send rhythm
  - Suitable for: Custom protocols (e.g., Xiaozhi needs 60ms audio frame intervals)
  - Framework behavior: Calls wait_for_next() in send_worker before sending

Strategy B: PlayoutTracker - Lower layer controls send rhythm
  - Suitable for: WebRTC and other protocols with built-in flow control
  - Framework behavior: Sends directly, waits for on_playout_finished callback
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PlayoutResult:
    """Playout result"""
    playback_position: float  # Actual playback duration (seconds)
    interrupted: bool         # Whether playback was interrupted
    synchronized_transcript: Optional[str] = None  # Synchronized transcript text (optional)


class FlowControllerBase(ABC):
    """
    Strategy A: Flow Controller - Upper layer actively controls send rhythm.
    
    Use cases:
    - Custom WebSocket protocols
    - Precise audio frame interval control (e.g., 60ms)
    - Underlying layer has no built-in flow control
    
    Framework calls in send_worker:
        if isinstance(output_controller, FlowControllerBase):
            await output_controller.wait_for_next()
        await _do_write(data)
    """
    
    @abstractmethod
    async def wait_for_next(self) -> None:
        """
        Wait for next send opportunity.
        
        Example implementation (60ms audio frame interval):
            current = time.monotonic()
            sleep_time = max(0, self._next_send_time - current)
            await asyncio.sleep(sleep_time)
            self._next_send_time = current + 0.06  # 60ms
        """
        pass
    
    def reset(self) -> None:
        """
        Reset flow control state (called when new turn starts).
        
        Subclasses can override to implement reset logic.
        """
        pass
    
    def on_interrupt(self) -> None:
        """
        Called on interrupt.
        
        Subclasses can override to implement interrupt reset logic.
        """
        self.reset()


class PlayoutTrackerBase(ABC):
    """
    Strategy B: Playout Tracker - Lower layer controls send rhythm.
    
    Use cases:
    - WebRTC (automatic flow control)
    - LiveKit (rtc.AudioSource internal control)
    - Scenarios requiring playback completion confirmation
    
    Framework behavior:
    - Calls _do_write() directly (no waiting)
    - Waits for wait_for_playout() callback after sending
    """
    
    def __init__(self):
        self._playout_event = asyncio.Event()
        self._last_result: Optional[PlayoutResult] = None
        self._segment_count = 0
        self._finished_count = 0
    
    @abstractmethod
    async def wait_for_playout(self) -> PlayoutResult:
        """
        Wait for current audio playback to complete.
        
        Returns:
            PlayoutResult: Playback result (duration, interrupted status, etc.)
        """
        pass
    
    def on_playout_finished(
        self, 
        playback_position: float, 
        interrupted: bool,
        synchronized_transcript: Optional[str] = None
    ) -> None:
        """
        Callback when lower layer playback completes.
        
        Developers implementing audio sinks must call this method when playback finishes.
        
        Args:
            playback_position: Actual playback duration (seconds)
            interrupted: Whether playback was interrupted
            synchronized_transcript: Synchronized transcript text (optional)
        """
        self._finished_count += 1
        self._last_result = PlayoutResult(
            playback_position=playback_position,
            interrupted=interrupted,
            synchronized_transcript=synchronized_transcript
        )
        self._playout_event.set()
    
    def flush(self) -> None:
        """
        Mark current segment as complete.
        
        Call this method to indicate current audio segment has been fully sent.
        """
        self._segment_count += 1
    
    def clear(self) -> None:
        """
        Clear buffer (called on interrupt).
        
        Immediately stops playback and clears all pending data.
        """
        self._playout_event.set()  # Wake up waiters
        self._last_result = PlayoutResult(
            playback_position=0,
            interrupted=True
        )


class SimpleFlowController(FlowControllerBase):
    """
    Simple flow controller implementation - Controls send rhythm based on time intervals.
    
    Suitable for scenarios requiring fixed interval audio frame sending.
    """
    
    def __init__(self, frame_duration_ms: int = 60, pre_buffer_count: int = 5):
        """
        Args:
            frame_duration_ms: Frame duration (milliseconds)
            pre_buffer_count: Pre-buffer frame count (frames allowed to send consecutively)
        """
        self._frame_duration_ms = frame_duration_ms
        self._pre_buffer_count = pre_buffer_count
        self._frame_duration_s = frame_duration_ms / 1000.0
        
        self._next_send_time: float = 0.0
        self._frames_sent: int = 0
        self._start_time: Optional[float] = None
    
    async def wait_for_next(self) -> None:
        """Wait for next send opportunity"""
        import time
        
        current_time = time.monotonic()
        
        # First frame or after reset
        if self._start_time is None:
            self._start_time = current_time
            self._frames_sent = 0
            self._next_send_time = current_time
        
        # Calculate expected send time
        expected_time = self._start_time + (self._frames_sent * self._frame_duration_s)
        
        # No waiting during pre-buffer phase
        if self._frames_sent < self._pre_buffer_count:
            self._frames_sent += 1
            return
        
        # Calculate wait time
        wait_time = expected_time - current_time
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        self._frames_sent += 1
    
    def reset(self) -> None:
        """Reset flow control state"""
        self._start_time = None
        self._frames_sent = 0
        self._next_send_time = 0.0


class SimplePlayoutTracker(PlayoutTrackerBase):
    """
    Simple playout tracker implementation.
    
    Basic implementation that waits for on_playout_finished callback.
    """
    
    async def wait_for_playout(self) -> PlayoutResult:
        """Wait for playback to complete"""
        await self._playout_event.wait()
        self._playout_event.clear()
        return self._last_result or PlayoutResult(playback_position=0, interrupted=False)
