"""
Unit tests for TurnDetectorStation
"""

import pytest
import asyncio
from vixio.core.chunk import Chunk, ChunkType, EventChunk, ControlChunk
from vixio.stations.turn_detector import TurnDetectorStation


@pytest.fixture
def turn_detector():
    """Create TurnDetectorStation"""
    # Use short threshold for testing (50ms)
    return TurnDetectorStation(silence_threshold_ms=50)


@pytest.mark.asyncio
async def test_turn_detector_emits_turn_end_after_silence(turn_detector):
    """Test turn detector emits EVENT_TURN_END after silence threshold"""
    session_id = "test-session"
    
    # Send VAD_START
    vad_start = EventChunk(
        type=ChunkType.EVENT_VAD_START,
        event_data={"has_voice": True},
        source_station="VAD",
        session_id=session_id
    )
    
    chunks_start = []
    async for chunk in turn_detector.process_chunk(vad_start):
        chunks_start.append(chunk)
    
    # Should just passthrough
    assert len(chunks_start) == 1
    assert chunks_start[0].type == ChunkType.EVENT_VAD_START
    
    # Send VAD_END
    vad_end = EventChunk(
        type=ChunkType.EVENT_VAD_END,
        event_data={"has_voice": False},
        source_station="VAD",
        session_id=session_id
    )
    
    chunks_end = []
    # This should passthrough VAD_END, wait for threshold, then emit TURN_END
    async for chunk in turn_detector.process_chunk(vad_end):
        chunks_end.append(chunk)
    
    # Should output: VAD_END (passthrough) + TURN_END (after delay)
    assert len(chunks_end) == 2
    assert chunks_end[0].type == ChunkType.EVENT_VAD_END
    assert chunks_end[1].type == ChunkType.EVENT_TURN_END
    assert chunks_end[1].session_id == session_id


@pytest.mark.asyncio
async def test_turn_detector_cancels_on_voice_resume(turn_detector):
    """Test turn detector cancels timer if voice resumes"""
    session_id = "test-session"
    
    # Send VAD_END to start timer
    vad_end = EventChunk(
        type=ChunkType.EVENT_VAD_END,
        event_data={"has_voice": False},
        source_station="VAD",
        session_id=session_id
    )
    
    # Start processing VAD_END (this will await)
    process_task = asyncio.create_task(
        _collect_chunks(turn_detector.process_chunk(vad_end))
    )
    
    # Wait a bit, but less than threshold
    await asyncio.sleep(0.01)
    
    # Send VAD_START to cancel timer
    vad_start = EventChunk(
        type=ChunkType.EVENT_VAD_START,
        event_data={"has_voice": True},
        source_station="VAD",
        session_id=session_id
    )
    
    chunks_start = []
    async for chunk in turn_detector.process_chunk(vad_start):
        chunks_start.append(chunk)
    
    # Should just passthrough VAD_START
    assert len(chunks_start) == 1
    assert chunks_start[0].type == ChunkType.EVENT_VAD_START
    
    # Wait for original task to complete
    chunks_end = await process_task
    
    # VAD_END task should complete without emitting TURN_END
    # (cancelled because voice resumed)
    assert len(chunks_end) == 1  # Only VAD_END passthrough
    assert chunks_end[0].type == ChunkType.EVENT_VAD_END


@pytest.mark.asyncio
async def test_turn_detector_reset_on_interrupt(turn_detector):
    """Test turn detector handles CONTROL_INTERRUPT gracefully"""
    session_id = "test-session"
    
    # Send interrupt (before any VAD events)
    interrupt = ControlChunk(
        type=ChunkType.CONTROL_INTERRUPT,
        command="stop",
        session_id=session_id
    )
    
    chunks_interrupt = []
    async for chunk in turn_detector.process_chunk(interrupt):
        chunks_interrupt.append(chunk)
    
    # Should passthrough interrupt
    assert len(chunks_interrupt) == 1
    assert chunks_interrupt[0].type == ChunkType.CONTROL_INTERRUPT
    
    # After interrupt, VAD events should work normally
    vad_end = EventChunk(
        type=ChunkType.EVENT_VAD_END,
        event_data={"has_voice": False},
        source_station="VAD",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in turn_detector.process_chunk(vad_end):
        chunks.append(chunk)
    
    # Should emit VAD_END + TURN_END normally
    assert len(chunks) == 2
    assert chunks[0].type == ChunkType.EVENT_VAD_END
    assert chunks[1].type == ChunkType.EVENT_TURN_END


async def _collect_chunks(chunk_iterator):
    """Helper to collect all chunks from async iterator"""
    chunks = []
    async for chunk in chunk_iterator:
        chunks.append(chunk)
    return chunks

