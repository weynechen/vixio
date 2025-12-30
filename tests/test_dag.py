"""
Unit tests for DAG (Directed Acyclic Graph) implementation
"""

import asyncio
import pytest
from collections.abc import AsyncIterator

from vixio.core.chunk import Chunk, ChunkType, TextChunk, AudioChunk, EventChunk
from vixio.core.station import Station, PassthroughStation
from vixio.core.dag import DAG, DAGNode, CompiledDAG, NodeStats, DAGValidationError
from vixio.core.dag_events import DAGEventEmitter, DAGEventType


# ============ Test Stations ============


class TextOnlyStation(Station):
    """Station that only accepts TEXT chunks"""

    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]

    def __init__(self, name: str = "TextOnlyStation"):
        super().__init__(name=name)

    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:  # type: ignore[override]
        # Transform text to uppercase
        if chunk.type == ChunkType.TEXT:
            yield TextChunk(
                data=str(chunk.data).upper(),
                source=self.name,
                turn_id=chunk.turn_id,
            )
        else:
            yield chunk


class AudioOnlyStation(Station):
    """Station that only accepts AUDIO chunks"""

    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO]

    def __init__(self, name: str = "AudioOnlyStation"):
        super().__init__(name=name)

    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:  # type: ignore[override]
        # Just pass through audio
        chunk.source = self.name
        yield chunk


class MultiTypeStation(Station):
    """Station that accepts both TEXT and AUDIO"""

    ALLOWED_INPUT_TYPES = [ChunkType.TEXT, ChunkType.AUDIO]

    def __init__(self, name: str = "MultiTypeStation"):
        super().__init__(name=name)

    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:  # type: ignore[override]
        chunk.source = self.name
        yield chunk


class CountingStation(Station):
    """Station that counts processed chunks"""

    ALLOWED_INPUT_TYPES = []  # Accept all

    def __init__(self, name: str = "CountingStation"):
        super().__init__(name=name)
        self.count = 0

    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        self.count += 1
        chunk.source = self.name
        yield chunk


class DelayStation(Station):
    """Station with configurable delay"""

    ALLOWED_INPUT_TYPES = []

    def __init__(self, delay_ms: float = 10, name: str = "DelayStation"):
        super().__init__(name=name)
        self.delay_ms = delay_ms

    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        await asyncio.sleep(self.delay_ms / 1000)
        chunk.source = self.name
        yield chunk


# ============ Test DAG Builder ============


