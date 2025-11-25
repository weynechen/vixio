"""
TimeoutMonitor - Monitor overall conversation flow for timeouts

Monitors:
- Agent processing timeout (default: 30s)
- TTS generation timeout (default: 60s)  
- Overall turn timeout (default: 120s)
"""

import asyncio
import logging
from typing import Optional
from dataclasses import dataclass, field
import time

logger = logging.getLogger(__name__)


@dataclass
class TimeoutConfig:
    """
    Configuration for timeout monitoring.
    
    Attributes:
        agent_timeout: Agent processing timeout in seconds
        tts_timeout: TTS generation timeout in seconds
        turn_timeout: Overall turn timeout in seconds
    """
    agent_timeout: float = 30.0
    tts_timeout: float = 60.0
    turn_timeout: float = 120.0


class TimeoutMonitor:
    """
    Monitor conversation flow timeouts and send interrupt signals.
    
    This monitor tracks various stages of conversation processing and sends
    interrupt signals via ControlBus when timeouts occur.
    
    Usage:
        monitor = TimeoutMonitor(control_bus, config)
        
        # Start monitoring a turn
        await monitor.start_turn(session_id)
        
        # Mark stage starts
        await monitor.mark_agent_start(session_id)
        await monitor.mark_tts_start(session_id)
        
        # Mark stage ends
        await monitor.mark_agent_end(session_id)
        await monitor.mark_tts_end(session_id)
        
        # End turn
        await monitor.end_turn(session_id)
    """
    
    def __init__(self, control_bus, config: Optional[TimeoutConfig] = None):
        """
        Initialize timeout monitor.
        
        Args:
            control_bus: ControlBus instance for sending interrupt signals
            config: Timeout configuration (defaults to TimeoutConfig())
        """
        self.control_bus = control_bus
        self.config = config or TimeoutConfig()
        self.logger = logging.getLogger("TimeoutMonitor")
        
        # Track active timeouts
        self._turn_tasks = {}  # session_id -> turn timeout task
        self._agent_tasks = {}  # session_id -> agent timeout task
        self._tts_tasks = {}  # session_id -> tts timeout task
        
        # Track timestamps
        self._turn_start_times = {}  # session_id -> start timestamp
        self._agent_start_times = {}  # session_id -> start timestamp
        self._tts_start_times = {}  # session_id -> start timestamp
    
    async def start_turn(self, session_id: str) -> None:
        """
        Start monitoring a turn.
        
        Args:
            session_id: Session identifier
        """
        self.logger.debug(f"[{session_id[:8]}] Starting turn timeout monitoring")
        
        # Cancel any existing turn timeout
        await self._cancel_task(self._turn_tasks, session_id)
        
        # Record start time
        self._turn_start_times[session_id] = time.time()
        
        # Start turn timeout task
        task = asyncio.create_task(
            self._monitor_turn_timeout(session_id),
            name=f"turn-timeout-{session_id[:8]}"
        )
        self._turn_tasks[session_id] = task
    
    async def end_turn(self, session_id: str) -> None:
        """
        End turn monitoring.
        
        Args:
            session_id: Session identifier
        """
        self.logger.debug(f"[{session_id[:8]}] Ending turn timeout monitoring")
        
        # Cancel all timeout tasks for this session
        await self._cancel_task(self._turn_tasks, session_id)
        await self._cancel_task(self._agent_tasks, session_id)
        await self._cancel_task(self._tts_tasks, session_id)
        
        # Clean up timestamps
        self._turn_start_times.pop(session_id, None)
        self._agent_start_times.pop(session_id, None)
        self._tts_start_times.pop(session_id, None)
    
    async def mark_agent_start(self, session_id: str) -> None:
        """
        Mark agent processing start.
        
        Args:
            session_id: Session identifier
        """
        self.logger.debug(f"[{session_id[:8]}] Starting agent timeout monitoring")
        
        # Cancel any existing agent timeout
        await self._cancel_task(self._agent_tasks, session_id)
        
        # Record start time
        self._agent_start_times[session_id] = time.time()
        
        # Start agent timeout task
        task = asyncio.create_task(
            self._monitor_agent_timeout(session_id),
            name=f"agent-timeout-{session_id[:8]}"
        )
        self._agent_tasks[session_id] = task
    
    async def mark_agent_end(self, session_id: str) -> None:
        """
        Mark agent processing end.
        
        Args:
            session_id: Session identifier
        """
        self.logger.debug(f"[{session_id[:8]}] Ending agent timeout monitoring")
        
        # Cancel agent timeout task
        await self._cancel_task(self._agent_tasks, session_id)
        self._agent_start_times.pop(session_id, None)
    
    async def mark_tts_start(self, session_id: str) -> None:
        """
        Mark TTS generation start.
        
        Args:
            session_id: Session identifier
        """
        self.logger.debug(f"[{session_id[:8]}] Starting TTS timeout monitoring")
        
        # Cancel any existing TTS timeout
        await self._cancel_task(self._tts_tasks, session_id)
        
        # Record start time
        self._tts_start_times[session_id] = time.time()
        
        # Start TTS timeout task
        task = asyncio.create_task(
            self._monitor_tts_timeout(session_id),
            name=f"tts-timeout-{session_id[:8]}"
        )
        self._tts_tasks[session_id] = task
    
    async def mark_tts_end(self, session_id: str) -> None:
        """
        Mark TTS generation end.
        
        Args:
            session_id: Session identifier
        """
        self.logger.debug(f"[{session_id[:8]}] Ending TTS timeout monitoring")
        
        # Cancel TTS timeout task
        await self._cancel_task(self._tts_tasks, session_id)
        self._tts_start_times.pop(session_id, None)
    
    async def _monitor_turn_timeout(self, session_id: str) -> None:
        """
        Monitor overall turn timeout.
        
        Args:
            session_id: Session identifier
        """
        try:
            await asyncio.sleep(self.config.turn_timeout)
            
            # Timeout occurred
            elapsed = time.time() - self._turn_start_times.get(session_id, time.time())
            self.logger.warning(
                f"[{session_id[:8]}] Turn timeout after {elapsed:.1f}s "
                f"(limit: {self.config.turn_timeout}s)"
            )
            
            # Send interrupt signal
            await self.control_bus.send_interrupt(
                source="TimeoutMonitor",
                reason="turn_timeout",
                metadata={
                    "session_id": session_id,
                    "timeout_seconds": self.config.turn_timeout,
                    "elapsed_seconds": elapsed
                }
            )
        
        except asyncio.CancelledError:
            # Normal cancellation
            pass
    
    async def _monitor_agent_timeout(self, session_id: str) -> None:
        """
        Monitor agent processing timeout.
        
        Args:
            session_id: Session identifier
        """
        try:
            await asyncio.sleep(self.config.agent_timeout)
            
            # Timeout occurred
            elapsed = time.time() - self._agent_start_times.get(session_id, time.time())
            self.logger.warning(
                f"[{session_id[:8]}] Agent timeout after {elapsed:.1f}s "
                f"(limit: {self.config.agent_timeout}s)"
            )
            
            # Send interrupt signal
            await self.control_bus.send_interrupt(
                source="TimeoutMonitor",
                reason="agent_timeout",
                metadata={
                    "session_id": session_id,
                    "timeout_seconds": self.config.agent_timeout,
                    "elapsed_seconds": elapsed
                }
            )
        
        except asyncio.CancelledError:
            # Normal cancellation
            pass
    
    async def _monitor_tts_timeout(self, session_id: str) -> None:
        """
        Monitor TTS generation timeout.
        
        Args:
            session_id: Session identifier
        """
        try:
            await asyncio.sleep(self.config.tts_timeout)
            
            # Timeout occurred
            elapsed = time.time() - self._tts_start_times.get(session_id, time.time())
            self.logger.warning(
                f"[{session_id[:8]}] TTS timeout after {elapsed:.1f}s "
                f"(limit: {self.config.tts_timeout}s)"
            )
            
            # Send interrupt signal
            await self.control_bus.send_interrupt(
                source="TimeoutMonitor",
                reason="tts_timeout",
                metadata={
                    "session_id": session_id,
                    "timeout_seconds": self.config.tts_timeout,
                    "elapsed_seconds": elapsed
                }
            )
        
        except asyncio.CancelledError:
            # Normal cancellation
            pass
    
    async def _cancel_task(self, task_dict: dict, session_id: str) -> None:
        """
        Cancel a timeout task.
        
        Args:
            task_dict: Dictionary of tasks
            session_id: Session identifier
        """
        if session_id in task_dict:
            task = task_dict[session_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del task_dict[session_id]
    
    async def cleanup(self) -> None:
        """Cleanup all monitoring tasks."""
        self.logger.info("Cleaning up timeout monitor")
        
        # Cancel all tasks
        all_tasks = (
            list(self._turn_tasks.values()) +
            list(self._agent_tasks.values()) +
            list(self._tts_tasks.values())
        )
        
        for task in all_tasks:
            if not task.done():
                task.cancel()
        
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Clear all state
        self._turn_tasks.clear()
        self._agent_tasks.clear()
        self._tts_tasks.clear()
        self._turn_start_times.clear()
        self._agent_start_times.clear()
        self._tts_start_times.clear()

