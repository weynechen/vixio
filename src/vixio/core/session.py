"""
Session manager - connects Transport to DAG/Pipeline

Responsibilities:
1. Listen for new connections from Transport
2. Create a DAG (or Pipeline) instance for each connection
3. Route chunks: Transport input -> DAG -> Transport output
4. Manage DAG lifecycle
5. Handle interrupts via ControlBus
6. Handle device tools registration
"""

import asyncio
from typing import Callable, Dict, Optional, AsyncIterator, List, Union, TYPE_CHECKING
from vixio.core.transport import TransportBase
from vixio.core.pipeline import Pipeline
from vixio.core.dag import DAG, CompiledDAG
from vixio.core.control_bus import ControlBus
from vixio.core.chunk import Chunk, ChunkType
from loguru import logger

if TYPE_CHECKING:
    from vixio.core.station import Station
    from vixio.core.tools.types import ToolDefinition


class SessionManager:
    """
    Session manager - connects Transport to DAG (or Pipeline).
    
    Responsibilities:
    1. Listen for new connections from Transport
    2. Create a DAG instance for each connection
    3. Route chunks: Transport input -> DAG -> Transport output
    4. Manage DAG lifecycle
    5. Handle interrupts via ControlBus
    
    Design:
    - Each connection gets its own DAG and ControlBus instance
    - DAG runs as long as connection is alive
    - ControlBus handles interrupts and turn transitions
    - Automatically cleanup on disconnect
    
    DAG Mode:
    - Uses DAG for flexible non-linear processing
    - Supports branching and merging data flows
    - Automatic routing based on ALLOWED_INPUT_TYPES
    """
    
    def __init__(
        self,
        transport: TransportBase,
        dag_factory: Optional[Callable[[], DAG]] = None,
        pipeline_factory: Optional[Callable[[], Pipeline]] = None,
    ):
        """
        Initialize session manager.
        
        Args:
            transport: Transport to manage connections for
            dag_factory: Factory function that creates a fresh DAG for each connection
            pipeline_factory: (Legacy) Factory function that creates a Pipeline
            
        Note: Prefer dag_factory. pipeline_factory is for backward compatibility.
        """
        self.transport = transport
        self.dag_factory = dag_factory
        self.pipeline_factory = pipeline_factory
        
        # Determine factory type
        if dag_factory:
            self._use_dag = True
            self._factory = dag_factory
        elif pipeline_factory:
            self._use_dag = False
            self._factory = pipeline_factory
        else:
            raise ValueError("Either dag_factory or pipeline_factory must be provided")
        
        self._is_async_factory = asyncio.iscoroutinefunction(self._factory)
        self._sessions: Dict[str, asyncio.Task] = {}  # connection_id -> task
        self._control_buses: Dict[str, ControlBus] = {}  # connection_id -> control bus
        self._dags: Dict[str, Union[CompiledDAG, Pipeline]] = {}  # connection_id -> dag/pipeline
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
        
        # Register device tools callback (for MCP/function tools)
        if hasattr(self.transport, 'set_device_tools_callback'):
            self.transport.set_device_tools_callback(self._on_device_tools_ready)
        
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
        
        Creates DAG (or Pipeline), control bus, and interrupt handler for this connection.
        
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
        if hasattr(self.transport, 'start_workers'):
            await self.transport.start_workers(connection_id)
            self.logger.debug(f"Transport workers started for connection {connection_id[:8]}")
        
        # Create fresh DAG or Pipeline (support both sync and async factories)
        if self._is_async_factory:
            dag_or_pipeline = await self._factory()
        else:
            dag_or_pipeline = self._factory()
        
        # Get InputStation and OutputStation from Transport
        input_station = self.transport.get_input_station(connection_id)
        output_station = self.transport.get_output_station(connection_id)
        
        # Store InputStation (it's the data source, not part of DAG/Pipeline)
        self._input_stations[connection_id] = input_station
        input_station.control_bus = control_bus
        input_station.set_session_id(connection_id)
        
        if self._use_dag:
            # DAG mode: add OutputStation as a node and compile
            dag = dag_or_pipeline
            dag.add_node("transport_out", output_station)
            
            # Compile DAG with ControlBus
            compiled_dag = dag.compile(control_bus=control_bus)
            compiled_dag.set_session_id(connection_id)
            self._dags[connection_id] = compiled_dag
            
            self.logger.debug(
                f"Created DAG for connection {connection_id[:8]} with "
                f"{len(compiled_dag.nodes)} nodes"
            )
            
            # Start interrupt handler
            interrupt_task = asyncio.create_task(
                self._handle_interrupts_dag(connection_id, compiled_dag, control_bus),
                name=f"interrupt-handler-{connection_id[:8]}"
            )
        else:
            # Legacy Pipeline mode
            full_stations = dag_or_pipeline.stations + [output_station]
            pipeline = Pipeline(
                stations=full_stations,
                control_bus=control_bus,
                session_id=connection_id
            )
            self._dags[connection_id] = pipeline
            
            self.logger.debug(
                f"Created Pipeline for connection {connection_id[:8]} with "
                f"{len(full_stations)} stations"
            )
            
            # Start interrupt handler
            interrupt_task = asyncio.create_task(
                self._handle_interrupts(connection_id, pipeline, control_bus),
                name=f"interrupt-handler-{connection_id[:8]}"
            )
        
        self._interrupt_tasks[connection_id] = interrupt_task
        
        # Notify transport that DAG/Pipeline is ready
        await self.transport.on_pipeline_ready(connection_id)
        
        # Run DAG/Pipeline in background task
        if self._use_dag:
            task = asyncio.create_task(
                self._run_dag(connection_id, self._dags[connection_id]),
                name=f"dag-{connection_id[:8]}"
            )
        else:
            task = asyncio.create_task(
                self._run_pipeline(connection_id, self._dags[connection_id]),
                name=f"pipeline-{connection_id[:8]}"
            )
        
        self._sessions[connection_id] = task
        
        # Cleanup when done
        task.add_done_callback(lambda _: self._cleanup_session(connection_id))
    
    async def _handle_interrupts_dag(
        self,
        connection_id: str,
        dag: CompiledDAG,
        control_bus: ControlBus
    ) -> None:
        """
        Handle interrupt signals for a DAG connection.
        
        DAG interrupt handling:
        - ControlBus already notifies all registered handlers
        - Just need to log and clear event flag
        
        Args:
            connection_id: Connection ID
            dag: CompiledDAG instance
            control_bus: ControlBus instance
        """
        self.logger.info(f"Starting DAG interrupt handler for connection {connection_id[:8]}")
        
        try:
            while True:
                # Wait for interrupt signal
                interrupt = await control_bus.wait_for_interrupt()
                
                self.logger.warning(
                    f"[{connection_id[:8]}] DAG interrupt received: {interrupt.source} - {interrupt.reason}"
                )
                
                # Note: ControlBus.send_interrupt() already:
                # 1. Notifies all registered handlers (middlewares subscribed)
                # 2. Increments turn_id
                # So we just need to log and clear the event
                
                # Clear interrupt event flag
                control_bus.clear_interrupt_event()
                
                self.logger.info(
                    f"[{connection_id[:8]}] DAG interrupt handled, turn_id={control_bus.get_current_turn_id()}"
                )
        
        except asyncio.CancelledError:
            self.logger.info(f"DAG interrupt handler cancelled for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error in DAG interrupt handler for connection {connection_id[:8]}: {e}",
                exc_info=True
            )
    
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
                    from vixio.core.chunk import ControlChunk, ChunkType
                    
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
                    from vixio.core.chunk import ControlChunk, ChunkType
                    
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
    
    async def _run_dag(self, connection_id: str, dag: CompiledDAG) -> None:
        """
        Run DAG for a connection.
        
        Flow:
        1. Get input stream from InputStation
        2. Wrap input stream to detect CONTROL_INTERRUPT chunks
        3. Run DAG (chunks flow through nodes to OutputStation)
        
        Args:
            connection_id: Connection ID to run DAG for
            dag: CompiledDAG instance to run
        """
        self.logger.info(f"Starting DAG for connection {connection_id[:8]}")
        
        try:
            # Get InputStation
            input_station = self._input_stations.get(connection_id)
            if not input_station:
                self.logger.error(f"InputStation not found for connection {connection_id[:8]}")
                return
            
            # Get input stream from InputStation
            raw_input_stream = input_station._generate_chunks()
            
            # Get ControlBus for this connection
            control_bus = self._control_buses.get(connection_id)
            
            # Wrap input stream to detect CONTROL_INTERRUPT
            input_stream = self._wrap_input_stream(raw_input_stream, control_bus, connection_id)
            
            # Run DAG (OutputStation is part of DAG, handles output)
            chunk_count = 0
            async for _ in dag.run(input_stream):
                # OutputStation handles sending, nothing to do here
                chunk_count += 1
            
            self.logger.info(
                f"DAG completed for connection {connection_id[:8]}, "
                f"processed {chunk_count} output chunks"
            )
        
        except asyncio.CancelledError:
            self.logger.info(f"DAG cancelled for connection {connection_id[:8]}")
            dag.stop()
        
        except Exception as e:
            self.logger.error(f"DAG error for connection {connection_id[:8]}: {e}", exc_info=True)
        
        finally:
            self._cleanup_session(connection_id)
    
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
    
    async def _on_device_tools_ready(
        self,
        connection_id: str,
        tool_definitions: List['ToolDefinition']
    ) -> None:
        """
        Handle device tools becoming available.
        
        Called by Transport when MCP initialization completes and device tools are ready.
        Finds the AgentStation in the DAG/Pipeline and updates its tools.
        
        Args:
            connection_id: Connection ID
            tool_definitions: List of ToolDefinition from device
        """
        self.logger.info(
            f"Device tools ready for connection {connection_id[:8]}: "
            f"{len(tool_definitions)} tools"
        )
        
        # Find DAG/Pipeline for this connection
        dag_or_pipeline = self._dags.get(connection_id)
        if not dag_or_pipeline:
            self.logger.warning(
                f"No DAG/Pipeline found for connection {connection_id[:8]}, "
                f"cannot update tools"
            )
            return
        
        # Find AgentStation in DAG or Pipeline
        agent_station = None
        if isinstance(dag_or_pipeline, CompiledDAG):
            # DAG mode: search in nodes
            for node in dag_or_pipeline.nodes.values():
                if hasattr(node.station, 'agent') and hasattr(node.station.agent, 'add_tools'):
                    agent_station = node.station
                    break
        else:
            # Pipeline mode: search in stations
            for station in dag_or_pipeline.stations:
                if hasattr(station, 'agent') and hasattr(station.agent, 'add_tools'):
                    agent_station = station
                    break
        
        if not agent_station:
            self.logger.warning(
                f"No AgentStation found in pipeline for connection {connection_id[:8]}"
            )
            return
        
        # Convert ToolDefinition to Tool (for AgentProvider.update_tools)
        from vixio.providers.agent import Tool
        
        def make_device_executor(client, name):
            """Create executor closure with captured variables."""
            async def executor(**kwargs):
                return await client.call_tool(name, kwargs)
            return executor
        
        tools = []
        for tool_def in tool_definitions:
            # Create executor wrapper for device tools
            if tool_def.device_client:
                # Create async executor that calls device_client
                # Use factory function to capture variables correctly
                device_executor = make_device_executor(
                    tool_def.device_client,
                    tool_def.name
                )
                
                tool = Tool(
                    name=tool_def.name,
                    description=tool_def.description,
                    parameters=tool_def.parameters,
                    executor=device_executor
                )
            elif tool_def.executor:
                # Local tool - use executor directly
                tool = Tool(
                    name=tool_def.name,
                    description=tool_def.description,
                    parameters=tool_def.parameters,
                    executor=tool_def.executor
                )
            else:
                self.logger.warning(f"Tool {tool_def.name} has no executor, skipping")
                continue
            
            tools.append(tool)
        
        # Add device tools to agent (appends to existing tools)
        try:
            await agent_station.agent.add_tools(tools)
            self.logger.info(
                f"Added {len(tools)} device tools to AgentStation for "
                f"connection {connection_id[:8]}"
            )
        except Exception as e:
            self.logger.error(
                f"Failed to add device tools for connection {connection_id[:8]}: {e}"
            )
    
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
        
        # Cleanup DAG/Pipeline resources
        if connection_id in self._dags:
            dag_or_pipeline = self._dags[connection_id]
            # Schedule async cleanup in background
            asyncio.create_task(
                self._cleanup_dag_or_pipeline(connection_id, dag_or_pipeline),
                name=f"cleanup-{connection_id[:8]}"
            )
            del self._dags[connection_id]
        
        # Stop Transport workers
        if hasattr(self.transport, 'stop_workers'):
            asyncio.create_task(
                self.transport.stop_workers(connection_id),
                name=f"stop-workers-{connection_id[:8]}"
            )
        else:
            # Legacy interface compatibility
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
    
    async def _cleanup_dag_or_pipeline(
        self, 
        connection_id: str, 
        dag_or_pipeline: Union[CompiledDAG, Pipeline]
    ) -> None:
        """
        Async cleanup for DAG or Pipeline resources.
        
        Args:
            connection_id: Connection ID (for logging)
            dag_or_pipeline: DAG or Pipeline to cleanup
        """
        try:
            await dag_or_pipeline.cleanup()
            resource_type = "DAG" if isinstance(dag_or_pipeline, CompiledDAG) else "Pipeline"
            self.logger.debug(f"{resource_type} cleaned up for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error cleaning up for connection {connection_id[:8]}: {e}",
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
