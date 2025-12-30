"""
Unit tests for ControlBus - centralized control signal management.

Tests cover:
- Turn ID management
- Interrupt signal handling
- Interrupt handlers registration
- Turn timeout detection
- Concurrent access
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from vixio.core.control_bus import ControlBus, InterruptSignal


class TestControlBusBasic:
    """Tests for basic ControlBus operations."""

    def test_initial_state(self):
        """Test initial ControlBus state."""
        bus = ControlBus()
        
        assert bus.get_current_turn_id() == 0
        assert bus.get_latest_interrupt() is None
        assert not bus.check_interrupt_event()

    def test_initial_state_with_timeout(self):
        """Test initial state with turn timeout configured."""
        bus = ControlBus(turn_timeout_seconds=30.0)
        
        assert bus._turn_timeout_seconds == 30.0
        assert bus.get_current_turn_id() == 0

    @pytest.mark.asyncio
    async def test_increment_turn(self):
        """Test turn ID increment."""
        bus = ControlBus()
        
        new_turn = await bus.increment_turn(source="test", reason="test_reason")
        
        assert new_turn == 1
        assert bus.get_current_turn_id() == 1

    @pytest.mark.asyncio
    async def test_increment_turn_multiple(self):
        """Test multiple turn increments."""
        bus = ControlBus()
        
        for i in range(5):
            new_turn = await bus.increment_turn(source="test", reason=f"turn_{i}")
            assert new_turn == i + 1
        
        assert bus.get_current_turn_id() == 5


class TestInterruptSignal:
    """Tests for InterruptSignal dataclass."""

    def test_interrupt_signal_creation(self):
        """Test InterruptSignal creation."""
        signal = InterruptSignal(
            source="vad",
            reason="user_interrupted",
            turn_id=5,
            metadata={"extra": "data"}
        )
        
        assert signal.source == "vad"
        assert signal.reason == "user_interrupted"
        assert signal.turn_id == 5
        assert signal.metadata == {"extra": "data"}
        assert signal.timestamp > 0

    def test_interrupt_signal_str(self):
        """Test InterruptSignal string representation."""
        signal = InterruptSignal(
            source="transport",
            reason="client_abort",
            turn_id=3
        )
        
        str_repr = str(signal)
        assert "transport" in str_repr
        assert "client_abort" in str_repr
        assert "3" in str_repr


class TestInterruptHandlers:
    """Tests for interrupt handler registration and execution."""

    @pytest.mark.asyncio
    async def test_register_sync_handler(self):
        """Test registering synchronous interrupt handler."""
        bus = ControlBus()
        received_signals = []
        
        def sync_handler(signal):
            received_signals.append(signal)
        
        bus.register_interrupt_handler(sync_handler)
        
        await bus.send_interrupt(source="test", reason="test_interrupt")
        
        assert len(received_signals) == 1
        assert received_signals[0].source == "test"
        assert received_signals[0].reason == "test_interrupt"

    @pytest.mark.asyncio
    async def test_register_async_handler(self):
        """Test registering asynchronous interrupt handler."""
        bus = ControlBus()
        received_signals = []
        
        async def async_handler(signal):
            await asyncio.sleep(0.01)  # Simulate async work
            received_signals.append(signal)
        
        bus.register_interrupt_handler(async_handler)
        
        await bus.send_interrupt(source="agent", reason="processing_error")
        
        assert len(received_signals) == 1
        assert received_signals[0].source == "agent"

    @pytest.mark.asyncio
    async def test_multiple_handlers(self):
        """Test multiple handlers receive same interrupt."""
        bus = ControlBus()
        handler1_calls = []
        handler2_calls = []
        
        def handler1(signal):
            handler1_calls.append(signal)
        
        async def handler2(signal):
            handler2_calls.append(signal)
        
        bus.register_interrupt_handler(handler1)
        bus.register_interrupt_handler(handler2)
        
        await bus.send_interrupt(source="vad", reason="speech_detected")
        
        assert len(handler1_calls) == 1
        assert len(handler2_calls) == 1
        assert handler1_calls[0].source == handler2_calls[0].source

    @pytest.mark.asyncio
    async def test_unregister_handler(self):
        """Test unregistering interrupt handler."""
        bus = ControlBus()
        received_signals = []
        
        def handler(signal):
            received_signals.append(signal)
        
        bus.register_interrupt_handler(handler)
        await bus.send_interrupt(source="test", reason="first")
        
        bus.unregister_interrupt_handler(handler)
        await bus.send_interrupt(source="test", reason="second")
        
        # Only first interrupt should be received
        assert len(received_signals) == 1
        assert received_signals[0].reason == "first"

    @pytest.mark.asyncio
    async def test_handler_exception_doesnt_break_others(self):
        """Test that exception in one handler doesn't prevent others."""
        bus = ControlBus()
        successful_calls = []
        
        def failing_handler(signal):
            raise ValueError("Handler error")
        
        def successful_handler(signal):
            successful_calls.append(signal)
        
        bus.register_interrupt_handler(failing_handler)
        bus.register_interrupt_handler(successful_handler)
        
        # Should not raise, and second handler should still be called
        await bus.send_interrupt(source="test", reason="test")
        
        assert len(successful_calls) == 1

    @pytest.mark.asyncio
    async def test_duplicate_handler_registration(self):
        """Test that duplicate handlers are not registered twice."""
        bus = ControlBus()
        call_count = 0
        
        def handler(signal):
            nonlocal call_count
            call_count += 1
        
        bus.register_interrupt_handler(handler)
        bus.register_interrupt_handler(handler)  # Duplicate
        
        await bus.send_interrupt(source="test", reason="test")
        
        # Handler should only be called once
        assert call_count == 1