class TestDAGBuilder:
    """Tests for DAG construction"""

    def test_create_empty_dag(self):
        """Test creating empty DAG"""
        dag = DAG("test")
        assert dag.name == "test"
        assert len(dag._nodes) == 0
        assert len(dag._edges) == 0

    def test_add_node(self):
        """Test adding nodes"""
        dag = DAG("test")
        dag.add_node("station1", PassthroughStation())
        dag.add_node("station2", TextOnlyStation())

        assert "station1" in dag._nodes
        assert "station2" in dag._nodes
        assert len(dag._nodes) == 2

    def test_add_duplicate_node_raises(self):
        """Test that adding duplicate node raises error"""
        dag = DAG("test")
        dag.add_node("station1", PassthroughStation())

        with pytest.raises(ValueError, match="already exists"):
            dag.add_node("station1", PassthroughStation())

    def test_add_reserved_node_name_raises(self):
        """Test that transport_in is reserved but transport_out can be bound"""
        dag = DAG("test")

        # transport_in is always reserved (virtual entry)
        with pytest.raises(ValueError, match="reserved"):
            dag.add_node("transport_in", PassthroughStation())

        # transport_out can be bound to an actual OutputStation
        dag.add_node("transport_out", PassthroughStation())
        assert "transport_out" in dag._nodes

    def test_add_single_edge(self):
        """Test adding single edge"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", PassthroughStation())
        dag.add_edge("a", "b")

        assert ("a", "b") in dag._edges

    def test_add_chain_edge(self):
        """Test adding chain of edges"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", PassthroughStation())
        dag.add_node("c", PassthroughStation())
        dag.add_edge("a", "b", "c")

        assert ("a", "b") in dag._edges
        assert ("b", "c") in dag._edges
        assert len(dag._edges) == 2

    def test_add_edge_with_virtual_nodes(self):
        """Test edges with virtual nodes"""
        dag = DAG("test")
        dag.add_node("station", PassthroughStation())
        dag.add_edge("transport_in", "station", "transport_out")

        assert ("transport_in", "station") in dag._edges
        assert ("station", "transport_out") in dag._edges

    def test_add_edge_nonexistent_node_raises(self):
        """Test that edge to nonexistent node raises error"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())

        with pytest.raises(ValueError, match="does not exist"):
            dag.add_edge("a", "nonexistent")

    def test_chaining_api(self):
        """Test fluent API chaining"""
        dag = (
            DAG("test")
            .add_node("a", PassthroughStation())
            .add_node("b", PassthroughStation())
            .add_edge("transport_in", "a", "b", "transport_out")
        )

        assert len(dag._nodes) == 2
        assert len(dag._edges) == 3


# ============ Test DAG Validation ============


class TestDAGValidation:
    """Tests for DAG validation"""

    def test_valid_linear_dag(self):
        """Test valid linear DAG compiles"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", PassthroughStation())
        dag.add_edge("transport_in", "a", "b", "transport_out")

        # Should not raise
        compiled = dag.compile()
        assert compiled is not None

    def test_valid_branching_dag(self):
        """Test valid branching DAG compiles"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", TextOnlyStation())
        dag.add_node("c", AudioOnlyStation())
        dag.add_edge("transport_in", "a")
        dag.add_edge("a", "b", "transport_out")
        dag.add_edge("a", "c", "transport_out")

        compiled = dag.compile()
        assert compiled is not None

    def test_cycle_detection(self):
        """Test that cycles are detected"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", PassthroughStation())
        dag.add_node("c", PassthroughStation())

        # Create a cycle: a -> b -> c -> a
        dag._edges = [("a", "b"), ("b", "c"), ("c", "a")]

        with pytest.raises(DAGValidationError, match="cycle"):
            dag.compile()

    def test_no_entry_nodes_raises(self):
        """Test that DAG without entry nodes raises error"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", PassthroughStation())
        dag.add_edge("a", "b")  # No transport_in

        with pytest.raises(DAGValidationError, match="No entry nodes"):
            dag.compile()


# ============ Test DAGNode ============


class TestDAGNode:
    """Tests for DAGNode"""

    def test_node_creation(self):
        """Test node creation"""
        station = TextOnlyStation()
        node = DAGNode("test", station)

        assert node.name == "test"
        assert node.station is station
        assert node.stats.status == "idle"

    def test_node_accepts_signal(self):
        """Test that nodes always accept signals"""
        station = TextOnlyStation()  # Only accepts TEXT
        node = DAGNode("test", station)

        # Signal should be accepted
        signal_chunk = EventChunk(type=ChunkType.EVENT_VAD_START)
        assert node.accepts(signal_chunk) is True

    def test_node_accepts_allowed_type(self):
        """Test that nodes accept allowed types"""
        station = TextOnlyStation()
        node = DAGNode("test", station)

        text_chunk = TextChunk(data="hello")
        assert node.accepts(text_chunk) is True

    def test_node_rejects_unallowed_type(self):
        """Test that nodes reject unallowed types"""
        station = TextOnlyStation()  # Only accepts TEXT
        node = DAGNode("test", station)

        audio_chunk = AudioChunk(data=b"audio")
        assert node.accepts(audio_chunk) is False

    def test_node_accepts_all_when_empty(self):
        """Test that nodes with empty ALLOWED_INPUT_TYPES accept all"""
        station = CountingStation()  # ALLOWED_INPUT_TYPES = []
        node = DAGNode("test", station)

        text_chunk = TextChunk(data="hello")
        audio_chunk = AudioChunk(data=b"audio")

        assert node.accepts(text_chunk) is True
        assert node.accepts(audio_chunk) is True

    def test_node_to_dict(self):
        """Test node serialization"""
        station = TextOnlyStation()
        node = DAGNode("test", station)
        node.position = {"x": 100, "y": 200}

        data = node.to_dict()
        assert data["id"] == "test"
        assert data["type"] == "TextOnlyStation"
        assert ChunkType.TEXT.value in data["allowed_input_types"]
        assert data["position"] == {"x": 100, "y": 200}


# ============ Test NodeStats ============


class TestNodeStats:
    """Tests for NodeStats"""

    def test_initial_stats(self):
        """Test initial statistics values"""
        stats = NodeStats()
        assert stats.chunks_received == 0
        assert stats.chunks_processed == 0
        assert stats.status == "idle"
        assert stats.avg_latency_ms == 0.0

    def test_record_latency(self):
        """Test latency recording"""
        stats = NodeStats()
        stats.record_latency(10.0)
        stats.record_latency(20.0)
        stats.record_latency(30.0)

        assert stats.avg_latency_ms == 20.0  # (10+20+30)/3

    def test_latency_rolling_average(self):
        """Test that latency uses rolling average"""
        stats = NodeStats()
        stats._max_samples = 3  # Small window for testing

        stats.record_latency(10.0)
        stats.record_latency(20.0)
        stats.record_latency(30.0)
        stats.record_latency(40.0)  # Should push out 10

        # Average of 20, 30, 40
        assert stats.avg_latency_ms == 30.0


# ============ Test CompiledDAG Execution ============


class TestCompiledDAGExecution:
    """Tests for DAG execution"""

    @pytest.mark.asyncio
    async def test_simple_passthrough(self):
        """Test simple passthrough DAG"""
        dag = DAG("test")
        dag.add_node("pass", PassthroughStation())
        dag.add_edge("transport_in", "pass", "transport_out")
        compiled = dag.compile()

        # Create input stream
        async def input_stream():
            yield TextChunk(data="hello", turn_id=1)
            yield TextChunk(data="world", turn_id=1)

        # Collect output
        outputs = []
        async for chunk in compiled.run(input_stream()):
            outputs.append(chunk)

        assert len(outputs) == 2
        assert outputs[0].data == "hello"
        assert outputs[1].data == "world"

    @pytest.mark.asyncio
    async def test_transform_station(self):
        """Test station that transforms data"""
        dag = DAG("test")
        dag.add_node("upper", TextOnlyStation())  # Converts to uppercase
        dag.add_edge("transport_in", "upper", "transport_out")
        compiled = dag.compile()

        async def input_stream():
            yield TextChunk(data="hello", turn_id=1)

        outputs = []
        async for chunk in compiled.run(input_stream()):
            outputs.append(chunk)

        assert len(outputs) == 1
        assert outputs[0].data == "HELLO"

    @pytest.mark.asyncio
    async def test_type_routing(self):
        """Test that chunks are routed by type"""
        dag = DAG("test")
        # Use custom names for stations to match node names
        dag.add_node("text_only", TextOnlyStation(name="text_only"))
        dag.add_node("audio_only", AudioOnlyStation(name="audio_only"))
        dag.add_edge("transport_in", "text_only", "transport_out")
        dag.add_edge("transport_in", "audio_only", "transport_out")
        compiled = dag.compile()

        async def input_stream():
            yield TextChunk(data="hello", turn_id=1)
            yield AudioChunk(data=b"audio", turn_id=1)

        outputs = []
        async for chunk in compiled.run(input_stream()):
            outputs.append(chunk)

        # Both should be processed
        assert len(outputs) == 2

        # Check sources (station names)
        sources = {o.source for o in outputs}
        assert "text_only" in sources
        assert "audio_only" in sources

    @pytest.mark.asyncio
    async def test_signal_broadcast(self):
        """Test that signals are broadcast to all downstream"""
        dag = DAG("test")
        counter1 = CountingStation(name="counter1")
        counter2 = CountingStation(name="counter2")
        dag.add_node("counter1", counter1)
        dag.add_node("counter2", counter2)
        dag.add_edge("transport_in", "counter1", "transport_out")
        dag.add_edge("transport_in", "counter2", "transport_out")
        compiled = dag.compile()

        async def input_stream():
            yield EventChunk(type=ChunkType.EVENT_VAD_START, turn_id=1)

        outputs = []
        async for chunk in compiled.run(input_stream()):
            outputs.append(chunk)

        # Signal should be received by both counters
        assert counter1.count == 1
        assert counter2.count == 1

    @pytest.mark.asyncio
    async def test_chain_processing(self):
        """Test chain of stations"""
        dag = DAG("test")
        counter1 = CountingStation(name="counter1")
        counter2 = CountingStation(name="counter2")
        counter3 = CountingStation(name="counter3")
        dag.add_node("c1", counter1)
        dag.add_node("c2", counter2)
        dag.add_node("c3", counter3)
        dag.add_edge("transport_in", "c1", "c2", "c3", "transport_out")
        compiled = dag.compile()

        async def input_stream():
            yield TextChunk(data="test", turn_id=1)

        outputs = []
        async for chunk in compiled.run(input_stream()):
            outputs.append(chunk)

        # Each counter should process once
        assert counter1.count == 1
        assert counter2.count == 1
        assert counter3.count == 1

        # Output should have source from last station
        assert outputs[0].source == "counter3"

    @pytest.mark.asyncio
    async def test_stats_collection(self):
        """Test that statistics are collected"""
        dag = DAG("test")
        dag.add_node("pass", PassthroughStation())
        dag.add_edge("transport_in", "pass", "transport_out")
        compiled = dag.compile()

        async def input_stream():
            for i in range(5):
                yield TextChunk(data=f"msg{i}", turn_id=1)

        async for _ in compiled.run(input_stream()):
            pass

        stats = compiled.get_stats()
        assert stats["nodes"]["pass"]["chunks_processed"] == 5

    @pytest.mark.asyncio
    async def test_control_signals_go_to_controlbus(self):
        """Test that CONTROL signals don't go through DAG.
        
        CONTROL_STATE_RESET behavior:
        1. Sends interrupt to ControlBus
        2. Clears all DAG queues (discards pending chunks)
        3. Does NOT pass through to stations
        
        So chunks queued BEFORE the CONTROL signal may be discarded.
        """
        from vixio.core.control_bus import ControlBus

        dag = DAG("test")
        counter = CountingStation()
        dag.add_node("counter", counter)
        dag.add_edge("transport_in", "counter", "transport_out")

        control_bus = ControlBus()
        compiled = dag.compile(control_bus=control_bus)

        async def input_stream():
            yield TextChunk(data="text", turn_id=1)
            yield Chunk(type=ChunkType.CONTROL_STATE_RESET, turn_id=1)
            yield TextChunk(data="text2", turn_id=2)

        outputs = []
        async for chunk in compiled.run(input_stream()):
            outputs.append(chunk)

        # text is queued but then cleared by CONTROL_STATE_RESET
        # Only text2 (after interrupt) is actually processed
        # This is correct behavior: interrupt discards pending data
        assert counter.count == 1
        
        # Verify interrupt was sent
        assert control_bus.get_current_turn_id() >= 1

    @pytest.mark.asyncio
    async def test_to_dict(self):
        """Test DAG serialization"""
        dag = DAG("test")
        dag.add_node("a", PassthroughStation())
        dag.add_node("b", TextOnlyStation())
        dag.add_edge("transport_in", "a", "b", "transport_out")
        compiled = dag.compile()

        data = compiled.to_dict()
        assert data["name"] == "test"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 3


# ============ Test DAGEventEmitter ============


class TestDAGEventEmitter:
    """Tests for event emitter"""

    @pytest.mark.asyncio
    async def test_event_subscription(self):
        """Test event subscription and emission"""
        emitter = DAGEventEmitter()
        received_events = []

        async def listener(event):
            received_events.append(event)

        emitter.subscribe(listener)
        await emitter.start()

        # Emit event
        chunk = TextChunk(data="test")
        await emitter.emit_chunk_processed("node1", chunk)

        # Wait for dispatch
        await asyncio.sleep(0.2)
        await emitter.stop()

        assert len(received_events) == 1
        assert received_events[0].type == DAGEventType.CHUNK_PROCESSED
        assert received_events[0].node_name == "node1"

    @pytest.mark.asyncio
    async def test_sync_listener(self):
        """Test synchronous listener"""
        emitter = DAGEventEmitter()
        received_events = []

        def sync_listener(event):
            received_events.append(event)

        emitter.subscribe(sync_listener)
        await emitter.start()

        chunk = TextChunk(data="test")
        await emitter.emit_chunk_processed("node1", chunk)

        await asyncio.sleep(0.2)
        await emitter.stop()

        assert len(received_events) == 1

    @pytest.mark.asyncio
    async def test_edge_active_event(self):
        """Test edge activation event"""
        emitter = DAGEventEmitter()
        received_events = []

        async def listener(event):
            received_events.append(event)

        emitter.subscribe(listener)
        await emitter.start()

        chunk = TextChunk(data="test")
        await emitter.emit_edge_active("node1", "node2", chunk)

        await asyncio.sleep(0.2)
        await emitter.stop()

        assert len(received_events) == 1
        assert received_events[0].type == DAGEventType.EDGE_ACTIVE
        assert received_events[0].data["target_node"] == "node2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
