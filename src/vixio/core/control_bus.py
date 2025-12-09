"""
ControlBus - centralized control signal management for pipeline interrupts

Design:
- Publish-subscribe pattern for interrupt signals
- Any component can send interrupt signals
- Monitor task manages turn transitions
- All stations check turn_id to discard old data
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time
from loguru import logger


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
    
    Turn Management Strategy:
    - turn_id increments immediately when turn completes or is interrupted
    - Components call increment_turn() when:
      * TTS finishes (bot finished speaking)
      * User interrupts (user starts speaking during bot speaking)
    - All subsequent chunks naturally carry the new turn_id
    - No "lazy increment" or "ready to start" states needed
    
    DAG Signal Handling:
    - CONTROL signals (CONTROL_INTERRUPT) go through ControlBus
    - EVENT signals go through DAG data flow
    - Components can register interrupt handlers via register_interrupt_handler()
    
    Features:
    - Any component can send interrupt signals
    - Simple, immediate turn_id increment on completion/interrupt
    - Provides current turn_id for all components
    - Supports handler registration for interrupt callbacks
    - Thread-safe and async-friendly
    
    Usage:
        bus = ControlBus()
        
        # Register interrupt handler
        bus.register_interrupt_handler(my_handler)
        
        # Increment turn when complete/interrupted
        new_turn = await bus.increment_turn(source="TTS", reason="bot_finished")
        
        # Send interrupt signal (triggers handlers + increments turn)
        await bus.send_interrupt(source="TurnDetector", reason="user_interrupted")
        
        # Get current turn ID (used by Stations)
        turn_id = bus.get_current_turn_id()
    """
    
    def __init__(self):
        """Initialize control bus."""
        self._current_turn_id = 0
        self._interrupt_queue = asyncio.Queue()
        self._interrupt_event = asyncio.Event()
        self._latest_interrupt: Optional[InterruptSignal] = None
        self._interrupt_handlers: list = []  # List of async/sync interrupt handlers
        self._lock = asyncio.Lock()
        self.logger = logger.bind(component="ControlBus")
    
    def get_current_turn_id(self) -> int:
        """
        Get current turn ID.
        
        Returns:
            Current turn ID
        """
        return self._current_turn_id
    
    def register_interrupt_handler(self, handler) -> None:
        """
        Register an interrupt handler.
        
        Handlers are called when send_interrupt() is invoked.
        Handlers can be sync or async functions.
        
        Args:
            handler: Callable that takes InterruptSignal as argument
        """
        if handler not in self._interrupt_handlers:
            self._interrupt_handlers.append(handler)
            self.logger.debug(f"Registered interrupt handler: {handler.__name__ if hasattr(handler, '__name__') else handler}")
    
    def unregister_interrupt_handler(self, handler) -> None:
        """
        Unregister an interrupt handler.
        
        Args:
            handler: Previously registered handler
        """
        if handler in self._interrupt_handlers:
            self._interrupt_handlers.remove(handler)
            self.logger.debug(f"Unregistered interrupt handler: {handler.__name__ if hasattr(handler, '__name__') else handler}")
    
    async def send_interrupt(
        self,
        source: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Send an interrupt signal.
        
        This can be called from any component (VAD, Turn Detector, Agent, Transport, etc.)
        
        When called:
        1. Creates InterruptSignal with current turn_id
        2. Notifies all registered handlers
        3. Increments turn_id
        4. Sets interrupt event for waiters
        
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
        
        self.logger.info(f"Interrupt signal: {signal}")
        
        # Notify all registered handlers
        for handler in self._interrupt_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(signal)
                else:
                    handler(signal)
            except Exception as e:
                self.logger.error(f"Error in interrupt handler: {e}")
        
        # Increment turn_id
        await self.increment_turn(source=source, reason=reason)
        
        # Queue the interrupt for session processing
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
        
        # Update signal with current turn info
        async with self._lock:
            signal.turn_id = self._current_turn_id
            self._latest_interrupt = signal
        
        self.logger.info(f"Processing interrupt: {signal}, turn_id={self._current_turn_id}")
        
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
    
    async def increment_turn(self, source: str, reason: str) -> int:
        """
        Increment turn ID (called when turn completes or is interrupted).
        
        Args:
            source: Component incrementing the turn (e.g., "TTS", "TurnDetector")
            reason: Human-readable reason (e.g., "bot_finished", "user_interrupted")
        
        Returns:
            New turn ID
        """
        async with self._lock:
            self._current_turn_id += 1
            self.logger.info(f"Turn incremented to {self._current_turn_id} (source={source}, reason={reason})")
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