class TestSendInterrupt:
    """Tests for send_interrupt functionality."""

    @pytest.mark.asyncio
    async def test_send_interrupt_increments_turn(self):
        """Test that send_interrupt increments turn ID."""
        bus = ControlBus()
        
        assert bus.get_current_turn_id() == 0
        
        await bus.send_interrupt(source="vad", reason="user_interrupt")
        
        assert bus.get_current_turn_id() == 1

    @pytest.mark.asyncio
    async def test_send_interrupt_sets_event(self):
        """Test that send_interrupt sets event flag."""
        bus = ControlBus()
        
        assert not bus.check_interrupt_event()
        
        await bus.send_interrupt(source="test", reason="test")
        
        assert bus.check_interrupt_event()

    @pytest.mark.asyncio
    async def test_send_interrupt_with_metadata(self):
        """Test send_interrupt with metadata."""
        bus = ControlBus()
        received_signals = []
        
        def handler(signal):
            received_signals.append(signal)
        
        bus.register_interrupt_handler(handler)
        
        await bus.send_interrupt(
            source="tts",
            reason="timeout",
            metadata={"elapsed_seconds": 5.0, "max_seconds": 10.0}
        )
        
        assert received_signals[0].metadata["elapsed_seconds"] == 5.0
        assert received_signals[0].metadata["max_seconds"] == 10.0

    @pytest.mark.asyncio
    async def test_wait_for_interrupt(self):
        """Test waiting for interrupt signal."""
        bus = ControlBus()
        
        # Send interrupt in background
        async def send_later():
            await asyncio.sleep(0.05)
            await bus.send_interrupt(source="delayed", reason="delayed_interrupt")
        
        task = asyncio.create_task(send_later())
        
        # Wait for interrupt
        signal = await bus.wait_for_interrupt()
        
        assert signal.source == "delayed"
        assert signal.reason == "delayed_interrupt"
        
        await task

    @pytest.mark.asyncio
    async def test_clear_interrupt_event(self):
        """Test clearing interrupt event flag."""
        bus = ControlBus()
        
        await bus.send_interrupt(source="test", reason="test")
        assert bus.check_interrupt_event()
        
        bus.clear_interrupt_event()
        assert not bus.check_interrupt_event()


