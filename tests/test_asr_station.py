"""
Unit tests for ASRStation
"""

import pytest
from core.chunk import Chunk, ChunkType, AudioChunk, EventChunk, ControlChunk, TextChunk
from stations.asr import ASRStation
from providers.asr import ASRProvider
from typing import List


class MockASRProvider(ASRProvider):
    """Mock ASR provider for testing"""
    
    def __init__(self):
        super().__init__(name="MockASR")
        self._transcription_result = "mock transcription"
        self._transcribe_called = False
        self._reset_called = False
    
    async def transcribe(self, audio_chunks: List[bytes]) -> str:
        """Return configured transcription result"""
        self._transcribe_called = True
        self._last_audio_chunks_count = len(audio_chunks)
        return self._transcription_result
    
    def reset(self) -> None:
        """Track reset calls"""
        self._reset_called = True
    
    def set_transcription(self, text: str):
        """Set the transcription result"""
        self._transcription_result = text


@pytest.fixture
def mock_asr():
    """Create mock ASR provider"""
    return MockASRProvider()


@pytest.fixture
def asr_station(mock_asr):
    """Create ASRStation with mock provider"""
    return ASRStation(mock_asr)


@pytest.mark.asyncio
async def test_asr_buffers_audio(asr_station, mock_asr):
    """Test ASRStation buffers audio chunks"""
    session_id = "test-session"
    
    # Send multiple audio chunks
    for i in range(3):
        audio_chunk = AudioChunk(
            type=ChunkType.AUDIO_RAW,
            data=bytes([i]) * 100,
            sample_rate=16000,
            channels=1,
            session_id=session_id
        )
        
        chunks = []
        async for chunk in asr_station.process_chunk(audio_chunk):
            chunks.append(chunk)
        
        # Should passthrough audio
        assert len(chunks) == 1
        assert chunks[0].type == ChunkType.AUDIO_RAW
    
    # ASR should not have been called yet
    assert not mock_asr._transcribe_called


@pytest.mark.asyncio
async def test_asr_transcribes_on_turn_end(asr_station, mock_asr):
    """Test ASRStation transcribes when turn ends"""
    session_id = "test-session"
    
    # Buffer some audio
    for i in range(3):
        audio_chunk = AudioChunk(
            type=ChunkType.AUDIO_RAW,
            data=bytes([i]) * 100,
            sample_rate=16000,
            channels=1,
            session_id=session_id
        )
        async for _ in asr_station.process_chunk(audio_chunk):
            pass
    
    # Set transcription result
    mock_asr.set_transcription("Hello World")
    
    # Send TURN_END event
    turn_end = EventChunk(
        type=ChunkType.EVENT_TURN_END,
        event_data={"silence_duration": 0.8},
        source_station="TurnDetector",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in asr_station.process_chunk(turn_end):
        chunks.append(chunk)
    
    # Should yield: TURN_END (passthrough) + TEXT (transcription)
    assert len(chunks) == 2
    assert chunks[0].type == ChunkType.EVENT_TURN_END
    assert chunks[1].type == ChunkType.TEXT
    assert chunks[1].content == "Hello World"
    
    # ASR should have been called
    assert mock_asr._transcribe_called
    assert mock_asr._last_audio_chunks_count == 3


@pytest.mark.asyncio
async def test_asr_clears_buffer_after_transcription(asr_station, mock_asr):
    """Test ASRStation clears buffer after transcription"""
    session_id = "test-session"
    
    # Buffer audio
    audio_chunk = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    async for _ in asr_station.process_chunk(audio_chunk):
        pass
    
    # Trigger transcription
    turn_end = EventChunk(
        type=ChunkType.EVENT_TURN_END,
        event_data={},
        source_station="TurnDetector",
        session_id=session_id
    )
    async for _ in asr_station.process_chunk(turn_end):
        pass
    
    # Reset mock state
    mock_asr._transcribe_called = False
    
    # Send another TURN_END (no audio buffered)
    turn_end2 = EventChunk(
        type=ChunkType.EVENT_TURN_END,
        event_data={},
        source_station="TurnDetector",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in asr_station.process_chunk(turn_end2):
        chunks.append(chunk)
    
    # Should only passthrough TURN_END (no TEXT since buffer is empty)
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.EVENT_TURN_END
    assert not mock_asr._transcribe_called


@pytest.mark.asyncio
async def test_asr_handles_empty_transcription(asr_station, mock_asr):
    """Test ASRStation handles empty transcription result"""
    session_id = "test-session"
    
    # Buffer audio
    audio_chunk = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    async for _ in asr_station.process_chunk(audio_chunk):
        pass
    
    # Set empty transcription
    mock_asr.set_transcription("")
    
    # Trigger transcription
    turn_end = EventChunk(
        type=ChunkType.EVENT_TURN_END,
        event_data={},
        source_station="TurnDetector",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in asr_station.process_chunk(turn_end):
        chunks.append(chunk)
    
    # Should only passthrough TURN_END (no TEXT for empty result)
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.EVENT_TURN_END


@pytest.mark.asyncio
async def test_asr_resets_on_interrupt(asr_station, mock_asr):
    """Test ASRStation clears buffer on CONTROL_INTERRUPT"""
    session_id = "test-session"
    
    # Buffer some audio
    audio_chunk = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    async for _ in asr_station.process_chunk(audio_chunk):
        pass
    
    # Send interrupt
    interrupt = ControlChunk(
        type=ChunkType.CONTROL_INTERRUPT,
        command="stop",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in asr_station.process_chunk(interrupt):
        chunks.append(chunk)
    
    # Should passthrough interrupt
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.CONTROL_INTERRUPT
    assert mock_asr._reset_called
    
    # Buffer should be cleared - send TURN_END
    turn_end = EventChunk(
        type=ChunkType.EVENT_TURN_END,
        event_data={},
        source_station="TurnDetector",
        session_id=session_id
    )
    
    chunks2 = []
    async for chunk in asr_station.process_chunk(turn_end):
        chunks2.append(chunk)
    
    # Should only passthrough (no buffered audio)
    assert len(chunks2) == 1
    assert chunks2[0].type == ChunkType.EVENT_TURN_END


@pytest.mark.asyncio
async def test_asr_passthrough_non_audio_data(asr_station):
    """Test ASRStation passes through non-audio data chunks"""
    session_id = "test-session"
    
    # Send text chunk
    text_chunk = TextChunk(
        type=ChunkType.TEXT,
        content="Hello",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in asr_station.process_chunk(text_chunk):
        chunks.append(chunk)
    
    # Should just passthrough
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.TEXT

