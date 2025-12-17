"""
DAG (Directed Acyclic Graph) - flexible pipeline architecture

Design principles:
1. Station reuse: Station interface unchanged, just remove passthrough logic
2. Auto routing: Based on downstream ALLOWED_INPUT_TYPES
3. Type generalization: Keep generic types (TEXT, AUDIO), not source-specific
4. Full decoupling: Station doesn't check chunk.source, only cares about type
5. Cohesive sync: Stream sync handled inside Station

Completion Signal Contract:
- Stations declare EMITS_COMPLETION and AWAITS_COMPLETION
- DAG automatically binds completion signals between adjacent stations
- When upstream (EMITS_COMPLETION=True) completes, downstream (AWAITS_COMPLETION=True) receives signal
- This decouples stations from knowing each other's specific signal types
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import (
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    AsyncIterator,
    Any,
    TYPE_CHECKING,
)
from loguru import logger

from vixio.core.chunk import Chunk, ChunkType, is_completion_event
from vixio.core.station import Station

if TYPE_CHECKING:
    from vixio.core.control_bus import ControlBus


@dataclass
class NodeStats:
    """Runtime statistics for a DAG node"""

    chunks_received: int = 0
    chunks_processed: int = 0
    chunks_forwarded: int = 0
    queue_size: int = 0
    avg_latency_ms: float = 0.0
    last_chunk_time: float = 0.0
    status: str = "idle"  # idle, processing, error
    error_count: int = 0
    last_error: Optional[str] = None

    # Text preview for visualization
    last_input_text: str = ""
    last_output_text: str = ""

    # Latency tracking
    _latency_samples: List[float] = field(default_factory=list)
    _max_samples: int = 100

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency sample and update average"""
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > self._max_samples:
            self._latency_samples.pop(0)
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)


