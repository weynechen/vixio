"""
Session Context - Single owner for all session resources.

Design principles:
1. Single Ownership: All session objects are owned by SessionContext
2. Automatic Cleanup: When async with exits, all objects are released
3. Lifecycle Bound: Resources live as long as the WebSocket connection

Usage:
    async with await SessionContext.create(session_id, dag_factory, transport) as ctx:
        await ctx.run()
    # All resources automatically released here

NOTE: This refactoring is not used in the current implementation.
"""

import asyncio
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Union,
)

from loguru import logger

if TYPE_CHECKING:
    from vixio.core.control_bus import ControlBus
    from vixio.core.dag import CompiledDAG, DAG
    from vixio.core.station import Station
    from vixio.core.transport import TransportBase
    from vixio.core.chunk import Chunk


@dataclass
class SessionQueues:
    """All queues for a session."""
    read: asyncio.Queue = field(default_factory=asyncio.Queue)
    send: asyncio.Queue = field(default_factory=asyncio.Queue)
    priority: asyncio.Queue = field(default_factory=asyncio.Queue)
    video: asyncio.Queue = field(default_factory=asyncio.Queue)


@dataclass 
class SessionTasks:
    """All async tasks for a session."""
    dag_task: Optional[asyncio.Task] = None
    interrupt_task: Optional[asyncio.Task] = None
    read_worker: Optional[asyncio.Task] = None
    send_worker: Optional[asyncio.Task] = None


