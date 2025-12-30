"""
Unit tests for Middleware system.

Tests cover:
- InterruptDetectorMiddleware
- TimeoutHandlerMiddleware
- MiddlewareChain execution
- DataMiddleware/SignalMiddleware routing
"""

import asyncio
import pytest
from typing import AsyncIterator
from unittest.mock import MagicMock, AsyncMock, patch

from vixio.core.chunk import (
    Chunk, ChunkType, TextChunk, TextDeltaChunk,
    AudioChunk, EventChunk
)
from vixio.core.control_bus import ControlBus
from vixio.core.middleware import (
    Middleware, DataMiddleware, SignalMiddleware,
    MiddlewareChain, NextHandler
)
from vixio.stations.middlewares.interrupt_detector import InterruptDetectorMiddleware
from vixio.stations.middlewares.timeout_handler import TimeoutHandlerMiddleware


# ============ Mock Helpers ============

class MockStation:
    """Mock station for testing middlewares."""
    
    def __init__(self, name: str = "MockStation"):
        self.name = name
        self.control_bus = ControlBus()
        self.current_turn_id = 0


async def simple_next_handler(chunk: Chunk) -> AsyncIterator[Chunk]:
    """Simple passthrough next handler."""
    yield chunk


async def delayed_next_handler(chunk: Chunk, delay: float = 0.1) -> AsyncIterator[Chunk]:
    """Next handler with delay."""
    await asyncio.sleep(delay)
    yield chunk


async def multi_chunk_handler(chunk: Chunk, count: int = 3) -> AsyncIterator[Chunk]:
    """Next handler that yields multiple chunks."""
    for i in range(count):
        new_chunk = TextDeltaChunk(
            data=f"chunk_{i}",
            turn_id=chunk.turn_id,
            session_id=chunk.session_id
        )
        yield new_chunk


# ============ Test InterruptDetectorMiddleware ============

class TestInterruptDetectorMiddleware:
    """Tests for InterruptDetectorMiddleware."""

    def test_initialization(self):
        """Test middleware initialization."""
        middleware = InterruptDetectorMiddleware(check_interval=5)
        
        assert middleware.check_interval == 5
        assert middleware.name == "InterruptDetector"

    def test_attach_to_station(self):
        """Test attaching middleware to station."""
        middleware = InterruptDetectorMiddleware()
        station = MockStation()
        
        middleware.attach(station)
        
        assert middleware.station is station

    @pytest.mark.asyncio
    async def test_passthrough_when_no_interrupt(self):
        """Test chunks pass through when no interrupt."""
        middleware = InterruptDetectorMiddleware()
        station = MockStation()
        station.current_turn_id = 1
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in middleware.process_data(chunk, simple_next_handler):
            results.append(result)
        
        assert len(results) == 1
        assert results[0].data == "test"

    @pytest.mark.asyncio
    async def test_stop_on_interrupt(self):
        """Test processing stops when turn ID changes.
        
        InterruptDetector compares station.current_turn_id with control_bus.get_current_turn_id().
        When control_bus turn_id > station.current_turn_id, interrupt is detected.
        """
        middleware = InterruptDetectorMiddleware(check_interval=1)
        station = MockStation()
        # Station starts at turn 0, control_bus also starts at 0
        station.current_turn_id = 0
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=0)
        interrupt_at_chunk = 2
        
        async def handler_with_interrupt(c):
            for i in range(10):
                yield TextDeltaChunk(data=f"chunk_{i}", turn_id=0)
                if i == interrupt_at_chunk:
                    # Simulate interrupt: control bus turn increments to 1
                    # Now control_bus turn_id (1) > station.current_turn_id (0)
                    await station.control_bus.increment_turn("test", "interrupt")
        
        results = []
        async for result in middleware.process_data(chunk, handler_with_interrupt):
            results.append(result)
        
        # Interrupt happens AFTER chunk 2 is yielded
        # Detection happens when checking after receiving chunk 3 (4th chunk)
        # So we get chunks 0, 1, 2, 3 at most before break
        assert len(results) < 10, f"Should stop before all 10 chunks, got {len(results)}"
        assert len(results) >= 3, f"Should have at least 3 chunks before interrupt, got {len(results)}"

    @pytest.mark.asyncio
    async def test_check_interval(self):
        """Test interrupt check happens at interval."""
        middleware = InterruptDetectorMiddleware(check_interval=3)
        station = MockStation()
        station.current_turn_id = 1
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1)
        check_count = 0
        
        original_is_interrupted = middleware._is_interrupted
        
        def counting_is_interrupted():
            nonlocal check_count
            check_count += 1
            return original_is_interrupted()
        
        middleware._is_interrupted = counting_is_interrupted
        
        async def multi_handler(c):
            for i in range(10):
                yield TextDeltaChunk(data=f"chunk_{i}", turn_id=1)
        
        results = []
        async for result in middleware.process_data(chunk, multi_handler):
            results.append(result)
        
        # Should check at intervals: chunks 3, 6, 9 (3 checks for 10 chunks)
        assert check_count == 3

    @pytest.mark.asyncio
    async def test_signal_passthrough(self):
        """Test signal chunks bypass data processing."""
        middleware = InterruptDetectorMiddleware()
        station = MockStation()
        middleware.attach(station)
        
        signal = EventChunk(type=ChunkType.EVENT_VAD_START, turn_id=1)
        
        results = []
        async for result in middleware.process(signal, simple_next_handler):
            results.append(result)
        
        assert len(results) == 1
        assert results[0].type == ChunkType.EVENT_VAD_START

    @pytest.mark.asyncio
    async def test_no_station_no_interrupt(self):
        """Test no false interrupt when station not attached."""
        middleware = InterruptDetectorMiddleware()
        # Not attached to station
        
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in middleware.process_data(chunk, simple_next_handler):
            results.append(result)
        
        assert len(results) == 1