class DAGNode:
    """
    DAG Node - wraps a Station as a node in the graph

    Responsibilities:
    - Run Station in its own asyncio task
    - Route output chunks to downstream nodes based on ALLOWED_INPUT_TYPES
    - Route completion signals to downstream nodes based on contract
    - Collect runtime statistics
    """

    def __init__(self, name: str, station: Station):
        """
        Initialize DAG node.

        Args:
            name: Node name (unique identifier)
            station: Station instance to wrap
        """
        self.name = name
        self.station = station
        self.input_queue: asyncio.Queue = asyncio.Queue()
        self.downstream: List["DAGNode"] = []
        self.completion_downstream: List["DAGNode"] = []  # Nodes that await completion
        self.stats = NodeStats()
        self.position: Dict[str, float] = {"x": 0, "y": 0}  # For visualization layout
        self.logger = logger.bind(component=f"DAGNode:{name}")

    @property
    def allowed_input_types(self) -> Optional[Set[ChunkType]]:
        """Get allowed input types from Station"""
        types = getattr(self.station, "ALLOWED_INPUT_TYPES", None)
        if types:
            return set(types)
        return None  # None means accept all types
    
    @property
    def emits_completion(self) -> bool:
        """Check if this station emits completion signal"""
        return getattr(self.station, "EMITS_COMPLETION", False)
    
    @property
    def awaits_completion(self) -> bool:
        """Check if this station awaits completion signal"""
        return getattr(self.station, "AWAITS_COMPLETION", False)

    def accepts(self, chunk: Chunk) -> bool:
        """
        Check if this node accepts the chunk.

        - Signal (EVENT): Always accept, broadcast to all downstream
        - Data: Filter by ALLOWED_INPUT_TYPES

        Note: CONTROL signals go through ControlBus, not DAG

        Args:
            chunk: Chunk to check

        Returns:
            True if this node accepts the chunk
        """
        # EVENT signals always accepted (broadcast)
        if chunk.is_signal():
            return True

        # Data filtered by type
        allowed = self.allowed_input_types
        if allowed is None:
            return True
        return chunk.type in allowed

    async def run(
        self,
        output_queue: asyncio.Queue,
        event_emitter: Optional[Any] = None,
    ) -> None:
        """
        Run the node - process input and route to downstream.

        Args:
            output_queue: Queue for final output (chunks with no downstream)
            event_emitter: Optional DAGEventEmitter for monitoring
        """
        self.stats.status = "processing"
        self.logger.debug(f"Node {self.name} started")

        try:
            async for chunk in self._process_with_completion():
                start_time = time.time()

                # Update stats
                self.stats.chunks_processed += 1
                self.stats.last_chunk_time = time.time()

                # Record text preview for visualization
                if chunk.type in [ChunkType.TEXT, ChunkType.TEXT_DELTA]:
                    text = str(chunk.data) if chunk.data else ""
                    self.stats.last_output_text = text[:100]

                # Ensure source is set
                if not chunk.source:
                    chunk.source = self.name

                # Emit chunk processed event
                if event_emitter:
                    await event_emitter.emit_chunk_processed(self.name, chunk)

                # Handle completion event specially
                if is_completion_event(chunk):
                    await self._route_completion_event(chunk, output_queue, event_emitter)
                else:
                    # Route regular chunks to downstream nodes
                    await self._route_data_chunk(chunk, output_queue, event_emitter)

                # Record latency
                latency_ms = (time.time() - start_time) * 1000
                self.stats.record_latency(latency_ms)

        except asyncio.CancelledError:
            self.logger.debug(f"Node {self.name} cancelled")
            raise
        except Exception as e:
            self.stats.status = "error"
            self.stats.error_count += 1
            self.stats.last_error = str(e)
            self.logger.error(f"Node {self.name} error: {e}", exc_info=True)
            raise
        finally:
            self.stats.status = "idle"
            # Signal downstream that this node is done
            for downstream in self.downstream:
                await downstream.input_queue.put(None)
            for downstream in self.completion_downstream:
                if downstream not in self.downstream:
                    await downstream.input_queue.put(None)
            self.logger.debug(f"Node {self.name} finished")
    
    async def _process_with_completion(self) -> AsyncIterator[Chunk]:
        """
        Process input through station.
        
        """
        async for chunk in self.station.process(self._input_iterator()):
            yield chunk
    
    async def _route_completion_event(
        self,
        chunk: Chunk,
        output_queue: asyncio.Queue,
        event_emitter: Optional[Any] = None,
    ) -> None:
        """
        Route completion event to downstream nodes that await completion.
        
        Args:
            chunk: EventChunk with EVENT_STREAM_COMPLETE to route
            output_queue: Queue for final output
            event_emitter: Optional event emitter
        """
        sent = False
        
        # Route to completion_downstream (nodes that await completion)
        for downstream in self.completion_downstream:
            await downstream.input_queue.put(chunk)
            downstream.stats.chunks_received += 1
            self.stats.chunks_forwarded += 1
            sent = True
            
            self.logger.debug(
                f"Routed completion event to {downstream.name}"
            )
            
            if event_emitter:
                await event_emitter.emit_edge_active(
                    self.name, downstream.name, chunk
                )
        
        # If no downstream awaits completion, send to output (for logging/monitoring)
        if not sent:
            await output_queue.put(chunk)
    
    async def _route_data_chunk(
        self,
        chunk: Chunk,
        output_queue: asyncio.Queue,
        event_emitter: Optional[Any] = None,
    ) -> None:
        """
        Route regular data chunk to downstream nodes.
        
        Args:
            chunk: Chunk to route
            output_queue: Queue for final output
            event_emitter: Optional event emitter
        """
        sent = False
        
        # Debug logging for TEXT_DELTA routing
        if chunk.type == ChunkType.TEXT_DELTA:
            text_preview = str(chunk.data)[:30] if chunk.data else ""
            self.logger.debug(
                f"[DAG_ROUTE] {self.name} routing TEXT_DELTA: '{text_preview}' "
                f"to {len(self.downstream)} downstream(s)"
            )
        
        for downstream in self.downstream:
            if downstream.accepts(chunk):
                await downstream.input_queue.put(chunk)
                downstream.stats.chunks_received += 1
                downstream.stats.queue_size = downstream.input_queue.qsize()

                # Record text preview for downstream
                if chunk.type in [ChunkType.TEXT, ChunkType.TEXT_DELTA]:
                    text = str(chunk.data) if chunk.data else ""
                    downstream.stats.last_input_text = text[:100]

                self.stats.chunks_forwarded += 1
                sent = True

                # Debug logging for TEXT_DELTA routing
                if chunk.type == ChunkType.TEXT_DELTA:
                    self.logger.debug(
                        f"[DAG_ROUTE] {self.name} -> {downstream.name}: TEXT_DELTA accepted"
                    )

                # Emit edge active event
                if event_emitter:
                    await event_emitter.emit_edge_active(
                        self.name, downstream.name, chunk
                    )
            else:
                # Debug logging for rejected chunks
                if chunk.type == ChunkType.TEXT_DELTA:
                    self.logger.debug(
                        f"[DAG_ROUTE] {self.name} -> {downstream.name}: TEXT_DELTA REJECTED "
                        f"(allowed_types={downstream.allowed_input_types})"
                    )

        # If no downstream accepted or no downstream at all, send to output
        if not sent or not self.downstream:
            if chunk.type == ChunkType.TEXT_DELTA:
                self.logger.debug(
                    f"[DAG_ROUTE] {self.name}: TEXT_DELTA sent to output_queue "
                    f"(sent={sent}, downstream_count={len(self.downstream)})"
                )
            await output_queue.put(chunk)

    async def _input_iterator(self) -> AsyncIterator[Chunk]:
        """
        Iterate over input queue until None is received.
        
        """
        while True:
            chunk = await self.input_queue.get()
            if chunk is None:
                break
            self.stats.queue_size = self.input_queue.qsize()
            yield chunk

    def to_dict(self) -> Dict[str, Any]:
        """Export node info for visualization"""
        return {
            "id": self.name,
            "type": self.station.__class__.__name__,
            "display_name": getattr(self.station, "DISPLAY_NAME", self.name),
            "allowed_input_types": (
                [t.value for t in self.allowed_input_types]
                if self.allowed_input_types
                else []
            ),
            "position": self.position,
            "icon": getattr(self.station, "ICON", None),
            "color": getattr(self.station, "COLOR", None),
        }


