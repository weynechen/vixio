"""
ControlBus - centralized control signal management for pipeline interrupts

Design:
- Publish-subscribe pattern for interrupt signals
- Any component can send interrupt signals
- Monitor task manages turn transitions
- All stations check turn_id to discard old data
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time

logger = logging.getLogger(__name__)


@dataclass
class InterruptSignal:
    """
    Interrupt signal sent through ControlBus.
    
    Attributes:
        source: Component that sent the interrupt (e.g., "vad", "turn_detector", "transport")
        reason: Human-readable reason for interrupt
        turn_id: Turn ID when interrupt was sent
        timestamp: When the interrupt was sent
        metadata: Additional context (optional)
    """
    source: str
    reason: str
    turn_id: int
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        return f"InterruptSignal(source={self.source}, reason={self.reason}, turn={self.turn_id})"


class ControlBus:
    """
    Centralized control bus for managing interrupts and turn transitions.
    
    Features:
    - Any component can send interrupt signals
    - Monitor task coordinates turn transitions
    - Provides current turn_id for all components
    - Thread-safe and async-friendly
    
    Usage:
        bus = ControlBus()
        
        # Send interrupt
        await bus.send_interrupt(source="vad", reason="user_speaking")
        
        # Wait for interrupt (used by Session)
        signal = await bus.wait_for_interrupt()
        
        # Get current turn ID (used by Stations)
        turn_id = bus.get_current_turn_id()
    """
    
    def __init__(self):
        """Initialize control bus."""
        self._current_turn_id = 0
        self._interrupt_queue = asyncio.Queue()
        self._interrupt_event = asyncio.Event()
        self._latest_interrupt: Optional[InterruptSignal] = None
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("ControlBus")
    
    def get_current_turn_id(self) -> int:
        """
        Get current turn ID.
        
        Returns:
            Current turn ID
        """
        return self._current_turn_id
    
    async def send_interrupt(
        self,
        source: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Send an interrupt signal.
        
        This can be called from any component (VAD, Turn Detector, Agent, Transport, etc.)
        
        Args:
            source: Component sending the interrupt
            reason: Human-readable reason
            metadata: Additional context (optional)
        """
        signal = InterruptSignal(
            source=source,
            reason=reason,
            turn_id=self._current_turn_id,
            metadata=metadata or {}
        )
        
        self.logger.info(f"Interrupt signal received: {signal}")
        
        # Queue the interrupt for processing
        await self._interrupt_queue.put(signal)
        
        # Set event flag for immediate notification
        self._interrupt_event.set()
    
    async def wait_for_interrupt(self) -> InterruptSignal:
        """
        Wait for the next interrupt signal.
        
        This should be called by Session to handle interrupts.
        
        Returns:
            InterruptSignal with details
        """
        signal = await self._interrupt_queue.get()
        
        # Increment turn ID for new turn
        async with self._lock:
            self._current_turn_id += 1
            signal.turn_id = self._current_turn_id
            self._latest_interrupt = signal
        
        self.logger.info(f"Processing interrupt: {signal}, new turn_id={self._current_turn_id}")
        
        return signal
    
    def check_interrupt_event(self) -> bool:
        """
        Non-blocking check if interrupt event is set.
        
        Returns:
            True if interrupt event is currently set
        """
        return self._interrupt_event.is_set()
    
    async def wait_for_interrupt_event(self) -> None:
        """
        Wait for interrupt event to be set (async).
        
        This is a lighter-weight alternative to wait_for_interrupt()
        for components that just need to know an interrupt occurred.
        """
        await self._interrupt_event.wait()
    
    def clear_interrupt_event(self) -> None:
        """
        Clear the interrupt event flag.
        
        Should be called by Session after handling interrupt.
        """
        self._interrupt_event.clear()
    
    def get_latest_interrupt(self) -> Optional[InterruptSignal]:
        """
        Get the most recent interrupt signal.
        
        Returns:
            Latest InterruptSignal or None if no interrupt yet
        """
        return self._latest_interrupt
    
    async def reset_turn(self) -> int:
        """
        Manually reset to a new turn (used for session start).
        
        Returns:
            New turn ID
        """
        async with self._lock:
            self._current_turn_id += 1
            self.logger.info(f"Turn reset to {self._current_turn_id}")
            return self._current_turn_id
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the control bus.
        
        Returns:
            Dictionary with stats
        """
        return {
            "current_turn_id": self._current_turn_id,
            "pending_interrupts": self._interrupt_queue.qsize(),
            "interrupt_event_set": self._interrupt_event.is_set(),
            "latest_interrupt": str(self._latest_interrupt) if self._latest_interrupt else None
        }
    
    def __str__(self) -> str:
        return f"ControlBus(turn_id={self._current_turn_id})"
    
    def __repr__(self) -> str:
        return self.__str__()