# ============ Test TimeoutHandlerMiddleware ============

class TestTimeoutHandlerMiddleware:
    """Tests for TimeoutHandlerMiddleware."""

    def test_initialization(self):
        """Test middleware initialization."""
        middleware = TimeoutHandlerMiddleware(
            timeout_seconds=30.0,
            emit_timeout_event=True,
            send_interrupt_signal=False
        )
        
        assert middleware.timeout_seconds == 30.0
        assert middleware.emit_timeout_event is True
        assert middleware.send_interrupt_signal is False

    def test_no_timeout_configured(self):
        """Test initialization with no timeout."""
        middleware = TimeoutHandlerMiddleware()
        
        assert middleware.timeout_seconds is None

    @pytest.mark.asyncio
    async def test_passthrough_when_no_timeout(self):
        """Test chunks pass through when timeout not configured."""
        middleware = TimeoutHandlerMiddleware(timeout_seconds=None)
        station = MockStation()
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in middleware.process_data(chunk, simple_next_handler):
            results.append(result)
        
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_passthrough_within_timeout(self):
        """Test chunks pass through when within timeout."""
        middleware = TimeoutHandlerMiddleware(timeout_seconds=1.0)
        station = MockStation()
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1)
        
        async def fast_handler(c):
            await asyncio.sleep(0.01)
            yield c
        
        results = []
        async for result in middleware.process_data(chunk, fast_handler):
            results.append(result)
        
        assert len(results) == 1
        assert results[0].data == "test"

    @pytest.mark.asyncio
    async def test_timeout_stops_processing(self):
        """Test processing stops on timeout."""
        middleware = TimeoutHandlerMiddleware(
            timeout_seconds=0.1,
            emit_timeout_event=True,
            send_interrupt_signal=False  # Disable to simplify test
        )
        station = MockStation()
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1, session_id="test-session")
        
        async def slow_handler(c):
            for i in range(10):
                await asyncio.sleep(0.05)  # 50ms each, total 500ms
                yield TextDeltaChunk(data=f"chunk_{i}", turn_id=1)
        
        results = []
        async for result in middleware.process_data(chunk, slow_handler):
            results.append(result)
        
        # Should have stopped before all 10 chunks, plus timeout event
        data_chunks = [r for r in results if r.type != ChunkType.EVENT_TIMEOUT]
        timeout_chunks = [r for r in results if r.type == ChunkType.EVENT_TIMEOUT]
        
        assert len(data_chunks) < 10
        assert len(timeout_chunks) == 1

    @pytest.mark.asyncio
    async def test_timeout_emits_event(self):
        """Test timeout emits EVENT_TIMEOUT when configured."""
        middleware = TimeoutHandlerMiddleware(
            timeout_seconds=0.05,
            emit_timeout_event=True,
            send_interrupt_signal=False
        )
        station = MockStation()
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1, session_id="test-session")
        
        async def blocking_handler(c):
            await asyncio.sleep(1.0)  # Will timeout
            yield c
        
        results = []
        async for result in middleware.process_data(chunk, blocking_handler):
            results.append(result)
        
        assert len(results) == 1
        assert results[0].type == ChunkType.EVENT_TIMEOUT
        assert results[0].event_data["source"] == "MockStation"

    @pytest.mark.asyncio
    async def test_timeout_sends_interrupt_signal(self):
        """Test timeout sends interrupt signal when configured."""
        middleware = TimeoutHandlerMiddleware(
            timeout_seconds=0.05,
            emit_timeout_event=False,
            send_interrupt_signal=True
        )
        station = MockStation()
        middleware.attach(station)
        
        interrupt_received = []
        
        def handler(signal):
            interrupt_received.append(signal)
        
        station.control_bus.register_interrupt_handler(handler)
        
        chunk = TextChunk(data="test", turn_id=1, session_id="test-session")
        
        async def blocking_handler(c):
            await asyncio.sleep(1.0)  # Will timeout
            yield c
        
        results = []
        async for result in middleware.process_data(chunk, blocking_handler):
            results.append(result)
        
        assert len(interrupt_received) == 1
        assert interrupt_received[0].reason == "timeout"

    @pytest.mark.asyncio
    async def test_no_event_when_disabled(self):
        """Test no timeout event when disabled."""
        middleware = TimeoutHandlerMiddleware(
            timeout_seconds=0.05,
            emit_timeout_event=False,
            send_interrupt_signal=False
        )
        station = MockStation()
        middleware.attach(station)
        
        chunk = TextChunk(data="test", turn_id=1, session_id="test-session")
        
        async def blocking_handler(c):
            await asyncio.sleep(1.0)
            yield c
        
        results = []
        async for result in middleware.process_data(chunk, blocking_handler):
            results.append(result)
        
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_signal_passthrough(self):
        """Test signal chunks bypass timeout logic."""
        middleware = TimeoutHandlerMiddleware(timeout_seconds=0.01)
        station = MockStation()
        middleware.attach(station)
        
        async def slow_signal_handler(c):
            await asyncio.sleep(0.1)  # Would timeout if checked
            yield c
        
        signal = EventChunk(type=ChunkType.EVENT_VAD_START, turn_id=1)
        
        results = []
        async for result in middleware.process(signal, slow_signal_handler):
            results.append(result)
        
        # Signal should pass through even though it took longer than timeout
        assert len(results) == 1
        assert results[0].type == ChunkType.EVENT_VAD_START