class DAG:
    """
    DAG Builder - constructs the graph structure

    Usage:
        dag = DAG("voice_chat")
        dag.add_node("vad", VADStation())
        dag.add_node("asr", ASRStation())
        dag.add_edge("transport_in", "vad", "asr", "transport_out")
        compiled = dag.compile()
    """

    # Virtual node names
    TRANSPORT_IN = "transport_in"
    TRANSPORT_OUT = "transport_out"

    def __init__(self, name: str = "DAG"):
        """
        Initialize DAG builder.

        Args:
            name: DAG name for logging and visualization
        """
        self.name = name
        self._nodes: Dict[str, DAGNode] = {}
        self._edges: List[Tuple[str, str]] = []
        self.logger = logger.bind(component=f"DAG:{name}")

    def add_node(self, name: str, station: Station) -> "DAG":
        """
        Add a node to the DAG.

        Args:
            name: Unique node name
            station: Station instance

        Returns:
            self for chaining

        Note:
            - transport_in is reserved and cannot be used as a node name
            - transport_out can be bound to an actual OutputStation
        """
        if name in self._nodes:
            raise ValueError(f"Node '{name}' already exists")
        if name == self.TRANSPORT_IN:
            raise ValueError(f"'{name}' is a reserved virtual node name")

        self._nodes[name] = DAGNode(name, station)
        self.logger.debug(f"Added node: {name} ({station.__class__.__name__})")
        return self

    def add_edge(self, *nodes: str) -> "DAG":
        """
        Add edges to the DAG. Supports chain syntax.

        Usage:
            - add_edge("a", "b")           # Single edge a -> b
            - add_edge("a", "b", "c", "d") # Chain a -> b -> c -> d

        Args:
            *nodes: Node names (at least 2)

        Returns:
            self for chaining
        """
        if len(nodes) < 2:
            raise ValueError("add_edge requires at least 2 nodes")

        for i in range(len(nodes) - 1):
            from_node = nodes[i]
            to_node = nodes[i + 1]

            # Validate non-virtual nodes exist
            if from_node not in [self.TRANSPORT_IN] and from_node not in self._nodes:
                raise ValueError(f"Node '{from_node}' does not exist")
            if to_node not in [self.TRANSPORT_OUT] and to_node not in self._nodes:
                raise ValueError(f"Node '{to_node}' does not exist")

            self._edges.append((from_node, to_node))
            self.logger.debug(f"Added edge: {from_node} -> {to_node}")

        return self

    def compile(self, control_bus: Optional["ControlBus"] = None) -> "CompiledDAG":
        """
        Compile the DAG for execution.

        Validates the DAG structure and returns an executable CompiledDAG.

        Args:
            control_bus: Optional ControlBus for interrupt handling

        Returns:
            CompiledDAG instance

        Raises:
            DAGValidationError: If DAG is invalid (cycles, isolated nodes, etc.)
        """
        self._validate()
        return CompiledDAG(self, control_bus)

    def _validate(self) -> None:
        """
        Validate DAG structure.

        Checks:
        - No cycles
        - No isolated nodes (except virtual nodes)
        - At least one path from transport_in to transport_out
        """
        # Check for cycles using DFS
        if self._has_cycle():
            raise DAGValidationError("DAG contains a cycle")

        # Check for isolated nodes
        connected_nodes = set()
        for from_node, to_node in self._edges:
            connected_nodes.add(from_node)
            connected_nodes.add(to_node)

        isolated = set(self._nodes.keys()) - connected_nodes
        if isolated:
            self.logger.warning(f"Isolated nodes (not connected): {isolated}")

        # Check for at least one entry point
        entry_nodes = self._find_entry_nodes()
        if not entry_nodes:
            raise DAGValidationError("No entry nodes (nodes connected from transport_in)")

        self.logger.info(
            f"DAG validated: {len(self._nodes)} nodes, {len(self._edges)} edges"
        )

    def _has_cycle(self) -> bool:
        """Check if DAG has cycles using DFS"""
        # Build adjacency list
        adj: Dict[str, List[str]] = {name: [] for name in self._nodes}
        for from_node, to_node in self._edges:
            if from_node in adj:
                adj[from_node].append(to_node)

        visited = set()
        rec_stack = set()

        def dfs(node: str) -> bool:
            if node not in adj:
                return False
            visited.add(node)
            rec_stack.add(node)

            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for node in self._nodes:
            if node not in visited:
                if dfs(node):
                    return True
        return False

    def _find_entry_nodes(self) -> List[str]:
        """Find nodes connected from transport_in"""
        entry_nodes = []
        for from_node, to_node in self._edges:
            if from_node == self.TRANSPORT_IN and to_node in self._nodes:
                entry_nodes.append(to_node)
        return entry_nodes

    def _find_exit_nodes(self) -> List[str]:
        """Find nodes connected to transport_out"""
        exit_nodes = []
        for from_node, to_node in self._edges:
            if to_node == self.TRANSPORT_OUT and from_node in self._nodes:
                exit_nodes.append(from_node)
        return exit_nodes

    def to_dict(self) -> Dict[str, Any]:
        """Export DAG structure for visualization"""
        return {
            "name": self.name,
            "nodes": [node.to_dict() for node in self._nodes.values()],
            "edges": [{"from": f, "to": t} for f, t in self._edges],
        }


