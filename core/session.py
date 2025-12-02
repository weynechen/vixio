"""
Session manager - connects Transport to Pipelines

Responsibilities:
1. Listen for new connections from Transport
2. Create a Pipeline instance for each connection
3. Route chunks: Transport input -> Pipeline -> Transport output
4. Manage pipeline lifecycle
5. Handle interrupts via ControlBus
"""

import asyncio
from typing import Callable, Dict, Optional, AsyncIterator, TYPE_CHECKING
from core.transport import TransportBase
from core.pipeline import Pipeline
from core.control_bus import ControlBus
from core.chunk import Chunk, ChunkType
from loguru import logger

if TYPE_CHECKING:
    from core.station import Station


class SessionManager:
    """
    Session manager - connects Transport to Pipelines.
    
    Responsibilities:
    1. Listen for new connections from Transport
    2. Create a Pipeline instance for each connection
    3. Route chunks: Transport input -> Pipeline -> Transport output
    4. Manage pipeline lifecycle
    5. Handle interrupts via ControlBus
    
    Design:
    - Each connection gets its own Pipeline and ControlBus instance
    - Pipeline runs as long as connection is alive
    - ControlBus handles interrupts and turn transitions
    - Automatically cleanup on disconnect
    """
    
    def __init__(
        self,
        transport: TransportBase,
        pipeline_factory: Callable[[], Pipeline]
    ):
        """
        Initialize session manager.
        
        Args:
            transport: Transport to manage connections for
            pipeline_factory: Factory function (sync or async) that creates a fresh Pipeline for each connection
        """
        self.transport = transport
        self.pipeline_factory = pipeline_factory
        self._is_async_factory = asyncio.iscoroutinefunction(pipeline_factory)
        self._sessions: Dict[str, asyncio.Task] = {}  # connection_id -> pipeline task
        self._control_buses: Dict[str, ControlBus] = {}  # connection_id -> control bus
        self._pipelines: Dict[str, Pipeline] = {}  # connection_id -> pipeline
        self._input_stations: Dict[str, 'Station'] = {}  # connection_id -> input station
        self._interrupt_tasks: Dict[str, asyncio.Task] = {}  # connection_id -> interrupt handler task
        self.logger = logger.bind(component="SessionManager")
    
    async def start(self) -> None:
        """
        Start the session manager.
        
        This registers connection handler and starts the transport.
        """
        self.logger.info("Starting session manager...")
        
        # Register for new connections (sync method)
        self.transport.on_new_connection(self._handle_connection)
        
        # Register disconnect handler to cancel sessions
        # Support both new interface on_disconnect and legacy interface set_disconnect_handler
        if hasattr(self.transport, 'on_disconnect'):
            self.transport.on_disconnect(self.cancel_session)
        elif hasattr(self.transport, 'set_disconnect_handler'):
            self.transport.set_disconnect_handler(self.cancel_session)
        
        # Start transport server
        await self.transport.start()
        
        self.logger.info("Session manager started")
    
    async def stop(self) -> None:
        """
        Stop the session manager.
        
        This cancels all active pipelines, interrupt handlers, and stops transport.
        """
        self.logger.info("Stopping session manager...")
        
        # Cancel all interrupt handler tasks
        for connection_id, task in self._interrupt_tasks.items():
            self.logger.debug(f"Cancelling interrupt handler for connection {connection_id[:8]}")
            task.cancel()
        
        # Cancel all pipeline tasks
        for connection_id, task in self._sessions.items():
            self.logger.debug(f"Cancelling session for connection {connection_id[:8]}")
            task.cancel()
        
        # Wait for all to finish
        all_tasks = list(self._sessions.values()) + list(self._interrupt_tasks.values())
        if all_tasks:
            self.logger.info(f"Waiting for {len(all_tasks)} tasks to finish...")
            await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Stop transport
        await self.transport.stop()
        
        self.logger.info("Session manager stopped")
    
    async def _handle_connection(self, connection_id: str) -> None:
        """
        Handle new client connection.
        
        Creates pipeline, control bus, and interrupt handler for this connection.
        Auto-adds InputStation and OutputStation to Pipeline.
        
        Args:
            connection_id: Unique identifier for the connection
        """
        self.logger.info(f"New connection: {connection_id[:8]}")
        
        # Create ControlBus for this session
        control_bus = ControlBus()
        self._control_buses[connection_id] = control_bus
        
        # Inject ControlBus into Transport (for turn_id management)
        self.transport.set_control_bus(connection_id, control_bus)
        self.logger.debug(f"ControlBus set for connection {connection_id[:8]}")
        
        # Start Transport workers (read_worker + send_worker)
        # This starts framework-level common capabilities (Turn management, Latency tracking, etc.)
        if hasattr(self.transport, 'start_workers'):
            await self.transport.start_workers(connection_id)
            self.logger.debug(f"Transport workers started for connection {connection_id[:8]}")
        
        # Create fresh business pipeline (support both sync and async factories)
        if self._is_async_factory:
            business_pipeline = await self.pipeline_factory()
        else:
            business_pipeline = self.pipeline_factory()
        
        # Get InputStation and OutputStation from Transport
        input_station = self.transport.get_input_station(connection_id)
        output_station = self.transport.get_output_station(connection_id)
        
        # Assemble complete Pipeline (only OutputStation is added, InputStation is the data source)
        full_stations = business_pipeline.stations + [output_station]
        
        # Create complete Pipeline with ControlBus
        pipeline = Pipeline(
            stations=full_stations,
            control_bus=control_bus,
            session_id=connection_id
        )
        self._pipelines[connection_id] = pipeline
        
        # Store InputStation separately (it's not part of the pipeline, it's the data source)
        self._input_stations[connection_id] = input_station
        
        # Set ControlBus for InputStation manually (since it's not in the pipeline)
        input_station.control_bus = control_bus
        input_station.set_session_id(connection_id)
        
        self.logger.debug(f"Created complete pipeline for connection {connection_id[:8]} with {len(full_stations)} stations (InputStation as source)")
        
        # Start interrupt handler task
        interrupt_task = asyncio.create_task(
            self._handle_interrupts(connection_id, pipeline, control_bus),
            name=f"interrupt-handler-{connection_id[:8]}"
        )
        self._interrupt_tasks[connection_id] = interrupt_task
        
        # Notify transport that pipeline is ready (so it can send handshake signal)
        await self.transport.on_pipeline_ready(connection_id)
        
        # Run pipeline in background task (InputStation provides the data stream)
        task = asyncio.create_task(
            self._run_pipeline(connection_id, pipeline),
            name=f"pipeline-{connection_id[:8]}"
        )
        
        self._sessions[connection_id] = task
        
        # Cleanup when done
        task.add_done_callback(lambda _: self._cleanup_session(connection_id))
    
    async def _handle_interrupts(
        self,
        connection_id: str,
        pipeline: Pipeline,
        control_bus: ControlBus
    ) -> None:
        """
        Handle interrupt signals for a connection.
        
        Listens for interrupts on ControlBus and:
        1. Clears pipeline queues (except input queue)
        2. Cancels slow tasks (Agent, TTS)
        3. Logs the interrupt
        
        Args:
            connection_id: Connection ID
            pipeline: Pipeline instance
            control_bus: ControlBus instance
        """
        self.logger.info(f"Starting interrupt handler for connection {connection_id[:8]}")
        
        try:
            while True:
                # Wait for interrupt signal
                interrupt = await control_bus.wait_for_interrupt()
                
                self.logger.warning(
                    f"[{connection_id[:8]}] Interrupt received: {interrupt.source} - {interrupt.reason}"
                )
                
                # Increment turn FIRST to invalidate old chunks (unified handling for all interrupts)
                # This ensures any streaming output from Agent/TTS with old turn_id gets discarded
                old_turn = control_bus.get_current_turn_id()
                new_turn = await control_bus.increment_turn(
                    source="SessionManager", 
                    reason=interrupt.reason
                )
                self.logger.info(
                    f"[{connection_id[:8]}] Turn incremented {old_turn} -> {new_turn} due to {interrupt.reason}"
                )
                
                # Clear pipeline queues (keep input queue)
                # This removes pending chunks from all queues
                pipeline.clear_queues(from_stage=1)
                
                # Tasks continue running and will automatically discard old chunks
                # based on turn_id. This ensures they can process new chunks.
                
                # Inject CONTROL_INTERRUPT into input queue for stations to reset state
                # This must be done after clear_queues to ensure it's not cleared
                try:
                    from core.chunk import ControlChunk, ChunkType
                    
                    interrupt_chunk = ControlChunk(
                        type=ChunkType.CONTROL_INTERRUPT,
                        command="interrupt",
                        params={"reason": interrupt.reason, "interrupt_source": interrupt.source},
                        source="SessionManager", 
                        session_id=connection_id,
                        turn_id=new_turn
                    )
                    
                    # Put into input queue (queue[0]) for stations to process
                    if pipeline.queues and len(pipeline.queues) > 0:
                        await pipeline.queues[0].put(interrupt_chunk)
                        self.logger.debug(f"Injected CONTROL_INTERRUPT into pipeline (turn={new_turn})")
                    
                except Exception as e:
                    self.logger.warning(f"Failed to inject CONTROL_INTERRUPT: {e}")
                

                # Send CONTROL_INTERRUPT to OutputStation for immediate client notification
                # OutputStation will convert it to TTS_STOP + STATE_LISTENING protocol messages
                try:
                    from core.chunk import ControlChunk, ChunkType
                    
                    # Create interrupt chunk for OutputStation
                    output_interrupt = ControlChunk(
                        type=ChunkType.CONTROL_ABORT,
                        command="interrupt",
                        params={"reason": interrupt.reason, "source": "SessionManager"},
                        source="SessionManager",
                        session_id=connection_id,
                        turn_id=new_turn
                    )
                    
                    # Inject into OutputStation's input queue for immediate sending
                    # Pipeline has n+1 queues for n stations:
                    # - Queue[0-n-1]: between stations
                    # - Queue[n]: OutputStation's output (pipeline output)
                    # So OutputStation's input is Queue[n-1] (or queues[-2])
                    if pipeline.queues and len(pipeline.queues) >= 2:
                        output_station_input_queue = pipeline.queues[-2]
                        await output_station_input_queue.put(output_interrupt)
                        self.logger.info(
                            f"[{connection_id[:8]}] Injected CONTROL_ABORT to OutputStation"
                        )
                    else:
                        self.logger.warning(
                            f"[{connection_id[:8]}] Cannot inject CONTROL_ABORT: "
                            f"queues={len(pipeline.queues) if pipeline.queues else 0}, "
                            f"stations={len(pipeline.stations)}"
                        )
                        
                except Exception as e:
                    self.logger.warning(f"Failed to inject CONTROL_ABORT: {e}", exc_info=True)
                
                # Clear interrupt event flag
                control_bus.clear_interrupt_event()
                
                self.logger.info(
                    f"[{connection_id[:8]}] Interrupt handled, new turn_id={control_bus.get_current_turn_id()}"
                )
        
        except asyncio.CancelledError:
            self.logger.info(f"Interrupt handler cancelled for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error in interrupt handler for connection {connection_id[:8]}: {e}",
                exc_info=True
            )
    
    async def _run_pipeline(self, connection_id: str, pipeline: Pipeline) -> None:
        """
        Run pipeline for a connection.
        
        Flow:
        1. Get input stream from InputStation (via _generate_chunks())
        2. Wrap input stream to detect CONTROL_INTERRUPT chunks
        3. Run pipeline (all chunks flow through business stations and OutputStation)
        
        Args:
            connection_id: Connection ID to run pipeline for
            pipeline: Pipeline instance to run
        """
        self.logger.info(f"Starting pipeline for connection {connection_id[:8]}")
        
        try:
            # Get InputStation from stored instances
            input_station = self._input_stations.get(connection_id)
            if not input_station:
                self.logger.error(f"InputStation not found for connection {connection_id[:8]}")
                return
            
            # Get input stream from InputStation (acts as data source)
            raw_input_stream = input_station._generate_chunks()
            
            # Get ControlBus for this connection
            control_bus = self._control_buses.get(connection_id)
            
            # Wrap input stream to detect CONTROL_INTERRUPT and send to ControlBus
            input_stream = self._wrap_input_stream(raw_input_stream, control_bus, connection_id)
            
            # Run pipeline (OutputStation handles sending)
            chunk_count = 0
            async for _ in pipeline.run(input_stream):
                # OutputStation already handles output, nothing to do here
                chunk_count += 1
            
            self.logger.info(f"Pipeline completed for connection {connection_id[:8]}, processed {chunk_count} chunks")
        
        except asyncio.CancelledError:
            # Session cancelled (normal shutdown)
            self.logger.info(f"Pipeline cancelled for connection {connection_id[:8]}")
        
        except Exception as e:
            # Pipeline error
            self.logger.error(f"Pipeline error for connection {connection_id[:8]}: {e}", exc_info=True)
        
        finally:
            # Ensure cleanup
            self._cleanup_session(connection_id)
    
    async def _wrap_input_stream(
        self,
        input_stream: AsyncIterator[Chunk],
        control_bus: Optional[ControlBus],
        connection_id: str
    ) -> AsyncIterator[Chunk]:
        """
        Wrap input stream to detect CONTROL_INTERRUPT chunks and send to ControlBus.
        
        This allows client-initiated interrupts (abort) to trigger the interrupt mechanism.
        
        Args:
            input_stream: Raw input stream from transport
            control_bus: ControlBus instance for this connection
            connection_id: Connection identifier
            
        Yields:
            Chunks from input stream (CONTROL_INTERRUPT chunks still passed through)
        """
        async for chunk in input_stream:
            # Detect CONTROL_INTERRUPT chunk and send to ControlBus
            if chunk.type == ChunkType.CONTROL_INTERRUPT and control_bus:
                self.logger.info(
                    f"[{connection_id[:8]}] Client interrupt detected, sending to ControlBus"
                )
                
                # Send interrupt signal to ControlBus
                await control_bus.send_interrupt(
                    source="Transport",
                    reason=chunk.params.get("reason", "client_interrupt"),
                    metadata={
                        "connection_id": connection_id,
                        "command": chunk.command
                    }
                )
            
            # Always yield the chunk (so pipeline can process it too)
            yield chunk
    
    def _cleanup_session(self, connection_id: str) -> None:
        """
        Cleanup session resources.
        
        Args:
            connection_id: Connection to cleanup
        """
        self.logger.debug(f"Cleaning up session for connection {connection_id[:8]}")
        
        # Cancel interrupt handler
        if connection_id in self._interrupt_tasks:
            task = self._interrupt_tasks[connection_id]
            if not task.done():
                task.cancel()
            del self._interrupt_tasks[connection_id]
        
        # Cleanup pipeline resources (including provider cleanup)
        if connection_id in self._pipelines:
            pipeline = self._pipelines[connection_id]
            # Schedule async cleanup in background
            asyncio.create_task(
                self._cleanup_pipeline(connection_id, pipeline),
                name=f"cleanup-pipeline-{connection_id[:8]}"
            )
            del self._pipelines[connection_id]
        
        # Stop Transport workers (Transport handles ControlBus cleanup, etc.)
        if hasattr(self.transport, 'stop_workers'):
            asyncio.create_task(
                self.transport.stop_workers(connection_id),
                name=f"stop-workers-{connection_id[:8]}"
            )
        else:
            # Legacy interface compatibility: manual cleanup
            if hasattr(self.transport, '_control_buses'):
                self.transport._control_buses.pop(connection_id, None)
        
        # Cleanup session resources
        if connection_id in self._sessions:
            del self._sessions[connection_id]
        
        if connection_id in self._control_buses:
            del self._control_buses[connection_id]
        
        # Cleanup InputStation
        if connection_id in self._input_stations:
            del self._input_stations[connection_id]
    
    async def _cleanup_pipeline(self, connection_id: str, pipeline: Pipeline) -> None:
        """
        Async cleanup for pipeline resources.
        
        Args:
            connection_id: Connection ID (for logging)
            pipeline: Pipeline to cleanup
        """
        try:
            await pipeline.cleanup()
            self.logger.debug(f"Pipeline cleaned up for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error cleaning up pipeline for connection {connection_id[:8]}: {e}",
                exc_info=True
            )
    
    def get_active_session_count(self) -> int:
        """
        Get number of active sessions.
        
        Returns:
            Number of active sessions
        """
        return len(self._sessions)
    
    def get_active_sessions(self) -> list:
        """
        Get list of active session IDs.
        
        Returns:
            List of connection IDs
        """
        return list(self._sessions.keys())
    
    async def cancel_session(self, connection_id: str) -> None:
        """
        Cancel a specific session (e.g., when WebSocket disconnects).
        
        Args:
            connection_id: Connection to cancel
        """
        self.logger.info(f"Cancelling session for connection {connection_id[:8]}")
        
        # Cancel pipeline task
        if connection_id in self._sessions:
            task = self._sessions[connection_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    self.logger.error(f"Error waiting for pipeline cancellation: {e}")
        
        # Cancel interrupt handler
        if connection_id in self._interrupt_tasks:
            task = self._interrupt_tasks[connection_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        self.logger.info(f"Session cancelled for connection {connection_id[:8]}")