# ============ Test MiddlewareChain ============

class TestMiddlewareChain:
    """Tests for MiddlewareChain execution."""

    @pytest.mark.asyncio
    async def test_empty_chain(self):
        """Test chain with no middlewares."""
        chain = MiddlewareChain([], simple_next_handler)
        
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in chain.execute(chunk):
            results.append(result)
        
        assert len(results) == 1
        assert results[0].data == "test"

    @pytest.mark.asyncio
    async def test_single_middleware(self):
        """Test chain with single middleware."""
        class PassthroughMiddleware(Middleware):
            async def process(self, chunk, next_handler):
                async for result in next_handler(chunk):
                    yield result
        
        middleware = PassthroughMiddleware()
        chain = MiddlewareChain([middleware], simple_next_handler)
        
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in chain.execute(chunk):
            results.append(result)
        
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_middleware_order(self):
        """Test middlewares execute in correct order."""
        execution_order = []
        
        class OrderTrackingMiddleware(Middleware):
            def __init__(self, order_id):
                super().__init__(name=f"Order_{order_id}")
                self.order_id = order_id
            
            async def process(self, chunk, next_handler):
                execution_order.append(f"before_{self.order_id}")
                async for result in next_handler(chunk):
                    execution_order.append(f"after_{self.order_id}")
                    yield result
        
        middlewares = [
            OrderTrackingMiddleware(1),
            OrderTrackingMiddleware(2),
            OrderTrackingMiddleware(3),
        ]
        
        chain = MiddlewareChain(middlewares, simple_next_handler)
        
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in chain.execute(chunk):
            results.append(result)
        
        # Should execute: before_1, before_2, before_3, handler, after_3, after_2, after_1
        assert execution_order == [
            "before_1", "before_2", "before_3",
            "after_3", "after_2", "after_1"
        ]

    @pytest.mark.asyncio
    async def test_middleware_can_transform_chunk(self):
        """Test middleware can transform chunks."""
        class UppercaseMiddleware(Middleware):
            async def process(self, chunk, next_handler):
                # Transform input
                if hasattr(chunk, 'data') and isinstance(chunk.data, str):
                    chunk.data = chunk.data.upper()
                
                async for result in next_handler(chunk):
                    yield result
        
        middleware = UppercaseMiddleware()
        chain = MiddlewareChain([middleware], simple_next_handler)
        
        chunk = TextChunk(data="hello", turn_id=1)
        
        results = []
        async for result in chain.execute(chunk):
            results.append(result)
        
        assert results[0].data == "HELLO"

    @pytest.mark.asyncio
    async def test_middleware_can_add_chunks(self):
        """Test middleware can add additional chunks."""
        class AddPrefixMiddleware(Middleware):
            async def process(self, chunk, next_handler):
                # Add prefix chunk
                yield TextDeltaChunk(data="[START]", turn_id=chunk.turn_id)
                
                async for result in next_handler(chunk):
                    yield result
                
                # Add suffix chunk
                yield TextDeltaChunk(data="[END]", turn_id=chunk.turn_id)
        
        middleware = AddPrefixMiddleware()
        chain = MiddlewareChain([middleware], simple_next_handler)
        
        chunk = TextChunk(data="content", turn_id=1)
        
        results = []
        async for result in chain.execute(chunk):
            results.append(result)
        
        assert len(results) == 3
        assert results[0].data == "[START]"
        assert results[1].data == "content"
        assert results[2].data == "[END]"

    @pytest.mark.asyncio
    async def test_middleware_can_short_circuit(self):
        """Test middleware can short-circuit chain."""
        class BlockingMiddleware(Middleware):
            async def process(self, chunk, next_handler):
                # Don't call next_handler, just yield replacement
                yield TextChunk(data="blocked", turn_id=chunk.turn_id)
        
        middleware = BlockingMiddleware()
        chain = MiddlewareChain([middleware], simple_next_handler)
        
        chunk = TextChunk(data="original", turn_id=1)
        
        results = []
        async for result in chain.execute(chunk):
            results.append(result)
        
        assert len(results) == 1
        assert results[0].data == "blocked"


