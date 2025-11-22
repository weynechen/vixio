"""
Unit tests for Chunk classes
"""

import pytest
from core.chunk import (
    Chunk,
    ChunkType,
    AudioChunk,
    TextChunk,
    TextDeltaChunk,
    VideoChunk,
    ControlChunk,
    EventChunk,
    is_audio_chunk,
    is_text_chunk,
    is_video_chunk,
    is_control_chunk,
    is_event_chunk,
)


class TestChunkType:
    """Test ChunkType enum"""
    
    def test_audio_types(self):
        """Test audio chunk types"""
        assert ChunkType.AUDIO_RAW.value == "audio.raw"
        assert ChunkType.AUDIO_ENCODED.value == "audio.encoded"
    
    def test_text_types(self):
        """Test text chunk types"""
        assert ChunkType.TEXT.value == "text"
        assert ChunkType.TEXT_DELTA.value == "text.delta"
    
    def test_control_types(self):
        """Test control chunk types"""
        assert ChunkType.CONTROL_START.value == "control.start"
        assert ChunkType.CONTROL_INTERRUPT.value == "control.interrupt"
    
    def test_event_types(self):
        """Test event chunk types"""
        assert ChunkType.EVENT_VAD_START.value == "event.vad.start"
        assert ChunkType.EVENT_TURN_END.value == "event.turn.end"


class TestBaseChunk:
    """Test base Chunk class"""
    
    def test_create_chunk(self):
        """Test basic chunk creation"""
        chunk = Chunk(type=ChunkType.TEXT, data="Hello")
        assert chunk.type == ChunkType.TEXT
        assert chunk.data == "Hello"
        assert chunk.session_id is None
    
    def test_chunk_with_session_id(self):
        """Test chunk with session ID"""
        chunk = Chunk(
            type=ChunkType.AUDIO_RAW,
            data=b"audio data",
            session_id="session_123"
        )
        assert chunk.session_id == "session_123"
    
    def test_is_data(self):
        """Test is_data() method"""
        audio = Chunk(type=ChunkType.AUDIO_RAW)
        text = Chunk(type=ChunkType.TEXT)
        control = Chunk(type=ChunkType.CONTROL_START)
        
        assert audio.is_data() is True
        assert text.is_data() is True
        assert control.is_data() is False
    
    def test_is_signal(self):
        """Test is_signal() method"""
        control = Chunk(type=ChunkType.CONTROL_START)
        event = Chunk(type=ChunkType.EVENT_VAD_START)
        audio = Chunk(type=ChunkType.AUDIO_RAW)
        
        assert control.is_signal() is True
        assert event.is_signal() is True
        assert audio.is_signal() is False
    
    def test_chunk_str(self):
        """Test string representation"""
        chunk = Chunk(
            type=ChunkType.TEXT,
            data="test",
            session_id="session_12345678"
        )
        str_repr = str(chunk)
        assert "text" in str_repr
        assert "session_" in str_repr  # First 8 chars: "session_"


class TestAudioChunk:
    """Test AudioChunk"""
    
    def test_create_audio_chunk(self):
        """Test audio chunk creation"""
        audio = AudioChunk(data=b"audio data")
        assert audio.type == ChunkType.AUDIO_RAW
        assert audio.sample_rate == 16000
        assert audio.channels == 1
    
    def test_custom_sample_rate(self):
        """Test custom sample rate"""
        audio = AudioChunk(data=b"data", sample_rate=48000, channels=2)
        assert audio.sample_rate == 48000
        assert audio.channels == 2
    
    def test_duration_ms(self):
        """Test audio duration calculation"""
        # 16kHz, mono, 16-bit: 2 bytes per sample
        # 1 second = 16000 samples = 32000 bytes
        # 100ms = 1600 samples = 3200 bytes
        audio = AudioChunk(
            data=b'\x00' * 3200,
            sample_rate=16000,
            channels=1
        )
        duration = audio.duration_ms()
        assert abs(duration - 100.0) < 1.0  # Within 1ms tolerance


class TestTextChunk:
    """Test TextChunk"""
    
    def test_create_text_chunk(self):
        """Test text chunk creation"""
        text = TextChunk(content="Hello, world!")
        assert text.type == ChunkType.TEXT
        assert text.content == "Hello, world!"
    
    def test_text_chunk_str(self):
        """Test text chunk string representation"""
        text = TextChunk(content="Hello", session_id="session_123")
        str_repr = str(text)
        assert "Hello" in str_repr
        assert "TextChunk" in str_repr


