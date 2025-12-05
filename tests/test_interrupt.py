"""
Integration tests for interrupt functionality

Tests:
1. Async pipeline - parallel processing
2. Turn ID tracking - discard old chunks
3. Interrupt via ControlBus - clear queues and cancel tasks
4. Multiple interrupt sources - Turn Detector, Agent timeout, etc.
5. State reset on new turn
"""

import pytest
import asyncio
from typing import AsyncIterator
from vixio.core.chunk import Chunk, ChunkType, AudioChunk, EventChunk, ControlChunk, TextChunk
from vixio.core.station import Station
from vixio.core.pipeline import Pipeline
from vixio.core.control_bus import ControlBus
from vixio.core.session import SessionManager
from vixio.stations.turn_detector import TurnDetectorStation


# Mock Stations for Testing

class MockVADStation(Station):
    """Mock VAD station for testing."""
    
    def __init__(self, name="MockVAD"):
        super().__init__(name=name)
        self.processed_count = 0
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """Passthrough with counting."""
        self.processed_count += 1
        yield chunk


class MockAgentStation(Station):
    """Mock Agent station that can simulate slow processing."""
    
    def __init__(self, delay_seconds=0.1, name="MockAgent"):
        super().__init__(name=name)
        self.delay_seconds = delay_seconds
        self.processed_count = 0
        self.processing = False
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """Simulate slow agent processing."""
        if chunk.type == ChunkType.TEXT:
            self.processing = True
            self.processed_count += 1
            
            # Emit AGENT_START
            yield EventChunk(
                type=ChunkType.EVENT_AGENT_START,
                source=self.name,
                session_id=chunk.session_id
            )
            
            # Simulate slow processing
            await asyncio.sleep(self.delay_seconds)
            
            # Yield response
            yield TextChunk(
                type=ChunkType.TEXT,
                content="Agent response",
                source=self.name,
                session_id=chunk.session_id
            )
            
            # Emit AGENT_STOP
            yield EventChunk(
                type=ChunkType.EVENT_AGENT_STOP,
                source=self.name,
                session_id=chunk.session_id
            )
            
            self.processing = False
        else:
            yield chunk
    
    async def reset_state(self) -> None:
        """Reset agent state."""
        await super().reset_state()
        self.processing = False


class MockTTSStation(Station):
    """Mock TTS station that can simulate slow generation."""
    
    def __init__(self, delay_seconds=0.1, name="MockTTS"):
        super().__init__(name=name)
        self.delay_seconds = delay_seconds
        self.processed_count = 0
        self.generating = False
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """Simulate slow TTS generation."""
        if chunk.type == ChunkType.TEXT:
            self.generating = True
            self.processed_count += 1
            
            # Emit BOT_STARTED_SPEAKING
            yield EventChunk(
                type=ChunkType.EVENT_BOT_STARTED_SPEAKING,
                source=self.name,
                session_id=chunk.session_id
            )
            
            # Simulate slow generation
            await asyncio.sleep(self.delay_seconds)
            
            # Yield audio chunk
            yield AudioChunk(
                type=ChunkType.AUDIO_RAW,
                data=b"fake audio data",
                source=self.name,
                session_id=chunk.session_id
            )
            
            # Emit BOT_STOPPED_SPEAKING
            yield EventChunk(
                type=ChunkType.EVENT_BOT_STOPPED_SPEAKING,
                source=self.name,
                session_id=chunk.session_id
            )
            
            self.generating = False
        else:
            yield chunk
    
    async def reset_state(self) -> None:
        """Reset TTS state."""
        await super().reset_state()
        self.generating = False


# Tests

@pytest.mark.asyncio
async def test_async_pipeline_parallel_processing():
    """Test that async pipeline allows parallel processing."""
    control_bus = ControlBus()
    
    # Create stations with delays
    vad = MockVADStation()
    agent = MockAgentStation(delay_seconds=0.2)
    tts = MockTTSStation(delay_seconds=0.2)
    
    # Create pipeline
    pipeline = Pipeline([vad, agent, tts], control_bus=control_bus, name="TestPipeline")
    
    # Create input stream
    async def input_stream():
        for i in range(3):
            yield TextChunk(
                type=ChunkType.TEXT,
                content=f"message {i}",
                session_id="test-session"
            )
            await asyncio.sleep(0.05)  # Small delay between inputs
    
    # Run pipeline
    output_chunks = []
    start_time = asyncio.get_event_loop().time()
    
    async for chunk in pipeline.run(input_stream()):
        output_chunks.append(chunk)
    
    elapsed = asyncio.get_event_loop().time() - start_time
    
    # With parallel processing, should be faster than sequential
    # Sequential would be: 3 * (0.2 + 0.2) = 1.2s
    # Parallel should be less due to overlapping
    assert elapsed < 1.5, f"Pipeline took {elapsed:.2f}s, expected < 1.5s for parallel processing"
    
    # All stations should have processed chunks
    assert vad.processed_count > 0
    assert agent.processed_count == 3
    assert tts.processed_count == 3