class TestTurnTimeout:
    """Tests for turn timeout functionality."""

    @pytest.mark.asyncio
    async def test_timeout_triggers_interrupt(self):
        """Test that turn timeout triggers interrupt."""
        bus = ControlBus(turn_timeout_seconds=0.1)  # 100ms for testing
        received_signals = []
        
        def handler(signal):
            received_signals.append(signal)
        
        bus.register_interrupt_handler(handler)
        
        # Increment turn to start timeout
        await bus.increment_turn(source="test", reason="start_turn")
        
        # Wait for timeout
        await asyncio.sleep(0.2)
        
        # Check that timeout interrupt was sent
        timeout_signals = [s for s in received_signals if s.reason == "turn_timeout"]
        assert len(timeout_signals) == 1

    @pytest.mark.asyncio
    async def test_cancel_turn_timeout(self):
        """Test cancelling turn timeout."""
        bus = ControlBus(turn_timeout_seconds=0.1)
        received_signals = []
        
        def handler(signal):
            received_signals.append(signal)
        
        bus.register_interrupt_handler(handler)
        
        # Increment turn to start timeout
        await bus.increment_turn(source="test", reason="start_turn")
        
        # Cancel timeout immediately
        bus.cancel_turn_timeout()
        
        # Wait past timeout period
        await asyncio.sleep(0.15)
        
        # No timeout interrupt should have been sent
        timeout_signals = [s for s in received_signals if s.reason == "turn_timeout"]
        assert len(timeout_signals) == 0

    @pytest.mark.asyncio
    async def test_cleanup_cancels_timeout(self):
        """Test that cleanup cancels pending timeout task."""
        bus = ControlBus(turn_timeout_seconds=0.5)
        
        await bus.increment_turn(source="test", reason="start_turn")
        
        # Timeout task should be running
        assert bus._timeout_task is not None
        assert not bus._timeout_task.done()
        
        # Cleanup
        bus.cleanup()
        
        # Wait a bit for task cancellation
        await asyncio.sleep(0.05)
        
        # Task should be cancelled
        assert bus._timeout_task.done() or bus._timeout_task.cancelled()

    def test_no_timeout_when_not_configured(self):
        """Test that no timeout happens when not configured."""
        bus = ControlBus()  # No timeout
        
        assert bus._turn_timeout_seconds is None
        assert bus._timeout_task is None


class TestConcurrency:
    """Tests for concurrent access to ControlBus."""

    @pytest.mark.asyncio
    async def test_concurrent_turn_increments(self):
        """Test concurrent turn increments are serialized."""
        bus = ControlBus()
        
        async def increment_task():
            for _ in range(10):
                await bus.increment_turn(source="concurrent", reason="test")
                await asyncio.sleep(0.001)
        
        # Run 5 concurrent increment tasks
        tasks = [asyncio.create_task(increment_task()) for _ in range(5)]
        await asyncio.gather(*tasks)
        
        # Should have 50 increments total
        assert bus.get_current_turn_id() == 50

    @pytest.mark.asyncio
    async def test_concurrent_interrupt_sends(self):
        """Test concurrent interrupt sends."""
        bus = ControlBus()
        received_count = 0
        
        def handler(signal):
            nonlocal received_count
            received_count += 1
        
        bus.register_interrupt_handler(handler)
        
        async def send_interrupt_task(source):
            await bus.send_interrupt(source=source, reason="concurrent")
        
        # Send 10 concurrent interrupts
        tasks = [
            asyncio.create_task(send_interrupt_task(f"source_{i}"))
            for i in range(10)
        ]
        await asyncio.gather(*tasks)
        
        # All handlers should have been called
        assert received_count == 10


class TestStats:
    """Tests for ControlBus statistics."""

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Test getting statistics."""
        bus = ControlBus()
        
        await bus.send_interrupt(source="test", reason="test")
        
        stats = bus.get_stats()
        
        assert stats["current_turn_id"] == 1
        assert "pending_interrupts" in stats
        assert stats["interrupt_event_set"] is True
        # Note: latest_interrupt is set only after wait_for_interrupt is called
        # Since we didn't call wait_for_interrupt, it may be None
        assert stats["pending_interrupts"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