class SessionContext:
    """
    Container for all session-scoped resources.
    
    When this context exits (via async with), all contained resources 
    are cleaned up and become eligible for garbage collection.
    
    Key design:
    - All session objects are stored HERE, not in separate dictionaries
    - Transport only holds WebSocket connections
    - SessionManager doesn't hold any session resources
    - Cleanup is automatic when async with exits
    """
    
    def __init__(self, session_id: str):
        """
        Initialize session context.
        
        Use SessionContext.create() factory method instead of direct instantiation.
        
        Args:
            session_id: Unique session identifier
        """
        self.session_id = session_id
        self.logger = logger.bind(
            component="SessionContext",
            session_id=session_id[:8] if session_id else "unknown"
        )
        
        # Core components (owned by this context)
        self.control_bus: Optional["ControlBus"] = None
        self.dag: Optional["CompiledDAG"] = None
        self.input_station: Optional["Station"] = None
        self.output_station: Optional["Station"] = None
        
        # Tasks (for tracking, actual tasks managed by Transport)
        self.tasks: SessionTasks = SessionTasks()
        
        # Transport reference (for read/write operations, not owned)
        self._transport: Optional["TransportBase"] = None
        
        # State
        self._running: bool = False
        self._cleanup_done: bool = False
    
    @classmethod
    async def create(
        cls,
        session_id: str,
        dag_factory: Union[Callable[[], "DAG"], Callable[[], Awaitable["DAG"]]],
        transport: "TransportBase",
    ) -> "SessionContext":
        """
        Factory method to create a fully initialized SessionContext.
        
        This ensures all resources are created together and owned by the context.
        Transport manages queues and workers; SessionContext manages DAG lifecycle.
        
        Args:
            session_id: Unique session identifier
            dag_factory: Factory function to create DAG (sync or async)
            transport: Transport instance (for creating stations and I/O)
            
        Returns:
            Fully initialized SessionContext
        """
        from vixio.core.control_bus import ControlBus
        
        ctx = cls(session_id=session_id)
        ctx._transport = transport
        
        # Create ControlBus (owned by context)
        ctx.control_bus = ControlBus()
        
        # Create DAG from factory (support both sync and async)
        if asyncio.iscoroutinefunction(dag_factory):
            dag = await dag_factory()
        else:
            dag = dag_factory()
        
        # Get InputStation from Transport (Transport manages queues)
        ctx.input_station = transport.get_input_station(session_id)
        ctx.input_station.control_bus = ctx.control_bus
        ctx.input_station.set_session_id(session_id)
        
        # Get OutputStation from Transport (Transport manages queues)
        ctx.output_station = transport.get_output_station(session_id)
        
        # Add OutputStation to DAG and compile
        dag.add_node("transport_out", ctx.output_station)
        ctx.dag = dag.compile(control_bus=ctx.control_bus)
        ctx.dag.set_session_id(session_id)
        
        ctx.logger.info(f"SessionContext created with {len(ctx.dag.nodes)} DAG nodes")
        return ctx
    
    async def start_workers(self) -> None:
        """
        Start workers for this session.
        
        Delegates to Transport's start_workers which handles:
        - Queue creation
        - Read/send workers with full functionality
        - Turn management, latency tracking, output control
        """
        if not self._transport:
            raise RuntimeError("Transport not set")
        
        # Let Transport start workers with its full functionality
        # Transport will use its own queues internally
        if hasattr(self._transport, 'start_workers'):
            await self._transport.start_workers(self.session_id)
        
        self.logger.debug("Workers started via Transport")
    
    async def run(self) -> None:
        """
        Run the session (DAG pipeline).
        
        This method blocks until the session ends (normal completion or cancellation).
        """
        if not self.dag or not self.input_station:
            raise RuntimeError("SessionContext not properly initialized")
        
        self._running = True
        
        try:
            # Start interrupt handler
            self.tasks.interrupt_task = asyncio.create_task(
                self._handle_interrupts(),
                name=f"interrupt-{self.session_id[:8]}"
            )
            
            # Notify transport that pipeline is ready (for handshake, etc.)
            if hasattr(self._transport, 'on_pipeline_ready'):
                await self._transport.on_pipeline_ready(self.session_id)
            
            # Run DAG with input stream
            input_stream = self.input_station._generate_chunks()
            async for _ in self.dag.run(input_stream):
                # OutputStation handles the output
                pass
            
            self.logger.info("Session completed normally")
            
        except asyncio.CancelledError:
            self.logger.info("Session cancelled")
            if self.dag:
                self.dag.stop()
            raise
        
        except Exception as e:
            self.logger.error(f"Session error: {e}", exc_info=True)
            raise
    
    async def _handle_interrupts(self) -> None:
        """Handle interrupt signals from ControlBus."""
        try:
            while self._running:
                if not self.control_bus:
                    break
                
                # Wait for interrupt signal
                interrupt = await self.control_bus.wait_for_interrupt()
                if interrupt and self.dag:
                    await self.dag.handle_interrupt(interrupt)
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Interrupt handler error: {e}")
    
    async def cleanup(self) -> None:
        """
        Cleanup all resources.
        
        This method is idempotent - safe to call multiple times.
        Called automatically by __aexit__.
        """
        if self._cleanup_done:
            return
        
        self._cleanup_done = True
        self._running = False
        self.logger.info("Cleaning up session...")
        
        # 1. Cancel all tasks
        all_tasks = [
            self.tasks.dag_task,
            self.tasks.interrupt_task,
            self.tasks.read_worker,
            self.tasks.send_worker,
        ]
        
        for task in all_tasks:
            if task and not task.done():
                task.cancel()
        
        # Wait for tasks to finish
        for task in all_tasks:
            if task:
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        # 2. Cleanup DAG (calls station cleanup methods)
        if self.dag:
            try:
                await self.dag.cleanup()
            except Exception as e:
                self.logger.error(f"DAG cleanup error: {e}")
        
        # 3. Stop Transport workers (handles queue cleanup)
        if self._transport and hasattr(self._transport, 'stop_workers'):
            try:
                await self._transport.stop_workers(self.session_id)
            except Exception as e:
                self.logger.error(f"Transport stop_workers error: {e}")
        
        # 4. Clear all references (help GC)
        self.dag = None
        self.input_station = None
        self.output_station = None
        self.control_bus = None
        self._transport = None
        self.tasks = SessionTasks()
        
        self.logger.info("Session cleaned up")
    
    async def __aenter__(self) -> "SessionContext":
        """Enter async context manager."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager - ensures cleanup."""
        await self.cleanup()
    
    def __repr__(self) -> str:
        return f"SessionContext(session_id={self.session_id[:8]})"