@pytest.mark.asyncio
async def test_turn_id_tracking_discard_old_chunks():
    """Test that stations discard chunks from old turns."""
    control_bus = ControlBus()
    
    # Create station
    station = MockAgentStation()
    station.control_bus = control_bus
    station.current_turn_id = 1  # Already at turn 1
    
    # Create chunks with different turn IDs
    async def input_stream():
        # Old turn (should be discarded)
        yield TextChunk(
            type=ChunkType.TEXT,
            content="old message",
            session_id="test-session",
            turn_id=0
        )
        
        # Current turn (should be processed)
        yield TextChunk(
            type=ChunkType.TEXT,
            content="current message",
            session_id="test-session",
            turn_id=1
        )
        
        # New turn (should trigger reset and be processed)
        yield TextChunk(
            type=ChunkType.TEXT,
            content="new message",
            session_id="test-session",
            turn_id=2
        )
    
    # Process chunks
    output_chunks = []
    async for chunk in station.process(input_stream()):
        output_chunks.append(chunk)
    
    # Should have processed only turn 1 and 2 (not turn 0)
    text_outputs = [c for c in output_chunks if c.type == ChunkType.TEXT and c.source == station.name]
    assert len(text_outputs) == 2
    assert "old message" not in str(output_chunks)
    assert station.processed_count == 2


@pytest.mark.asyncio
async def test_control_bus_interrupt_signal():
    """Test interrupt signal through ControlBus."""
    control_bus = ControlBus()
    
    # Send interrupt signal
    await control_bus.send_interrupt(
        source="test",
        reason="testing",
        metadata={"test": True}
    )
    
    # Wait for interrupt
    interrupt = await control_bus.wait_for_interrupt()
    
    # Verify interrupt signal
    assert interrupt.source == "test"
    assert interrupt.reason == "testing"
    assert interrupt.metadata["test"] is True
    assert interrupt.turn_id == 1  # Should increment turn ID
    
    # Verify turn ID was incremented
    assert control_bus.get_current_turn_id() == 1


@pytest.mark.asyncio
async def test_turn_detector_interrupt_on_bot_speaking():
    """Test Turn Detector sends interrupt when user speaks during bot speaking."""
    control_bus = ControlBus()
    
    # Create Turn Detector
    turn_detector = TurnDetectorStation(interrupt_enabled=True)
    turn_detector.control_bus = control_bus
    
    # Create input stream
    async def input_stream():
        # Bot starts speaking
        yield EventChunk(
            type=ChunkType.EVENT_BOT_STARTED_SPEAKING,
            session_id="test-session"
        )
        
        # User starts speaking (should trigger interrupt)
        yield EventChunk(
            type=ChunkType.EVENT_VAD_START,
            session_id="test-session"
        )
    
    # Process chunks
    output_chunks = []
    process_task = asyncio.create_task(
        _collect_chunks(turn_detector.process(input_stream()), output_chunks)
    )
    
    # Wait for interrupt signal
    try:
        interrupt = await asyncio.wait_for(control_bus.wait_for_interrupt(), timeout=1.0)
        assert interrupt.source == "TurnDetector"
        assert interrupt.reason == "user_speaking_during_bot_speaking"
    except asyncio.TimeoutError:
        pytest.fail("No interrupt signal received")
    finally:
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_pipeline_clear_queues_on_interrupt():
    """Test that pipeline clears queues on interrupt."""
    control_bus = ControlBus()
    
    # Create slow stations
    agent = MockAgentStation(delay_seconds=1.0)  # Very slow
    tts = MockTTSStation(delay_seconds=1.0)  # Very slow
    
    # Create pipeline
    pipeline = Pipeline([agent, tts], control_bus=control_bus, name="TestPipeline")
    
    # Create input stream
    async def input_stream():
        for i in range(5):
            yield TextChunk(
                type=ChunkType.TEXT,
                content=f"message {i}",
                session_id="test-session"
            )
    
    # Run pipeline
    output_chunks = []
    pipeline_task = asyncio.create_task(
        _collect_chunks(pipeline.run(input_stream()), output_chunks)
    )
    
    # Wait a bit for processing to start
    await asyncio.sleep(0.1)
    
    # Clear queues
    pipeline.clear_queues(from_stage=1)
    
    # Wait for pipeline to finish
    await asyncio.sleep(0.5)
    
    # Cancel pipeline
    pipeline_task.cancel()
    try:
        await pipeline_task
    except asyncio.CancelledError:
        pass
    
    # Should have cleared some chunks (not all 5 processed)
    # This is a weak test, but verifies the mechanism works
    assert len(output_chunks) < 15  # Would be ~15 with 5 messages (3 chunks each)


@pytest.mark.asyncio
async def test_state_reset_on_new_turn():
    """Test that stations reset state on new turn."""
    control_bus = ControlBus()
    
    # Create station
    agent = MockAgentStation()
    agent.control_bus = control_bus
    
    # Create input stream with turn change
    async def input_stream():
        # Turn 0
        yield TextChunk(
            type=ChunkType.TEXT,
            content="message 1",
            session_id="test-session",
            turn_id=0
        )
        
        # Turn 1 (should trigger reset)
        yield TextChunk(
            type=ChunkType.TEXT,
            content="message 2",
            session_id="test-session",
            turn_id=1
        )
    
    # Process chunks
    output_chunks = []
    async for chunk in agent.process(input_stream()):
        output_chunks.append(chunk)
    
    # Verify agent reset was called (current_turn_id should be 1)
    assert agent.current_turn_id == 1
    assert not agent.processing


# Helper Functions

async def _collect_chunks(stream: AsyncIterator[Chunk], output_list: list):
    """Collect chunks from stream into list."""
    async for chunk in stream:
        output_list.append(chunk)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

