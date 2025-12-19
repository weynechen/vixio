"""
Session manager - connects Transport to DAG

Responsibilities:
1. Listen for new connections from Transport
2. Create a DAG instance for each connection
3. Route chunks: Transport input -> DAG -> Transport output
4. Manage DAG lifecycle
5. Handle interrupts via ControlBus
6. Handle device tools registration
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Callable, Dict, Optional, List, TYPE_CHECKING
from vixio.core.transport import TransportBase
from vixio.core.dag import DAG, CompiledDAG
from vixio.core.control_bus import ControlBus
from vixio.core.chunk import Chunk, ChunkType
from loguru import logger

if TYPE_CHECKING:
    from vixio.core.station import Station
    from vixio.core.tools.types import ToolDefinition


class SessionManager:
    """
    Session manager - connects Transport to DAG.
    
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
        dag_factory: Callable[[], DAG],
        turn_timeout_seconds: Optional[float] = None,
    ):
        """
        Initialize session manager.
        
        Args:
            transport: Transport to manage connections for
            dag_factory: Factory function that creates a fresh DAG for each connection
            turn_timeout_seconds: Optional timeout for turn inactivity (None = no timeout).
                If set, sessions will automatically timeout if no VAD start event
                is detected within this many seconds after turn increment.
                Default is None (no timeout).
        """
        self.transport = transport
        self.dag_factory = dag_factory
        self.turn_timeout_seconds = turn_timeout_seconds
        
        self._is_async_factory = asyncio.iscoroutinefunction(dag_factory)
        self._sessions: Dict[str, asyncio.Task] = {}  # connection_id -> task
        self._control_buses: Dict[str, ControlBus] = {}  # connection_id -> control bus
        self._dags: Dict[str, CompiledDAG] = {}  # connection_id -> compiled dag
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
        
        This cancels all active DAGs, interrupt handlers, and stops transport.
        """
        self.logger.info("Stopping session manager...")
        
        # Cancel all interrupt handler tasks
        for connection_id, task in self._interrupt_tasks.items():
            self.logger.debug(f"Cancelling interrupt handler for connection {connection_id[:8]}")
            task.cancel()
        
        # Cancel all DAG tasks
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
        
        Creates DAG, control bus, and interrupt handler for this connection.
        
        Args:
            connection_id: Unique identifier for the connection
        """
        self.logger.info(f"New connection: {connection_id[:8]}")
        
        # Create ControlBus for this session with timeout configuration
        control_bus = ControlBus(turn_timeout_seconds=self.turn_timeout_seconds)
        self._control_buses[connection_id] = control_bus
        
        if self.turn_timeout_seconds:
            self.logger.info(
                f"Turn timeout enabled: {self.turn_timeout_seconds}s "
                f"for connection {connection_id[:8]}"
            )
        
        # Inject ControlBus into Transport (for turn_id management)
        self.transport.set_control_bus(connection_id, control_bus)
        self.logger.debug(f"ControlBus set for connection {connection_id[:8]}")
        
        # Start Transport workers (read_worker + send_worker)
        if hasattr(self.transport, 'start_workers'):
            await self.transport.start_workers(connection_id)
            self.logger.debug(f"Transport workers started for connection {connection_id[:8]}")
        
        # Create fresh DAG (support both sync and async factories)
        if self._is_async_factory:
            dag = await self.dag_factory()
        else:
            dag = self.dag_factory()
        
        # Get InputStation and OutputStation from Transport
        input_station = self.transport.get_input_station(connection_id)
        output_station = self.transport.get_output_station(connection_id)
        
        # Store InputStation (it's the data source, not part of DAG)
        self._input_stations[connection_id] = input_station
        input_station.control_bus = control_bus
        input_station.set_session_id(connection_id)
        
        # Add OutputStation as a node and compile DAG
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
            self._handle_interrupts(connection_id, compiled_dag, control_bus),
            name=f"interrupt-handler-{connection_id[:8]}"
        )
        self._interrupt_tasks[connection_id] = interrupt_task
        
        # Notify transport that DAG is ready
        await self.transport.on_pipeline_ready(connection_id)
        
        # Run DAG in background task
        task = asyncio.create_task(
            self._run_dag(connection_id, compiled_dag),
            name=f"dag-{connection_id[:8]}"
        )
        self._sessions[connection_id] = task
        
        # Cleanup when done
        task.add_done_callback(lambda _: self._cleanup_session(connection_id))
    
    async def _handle_interrupts(
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
                
                # Handle turn timeout - close session
                if interrupt.reason == "turn_timeout":
                    timeout_seconds = interrupt.metadata.get("timeout_seconds", "unknown")
                    self.logger.warning(
                        f"[{connection_id[:8]}] Turn timeout ({timeout_seconds}s) detected, "
                        f"closing session"
                    )
                    # Cancel the session
                    await self.cancel_session(connection_id)
                    break
                
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
    
    async def _run_dag(self, connection_id: str, dag: CompiledDAG) -> None:
        """
        Run DAG for a connection.
        
        Flow:
        1. Get input stream from InputStation
        2. Wrap input stream to detect CONTROL_STATE_RESET chunks
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
            
            # Wrap input stream to detect CONTROL_STATE_RESET
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
    
    async def _wrap_input_stream(
        self,
        input_stream: AsyncIterator[Chunk],
        control_bus: Optional[ControlBus],
        connection_id: str
    ) -> AsyncIterator[Chunk]:
        """
        Wrap input stream to detect CONTROL_STATE_RESET chunks and send to ControlBus.
        
        This allows client-initiated interrupts (abort) to trigger the interrupt mechanism.
        
        Args:
            input_stream: Raw input stream from transport
            control_bus: ControlBus instance for this connection
            connection_id: Connection identifier
            
        Yields:
            Chunks from input stream (CONTROL_STATE_RESET chunks still passed through)
        """
        async for chunk in input_stream:
            # Detect CONTROL_STATE_RESET chunk and send to ControlBus
            if chunk.type == ChunkType.CONTROL_STATE_RESET and control_bus:
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
            
            # Always yield the chunk (so DAG can process it too)
            yield chunk
    
    async def _on_device_tools_ready(
        self,
        connection_id: str,
        tool_definitions: List['ToolDefinition']
    ) -> None:
        """
        Handle device tools becoming available.
        
        Called by Transport when MCP initialization completes and device tools are ready.
        Finds the AgentStation in the DAG and updates its tools.
        
        Args:
            connection_id: Connection ID
            tool_definitions: List of ToolDefinition from device
        """
        self.logger.info(
            f"Device tools ready for connection {connection_id[:8]}: "
            f"{len(tool_definitions)} tools"
        )
        
        # Find DAG for this connection
        dag = self._dags.get(connection_id)
        if not dag:
            self.logger.warning(
                f"No DAG found for connection {connection_id[:8]}, "
                f"cannot update tools"
            )
            return
        
        # Find AgentStation in DAG
        agent_station = None
        for node in dag.nodes.values():
            if hasattr(node.station, 'agent') and hasattr(node.station.agent, 'add_tools'):
                agent_station = node.station
                break
        
        if not agent_station:
            self.logger.warning(
                f"No AgentStation found in DAG for connection {connection_id[:8]}"
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
        
        # Cleanup DAG resources
        if connection_id in self._dags:
            dag = self._dags[connection_id]
            # Schedule async cleanup in background
            asyncio.create_task(
                self._cleanup_dag(connection_id, dag),
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
        
        # Cleanup ControlBus (cancel timeout tasks and clear references)
        if connection_id in self._control_buses:
            control_bus = self._control_buses[connection_id]
            # Cancel any pending timeout tasks
            if hasattr(control_bus, "cleanup"):
                control_bus.cleanup()
            # Clear logger reference to help GC
            if hasattr(control_bus, "logger"):
                control_bus.logger = None
            del self._control_buses[connection_id]
        
        # Cleanup InputStation (clear logger and control_bus references)
        if connection_id in self._input_stations:
            input_station = self._input_stations[connection_id]
            if hasattr(input_station, "logger"):
                input_station.logger = None
            if hasattr(input_station, "control_bus"):
                input_station.control_bus = None
            del self._input_stations[connection_id]
    
    async def _cleanup_dag(
        self, 
        connection_id: str, 
        dag: CompiledDAG
    ) -> None:
        """
        Async cleanup for DAG resources.
        
        Args:
            connection_id: Connection ID (for logging)
            dag: CompiledDAG to cleanup
        """
        try:
            await dag.cleanup()
            self.logger.debug(f"DAG cleaned up for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error cleaning up DAG for connection {connection_id[:8]}: {e}",
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
        
        # Cancel DAG task
        if connection_id in self._sessions:
            task = self._sessions[connection_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    self.logger.error(f"Error waiting for DAG cancellation: {e}")
        
        # Cancel interrupt handler
        if connection_id in self._interrupt_tasks:
            task = self._interrupt_tasks[connection_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        # Cleanup ControlBus timeout tasks before cleaning up session
        if connection_id in self._control_buses:
            control_bus = self._control_buses[connection_id]
            if hasattr(control_bus, "cleanup"):
                control_bus.cleanup()
        
        # Cleanup session resources (critical for preventing memory leaks)
        self._cleanup_session(connection_id)
        
        self.logger.info(f"Session cancelled for connection {connection_id[:8]}")