class TestTextDeltaChunk:
    """Test TextDeltaChunk"""
    
    def test_create_delta_chunk(self):
        """Test delta chunk creation"""
        delta = TextDeltaChunk(delta="world")
        assert delta.type == ChunkType.TEXT_DELTA
        assert delta.delta == "world"
    
    def test_delta_chunk_str(self):
        """Test delta chunk string representation"""
        delta = TextDeltaChunk(delta="test", session_id="session_123")
        str_repr = str(delta)
        assert "test" in str_repr
        assert "TextDeltaChunk" in str_repr


class TestVideoChunk:
    """Test VideoChunk"""
    
    def test_create_video_chunk(self):
        """Test video chunk creation"""
        video = VideoChunk(
            data=b"image data",
            width=1920,
            height=1080,
            format="JPEG"
        )
        assert video.type == ChunkType.VIDEO_FRAME
        assert video.width == 1920
        assert video.height == 1080
        assert video.format == "JPEG"


class TestControlChunk:
    """Test ControlChunk"""
    
    def test_create_control_chunk(self):
        """Test control chunk creation"""
        control = ControlChunk(
            type=ChunkType.CONTROL_INTERRUPT,
            command="interrupt",
            params={"reason": "user_pressed_button"}
        )
        assert control.type == ChunkType.CONTROL_INTERRUPT
        assert control.command == "interrupt"
        assert control.params["reason"] == "user_pressed_button"
    
    def test_control_chunk_str(self):
        """Test control chunk string representation"""
        control = ControlChunk(
            type=ChunkType.CONTROL_START,
            command="start",
            session_id="session_123"
        )
        str_repr = str(control)
        assert "ControlChunk" in str_repr
        assert "start" in str_repr


class TestEventChunk:
    """Test EventChunk"""
    
    def test_create_event_chunk(self):
        """Test event chunk creation"""
        event = EventChunk(
            type=ChunkType.EVENT_VAD_START,
            event_data={"confidence": 0.95},
            source_station="VADStation"
        )
        assert event.type == ChunkType.EVENT_VAD_START
        assert event.event_data["confidence"] == 0.95
        assert event.source_station == "VADStation"
    
    def test_event_chunk_str(self):
        """Test event chunk string representation"""
        event = EventChunk(
            type=ChunkType.EVENT_TURN_END,
            source_station="TurnDetector",
            session_id="session_123"
        )
        str_repr = str(event)
        assert "EventChunk" in str_repr
        assert "TurnDetector" in str_repr


class TestTypeGuards:
    """Test type guard functions"""
    
    def test_is_audio_chunk(self):
        """Test is_audio_chunk()"""
        audio = AudioChunk(data=b"data")
        text = TextChunk(content="text")
        
        assert is_audio_chunk(audio) is True
        assert is_audio_chunk(text) is False
    
    def test_is_text_chunk(self):
        """Test is_text_chunk()"""
        text = TextChunk(content="text")
        delta = TextDeltaChunk(delta="delta")
        audio = AudioChunk(data=b"data")
        
        assert is_text_chunk(text) is True
        assert is_text_chunk(delta) is True
        assert is_text_chunk(audio) is False
    
    def test_is_video_chunk(self):
        """Test is_video_chunk()"""
        video = VideoChunk(data=b"data")
        text = TextChunk(content="text")
        
        assert is_video_chunk(video) is True
        assert is_video_chunk(text) is False
    
    def test_is_control_chunk(self):
        """Test is_control_chunk()"""
        control = ControlChunk(type=ChunkType.CONTROL_START)
        event = EventChunk(type=ChunkType.EVENT_VAD_START)
        
        assert is_control_chunk(control) is True
        assert is_control_chunk(event) is False
    
    def test_is_event_chunk(self):
        """Test is_event_chunk()"""
        event = EventChunk(type=ChunkType.EVENT_VAD_START)
        control = ControlChunk(type=ChunkType.CONTROL_START)
        
        assert is_event_chunk(event) is True
        assert is_event_chunk(control) is False
