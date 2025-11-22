"""
Unit tests for Transport implementations
"""

import pytest
import asyncio
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.core.chunk import (
    Chunk,
    ChunkType,
    AudioChunk,
    ControlChunk,
    EventChunk,
)


class TestXiaozhiTransport:
    """Test XiaozhiTransport"""
    
    def test_create_transport(self):
        """Test transport creation"""
        transport = XiaozhiTransport(host="127.0.0.1", port=9999)
        assert transport._host == "127.0.0.1"
        assert transport._port == 9999
        assert transport._running is False
    
    def test_decode_audio_message(self):
        """Test decoding binary audio message"""
        transport = XiaozhiTransport()
        
        # Simulate PCM audio data
        audio_data = b'\x00\x01' * 1600  # 100ms of 16kHz mono 16-bit audio
        
        chunk = transport._decode_xiaozhi_message(audio_data, "session_123")
        
        assert isinstance(chunk, AudioChunk)
        assert chunk.type == ChunkType.AUDIO_RAW
        assert chunk.data == audio_data
        assert chunk.sample_rate == 16000
        assert chunk.channels == 1
        assert chunk.session_id == "session_123"
    
    def test_decode_interrupt_message(self):
        """Test decoding interrupt control message"""
        transport = XiaozhiTransport()
        
        # JSON interrupt message
        message = '{"type": "interrupt"}'
        
        chunk = transport._decode_xiaozhi_message(message, "session_123")
        
        assert isinstance(chunk, ControlChunk)
        assert chunk.type == ChunkType.CONTROL_INTERRUPT
        assert chunk.command == "interrupt"
        assert chunk.session_id == "session_123"
    
    def test_decode_hello_message(self):
        """Test decoding hello control message"""
        transport = XiaozhiTransport()
        
        # JSON hello message with data
        message = '{"type": "hello", "data": {"version": "1.0", "device": "xiaozhi"}}'
        
        chunk = transport._decode_xiaozhi_message(message, "session_123")
        
        assert isinstance(chunk, ControlChunk)
        assert chunk.type == ChunkType.CONTROL_HELLO
        assert chunk.command == "hello"
        assert chunk.params["version"] == "1.0"
        assert chunk.params["device"] == "xiaozhi"
    
    def test_decode_stop_message(self):
        """Test decoding stop control message"""
        transport = XiaozhiTransport()
        
        message = '{"type": "stop"}'
        
        chunk = transport._decode_xiaozhi_message(message, "session_123")
        
        assert isinstance(chunk, ControlChunk)
        assert chunk.type == ChunkType.CONTROL_STOP
    
    def test_decode_invalid_json(self):
        """Test handling invalid JSON"""
        transport = XiaozhiTransport()
        
        message = '{"type": invalid json'
        
        chunk = transport._decode_xiaozhi_message(message, "session_123")
        
        assert chunk is None
    
    def test_encode_audio_chunk(self):
        """Test encoding audio chunk to binary"""
        transport = XiaozhiTransport()
        
        audio_data = b'\x00\x01' * 100
        chunk = AudioChunk(
            type=ChunkType.AUDIO_ENCODED,
            data=audio_data,
            sample_rate=16000,
            channels=1
        )
        
        encoded = transport._encode_xiaozhi_message(chunk)
        
        assert encoded == audio_data
    
    def test_encode_event_chunk(self):
        """Test encoding event chunk to JSON"""
        import json
        transport = XiaozhiTransport()
        
        chunk = EventChunk(
            type=ChunkType.EVENT_VAD_START,
            event_data={"confidence": 0.95},
            source_station="VADStation"
        )
        
        encoded = transport._encode_xiaozhi_message(chunk)
        
        assert encoded is not None
        decoded = json.loads(encoded.decode('utf-8'))
        assert decoded["type"] == "event.vad.start"
        assert decoded["data"]["confidence"] == 0.95
        assert decoded["source"] == "VADStation"
    
    def test_encode_control_chunk(self):
        """Test encoding control chunk to JSON"""
        import json
        transport = XiaozhiTransport()
        
        chunk = ControlChunk(
            type=ChunkType.CONTROL_START,
            command="start",
            params={"mode": "voice"}
        )
        
        encoded = transport._encode_xiaozhi_message(chunk)
        
        assert encoded is not None
        decoded = json.loads(encoded.decode('utf-8'))
        assert decoded["type"] == "control.start"
        assert decoded["data"]["mode"] == "voice"
    
    def test_buffer_initialization(self):
        """Test buffer structures are initialized"""
        transport = XiaozhiTransport()
        
        connection_id = "test_connection"
        transport._init_buffers(connection_id)
        
        assert connection_id in transport._input_buffers
        assert connection_id in transport._input_buffer_enabled
        assert connection_id in transport._output_buffers
        assert connection_id in transport._output_playing
        
        # Check initial states
        assert transport._input_buffer_enabled[connection_id] is True
        assert transport._output_playing[connection_id] is False
    
    def test_buffer_cleanup(self):
        """Test buffer cleanup"""
        transport = XiaozhiTransport()
        
        connection_id = "test_connection"
        transport._init_buffers(connection_id)
        
        # Verify buffers exist
        assert connection_id in transport._input_buffers
        
        # Cleanup
        transport._cleanup_buffers(connection_id)
        
        # Verify buffers are removed
        assert connection_id not in transport._input_buffers
        assert connection_id not in transport._input_buffer_enabled
        assert connection_id not in transport._output_buffers
        assert connection_id not in transport._output_playing
    
    def test_input_buffering_control(self):
        """Test input buffering enable/disable"""
        transport = XiaozhiTransport()
        
        connection_id = "test_connection"
        transport._init_buffers(connection_id)
        
        # Initially should buffer
        assert transport._should_buffer_input(connection_id) is True
        
        # Enable passthrough (VAD detected)
        transport._enable_input_passthrough(connection_id)
        assert transport._should_buffer_input(connection_id) is False
        
        # Disable passthrough (VAD ended)
        transport._disable_input_passthrough(connection_id)
        assert transport._should_buffer_input(connection_id) is True
    
    @pytest.mark.asyncio
    async def test_server_lifecycle(self):
        """Test server start/stop"""
        transport = XiaozhiTransport(host="127.0.0.1", port=9876)
        
        # Start server
        await transport.start()
        assert transport._running is True
        assert transport._server is not None
        
        # Wait a bit
        await asyncio.sleep(0.1)
        
        # Stop server
        await transport.stop()
        assert transport._running is False