# ============ Test DataMiddleware ============

class TestDataMiddleware:
    """Tests for DataMiddleware base class."""

    @pytest.mark.asyncio
    async def test_routes_data_chunks(self):
        """Test data chunks go to process_data."""
        class TestDataMiddleware(DataMiddleware):
            def __init__(self):
                super().__init__()
                self.data_processed = False
            
            async def process_data(self, chunk, next_handler):
                self.data_processed = True
                async for result in next_handler(chunk):
                    yield result
        
        middleware = TestDataMiddleware()
        chunk = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in middleware.process(chunk, simple_next_handler):
            results.append(result)
        
        assert middleware.data_processed is True

    @pytest.mark.asyncio
    async def test_passes_signals_unchanged(self):
        """Test signal chunks pass through unchanged."""
        class TestDataMiddleware(DataMiddleware):
            def __init__(self):
                super().__init__()
                self.data_processed = False
            
            async def process_data(self, chunk, next_handler):
                self.data_processed = True
                async for result in next_handler(chunk):
                    yield result
        
        middleware = TestDataMiddleware()
        signal = EventChunk(type=ChunkType.EVENT_VAD_START, turn_id=1)
        
        results = []
        async for result in middleware.process(signal, simple_next_handler):
            results.append(result)
        
        assert middleware.data_processed is False
        assert len(results) == 1
        assert results[0].type == ChunkType.EVENT_VAD_START


# ============ Test SignalMiddleware ============

class TestSignalMiddleware:
    """Tests for SignalMiddleware base class."""

    @pytest.mark.asyncio
    async def test_routes_signal_chunks(self):
        """Test signal chunks go to process_signal."""
        class TestSignalMiddleware(SignalMiddleware):
            def __init__(self):
                super().__init__()
                self.signal_processed = False
            
            async def process_signal(self, chunk, next_handler):
                self.signal_processed = True
                async for result in next_handler(chunk):
                    yield result
        
        middleware = TestSignalMiddleware()
        signal = EventChunk(type=ChunkType.EVENT_VAD_START, turn_id=1)
        
        results = []
        async for result in middleware.process(signal, simple_next_handler):
            results.append(result)
        
        assert middleware.signal_processed is True

    @pytest.mark.asyncio
    async def test_passes_data_unchanged(self):
        """Test data chunks pass through unchanged."""
        class TestSignalMiddleware(SignalMiddleware):
            def __init__(self):
                super().__init__()
                self.signal_processed = False
            
            async def process_signal(self, chunk, next_handler):
                self.signal_processed = True
                async for result in next_handler(chunk):
                    yield result
        
        middleware = TestSignalMiddleware()
        data = TextChunk(data="test", turn_id=1)
        
        results = []
        async for result in middleware.process(data, simple_next_handler):
            results.append(result)
        
        assert middleware.signal_processed is False
        assert len(results) == 1
        assert results[0].data == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