class CompiledDAG:
    """
    Compiled DAG - executable graph

    Created by DAG.compile(), runs all nodes as concurrent tasks.
    """

    def __init__(self, dag: DAG, control_bus: Optional["ControlBus"] = None):
        """
        Initialize compiled DAG.

        Args:
            dag: Source DAG
            control_bus: Optional ControlBus for interrupt handling
        """
        self.name = dag.name
        self.nodes = dag._nodes
        self.edges = dag._edges
        self.control_bus = control_bus
        self._entry_nodes = dag._find_entry_nodes()
        self._exit_nodes = dag._find_exit_nodes()
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self.logger = logger.bind(component=f"CompiledDAG:{self.name}")

        # Build graph connections
        self._build_graph()

        # Propagate control_bus to all stations
        if control_bus:
            for node in self.nodes.values():
                node.station.control_bus = control_bus

        self.logger.info(
            f"Compiled DAG: {len(self.nodes)} nodes, "
            f"entry={self._entry_nodes}, exit={self._exit_nodes}"
        )

    def _build_graph(self) -> None:
        """
        Build downstream connections between nodes.
        
        Two types of connections:
        1. Data routing: Based on ALLOWED_INPUT_TYPES
        2. Completion routing: Based on EMITS_COMPLETION and AWAITS_COMPLETION
        """
        for from_name, to_name in self.edges:
            # Skip transport_in (always virtual)
            if from_name == DAG.TRANSPORT_IN:
                continue
            # Skip transport_out only if it's not bound to an actual node
            if to_name == DAG.TRANSPORT_OUT and to_name not in self.nodes:
                continue
            from_node = self.nodes.get(from_name)
            to_node = self.nodes.get(to_name)
            if from_node and to_node:
                # Add data routing connection
                from_node.downstream.append(to_node)
                
                # Auto-bind completion signal routing
                if from_node.emits_completion and to_node.awaits_completion:
                    from_node.completion_downstream.append(to_node)
                    self.logger.info(
                        f"Auto-bound completion: {from_name} -> {to_name}"
                    )

    async def run(
        self,
        input_stream: AsyncIterator[Chunk],
        event_emitter: Optional[Any] = None,
    ) -> AsyncIterator[Chunk]:
        """
        Execute the DAG.

        Args:
            input_stream: Input chunk stream (from transport)
            event_emitter: Optional DAGEventEmitter for monitoring

        Yields:
            Output chunks (from exit nodes or nodes with no downstream)
        """
        self._running = True
        output_queue: asyncio.Queue = asyncio.Queue()

        # Create input queues for all nodes
        for node in self.nodes.values():
            node.input_queue = asyncio.Queue()

        # Start all node tasks
        self._tasks = []
        for node in self.nodes.values():
            task = asyncio.create_task(
                node.run(output_queue, event_emitter), name=f"DAG-{node.name}"
            )
            self._tasks.append(task)

        # Get entry nodes
        entry_nodes = [self.nodes[name] for name in self._entry_nodes]

        # Feed input stream to entry nodes
        async def feed_input():
            try:
                async for chunk in input_stream:
                    # Handle CONTROL_STATE_RESET: send interrupt + notify client immediately
                    if chunk.type == ChunkType.CONTROL_STATE_RESET:
                        if self.control_bus:
                            await self.control_bus.send_interrupt(
                                source=chunk.source or "transport",
                                reason=chunk.type.value,
                            )
                        
                        # Send TTS_STOP directly to Transport's priority_queue
                        # This bypasses DAG input_queue to avoid waiting behind queued audio
                        await self._send_immediate_turn_switch(chunk)
                        continue
                    
                    # Skip other CONTROL signals (CONTROL_HANDSHAKE, etc.)
                    # They don't need interrupt handling or client notification
                    if chunk.type.value.startswith("control."):
                        self.logger.debug(f"Skipping control signal: {chunk.type.value}")
                        continue

                    # Feed to entry nodes that accept this chunk
                    for entry_node in entry_nodes:
                        if entry_node.accepts(chunk):
                            await entry_node.input_queue.put(chunk)
                            entry_node.stats.chunks_received += 1

                            # Record text preview
                            if chunk.type in [ChunkType.TEXT, ChunkType.TEXT_DELTA]:
                                text = str(chunk.data) if chunk.data else ""
                                entry_node.stats.last_input_text = text[:100]
            except Exception as e:
                self.logger.error(f"Error feeding input: {e}")
            finally:
                # Signal end of input
                for entry_node in entry_nodes:
                    await entry_node.input_queue.put(None)
                self.logger.debug("Input feeding complete")

        input_task = asyncio.create_task(feed_input(), name="DAG-input-feeder")

        # Collect output
        try:
            while self._running:
                try:
                    chunk = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                    if chunk is None:
                        break
                    yield chunk
                except asyncio.TimeoutError:
                    # Check if all tasks are done
                    if all(t.done() for t in self._tasks) and output_queue.empty():
                        break
                    continue
        except asyncio.CancelledError:
            self.logger.debug("DAG run cancelled")
            raise
        finally:
            self._running = False
            await self._cleanup(input_task)

    def clear_queues(self) -> int:
        """
        Clear all node input queues.
        
        Called during interrupt handling to discard pending chunks.
        This mirrors Pipeline.clear_queues() behavior.
        
        Returns:
            Number of chunks cleared
        """
        cleared_count = 0
        
        for node in self.nodes.values():
            while not node.input_queue.empty():
                try:
                    node.input_queue.get_nowait()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break
        
        if cleared_count > 0:
            self.logger.info(f"Cleared {cleared_count} chunks from DAG queues")
        
        return cleared_count

    async def _send_immediate_turn_switch(self, chunk: Chunk) -> None:
        """
        Handle interrupt: clear queues and notify client immediately.
        
        Steps:
        1. Clear all DAG node queues (discard pending chunks)
        2. Clear Transport send_queue (via OutputStation)
        3. Send TTS_STOP via priority_queue
        
        Args:
            chunk: CONTROL_STATE_RESET chunk with session info
        """
        from vixio.stations.transport_stations import OutputStation
        
        # 1. Clear all DAG node queues
        self.clear_queues()
        
        # OutputStation is registered as 'transport_out' node
        transport_out_node = self.nodes.get(DAG.TRANSPORT_OUT)
        if not transport_out_node:
            self.logger.warning("transport_out node not found, cannot send immediate turn switch")
            return
        
        station = transport_out_node.station
        
        # 2 & 3. Clear send_queue + send TTS_STOP via priority_queue
        if isinstance(station, OutputStation):
            try:
                await station.handle_turn_switch()
                self.logger.info("Called OutputStation.handle_turn_switch() for interrupt")
            except Exception as e:
                self.logger.error(f"Failed to send immediate turn switch: {e}")
        else:
            self.logger.warning(f"transport_out node is not OutputStation: {type(station).__name__}")

    async def _cleanup(self, input_task: asyncio.Task) -> None:
        """Cleanup all tasks"""
        # Cancel input task
        if not input_task.done():
            input_task.cancel()
            try:
                await input_task
            except asyncio.CancelledError:
                pass

        # Cancel all node tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()
        self.logger.debug("All tasks cleaned up")

    def stop(self) -> None:
        """Stop the DAG execution"""
        self._running = False

    def get_stats(self) -> Dict[str, Any]:
        """Get runtime statistics for all nodes"""
        return {
            "nodes": {
                name: {
                    "status": node.stats.status,
                    "chunks_received": node.stats.chunks_received,
                    "chunks_processed": node.stats.chunks_processed,
                    "chunks_forwarded": node.stats.chunks_forwarded,
                    "queue_size": node.stats.queue_size,
                    "avg_latency_ms": node.stats.avg_latency_ms,
                    "last_input_text": node.stats.last_input_text,
                    "last_output_text": node.stats.last_output_text,
                    "error_count": node.stats.error_count,
                    "last_error": node.stats.last_error,
                }
                for name, node in self.nodes.items()
            },
            "timestamp": time.time(),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Export DAG structure for visualization"""
        return {
            "name": self.name,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [{"from": f, "to": t} for f, t in self.edges],
        }

    def set_session_id(self, session_id: str) -> None:
        """Set session ID for all stations"""
        for node in self.nodes.values():
            node.station.set_session_id(session_id)

    async def cleanup(self) -> None:
        """Cleanup all station resources and break reference cycles"""
        self.logger.debug("Cleaning up DAG resources...")
        cleanup_count = 0

        for node in self.nodes.values():
            station = node.station
            
            # Call station's cleanup method if exists
            if hasattr(station, "cleanup") and callable(getattr(station, "cleanup")):
                try:
                    cleanup_method = getattr(station, "cleanup")
                    if asyncio.iscoroutinefunction(cleanup_method):
                        await cleanup_method()
                    else:
                        cleanup_method()
                    cleanup_count += 1
                except Exception as e:
                    self.logger.error(f"Error cleaning up station {station.name}: {e}")
            
            # Clear station's middleware logger references
            if hasattr(station, "_middlewares"):
                for mw in getattr(station, "_middlewares", []):
                    if hasattr(mw, "logger"):
                        mw.logger = None
                    if hasattr(mw, "station"):
                        mw.station = None
            
            # Clear station's control_bus reference
            if hasattr(station, "control_bus"):
                station.control_bus = None
            
            # Clear station's logger reference
            if hasattr(station, "logger"):
                station.logger = None
            
            # Clear DAGNode's logger reference
            if hasattr(node, "logger"):
                node.logger = None

        if cleanup_count > 0:
            self.logger.info(f"Cleaned up {cleanup_count} stations")
        
        # Clear ControlBus reference
        if self.control_bus:
            if hasattr(self.control_bus, "logger"):
                self.control_bus.logger = None
            self.control_bus = None
        
        # Clear own logger reference (do this last)
        self.logger = None


class DAGValidationError(Exception):
    """Exception raised when DAG validation fails"""

    pass
