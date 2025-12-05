"""
Unit tests for VADStation
"""

import pytest
from vixio.core.chunk import Chunk, ChunkType, AudioChunk, EventChunk, ControlChunk
from vixio.stations.vad import VADStation
from vixio.providers.vad import VADProvider


class MockVADProvider(VADProvider):
    """Mock VAD provider for testing"""
    
    def __init__(self):
        super().__init__(name="MockVAD")
        self._should_detect_voice = False
        self._reset_called = False
    
    def detect(self, audio_data: bytes) -> bool:
        """Return configured detection result"""
        return self._should_detect_voice
    
    def reset(self) -> None:
        """Track reset calls"""
        self._reset_called = True
    
    def set_voice_detection(self, has_voice: bool):
        """Set whether voice should be detected"""
        self._should_detect_voice = has_voice


@pytest.fixture
def mock_vad():
    """Create mock VAD provider"""
    return MockVADProvider()


@pytest.fixture
def vad_station(mock_vad):
    """Create VADStation with mock provider"""
    return VADStation(mock_vad)


@pytest.mark.asyncio
async def test_vad_detects_voice_start(vad_station, mock_vad):
    """Test VAD emits EVENT_VAD_START when voice begins"""
    session_id = "test-session"
    
    # Configure mock to detect voice
    mock_vad.set_voice_detection(True)
    
    # Create audio chunk
    audio_chunk = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    
    # Process through VAD
    output_chunks = []
    async for chunk in vad_station.process_chunk(audio_chunk):
        output_chunks.append(chunk)
    
    # Should output VAD_START event + passthrough audio
    assert len(output_chunks) == 2
    assert output_chunks[0].type == ChunkType.EVENT_VAD_START
    assert output_chunks[0].session_id == session_id
    assert output_chunks[1].type == ChunkType.AUDIO_RAW


@pytest.mark.asyncio
async def test_vad_detects_voice_end(vad_station, mock_vad):
    """Test VAD emits EVENT_VAD_END when voice stops"""
    session_id = "test-session"
    
    # First, start voice
    mock_vad.set_voice_detection(True)
    audio1 = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    
    chunks1 = []
    async for chunk in vad_station.process_chunk(audio1):
        chunks1.append(chunk)
    
    # Then, stop voice
    mock_vad.set_voice_detection(False)
    audio2 = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x00' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    
    chunks2 = []
    async for chunk in vad_station.process_chunk(audio2):
        chunks2.append(chunk)
    
    # Should output VAD_END event + passthrough audio
    assert len(chunks2) == 2
    assert chunks2[0].type == ChunkType.EVENT_VAD_END
    assert chunks2[0].session_id == session_id
    assert chunks2[1].type == ChunkType.AUDIO_RAW


@pytest.mark.asyncio
async def test_vad_resets_on_interrupt(vad_station, mock_vad):
    """Test VAD resets state on CONTROL_INTERRUPT"""
    session_id = "test-session"
    
    # Start voice activity
    mock_vad.set_voice_detection(True)
    audio = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    
    async for _ in vad_station.process_chunk(audio):
        pass
    
    # Send interrupt
    interrupt = ControlChunk(
        type=ChunkType.CONTROL_INTERRUPT,
        command="stop",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in vad_station.process_chunk(interrupt):
        chunks.append(chunk)
    
    # Should passthrough interrupt and reset VAD
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.CONTROL_INTERRUPT
    assert mock_vad._reset_called


@pytest.mark.asyncio
async def test_vad_passthrough_non_audio(vad_station):
    """Test VAD passes through non-audio chunks"""
    session_id = "test-session"
    
    # Create text chunk (not audio)
    from vixio.core.chunk import TextChunk
    text_chunk = TextChunk(
        type=ChunkType.TEXT,
        content="Hello",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in vad_station.process_chunk(text_chunk):
        chunks.append(chunk)
    
    # Should just passthrough
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.TEXT

