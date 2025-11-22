"""
Unit tests for XiaozhiTransport (FastAPI version)
"""

import pytest
import asyncio
from transports.xiaozhi import XiaozhiTransport, XiaozhiProtocol, XiaozhiMessageType
from core.chunk import ChunkType, AudioChunk, TextChunk, ControlChunk


class TestXiaozhiProtocol:
    """Test Xiaozhi protocol implementation"""
    
    @pytest.fixture
    def protocol(self):
        """Create protocol instance"""
        return XiaozhiProtocol()
    
    def test_create_protocol(self, protocol):
        """Test creating protocol"""
        assert protocol.version == 1
        assert protocol.audio_format == "opus"
        assert protocol.sample_rate == 16000
    
    def test_is_audio_message(self, protocol):
        """Test audio message detection"""
        assert protocol.is_audio_message(b'\x00\x01\x02') == True
        assert protocol.is_audio_message("hello") == False
    
    def test_parse_audio_message(self, protocol):
        """Test parsing audio message"""
        audio_data = b'\x00\x01\x02\x03'
        msg = protocol.parse_message(audio_data)
        
        assert msg["type"] == XiaozhiMessageType.AUDIO
        assert msg["audio_data"] == audio_data
        assert msg["format"] == "opus"
    
    def test_parse_json_message(self, protocol):
        """Test parsing JSON message"""
        import json
        text_msg = json.dumps({"type": "control", "action": "interrupt"})
        msg = protocol.parse_message(text_msg)
        
        assert msg["type"] == "control"
        assert msg["action"] == "interrupt"
    
    def test_encode_audio_message(self, protocol):
        """Test encoding audio message"""
        message = {
            "type": XiaozhiMessageType.AUDIO,
            "audio_data": b'\x00\x01\x02',
        }
        
        encoded = protocol.encode_message(message)
        assert encoded == b'\x00\x01\x02'
    
    def test_encode_json_message(self, protocol):
        """Test encoding JSON message"""
        import json
        message = {
            "type": "tts",
            "session_id": "test-123",
            "state": "start"
        }
        
        encoded = protocol.encode_message(message)
        assert isinstance(encoded, str)
        
        decoded = json.loads(encoded)
        assert decoded["type"] == "tts"
        assert decoded["state"] == "start"
    
    def test_create_hello_message(self, protocol):
        """Test creating HELLO message"""
        msg = protocol.create_hello_message(session_id="test-123")
        
        assert msg["type"] == XiaozhiMessageType.HELLO
        assert msg["version"] == 1
        assert msg["session_id"] == "test-123"
        assert "audio_params" in msg
    
    def test_create_tts_message(self, protocol):
        """Test creating TTS message"""
        msg = protocol.create_tts_message(
            session_id="test-123",
            state="start",
            text="Hello"
        )
        
        assert msg["type"] == XiaozhiMessageType.TTS
        assert msg["session_id"] == "test-123"
        assert msg["state"] == "start"
        assert msg["text"] == "Hello"
    
    def test_create_state_message(self, protocol):
        """Test creating STATE message"""
        msg = protocol.create_state_message("listening", "test-123")
        
        assert msg["type"] == XiaozhiMessageType.STATE
        assert msg["state"] == "listening"
        assert msg["session_id"] == "test-123"
    
    def test_create_error_message(self, protocol):
        """Test creating ERROR message"""
        msg = protocol.create_error_message("Something went wrong", "test-123")
        
        assert msg["type"] == XiaozhiMessageType.ERROR
        assert msg["error"] == "Something went wrong"
        assert msg["session_id"] == "test-123"


class TestXiaozhiTransport:
    """Test Xiaozhi transport implementation"""
    
    @pytest.fixture
    def transport(self):
        """Create transport instance"""
        return XiaozhiTransport(host="127.0.0.1", port=18080)
    
    def test_create_transport(self, transport):
        """Test creating transport"""
        assert transport.host == "127.0.0.1"
        assert transport.port == 18080
        assert transport.websocket_path == "/xiaozhi/v1/"
        assert transport.app is not None
    
    def test_protocol_instance(self, transport):
        """Test protocol instance"""
        assert isinstance(transport.protocol, XiaozhiProtocol)
        assert transport.protocol.audio_format == "opus"
    
    @pytest.mark.asyncio
    async def test_server_lifecycle(self, transport):
        """Test server start/stop"""
        # Start server
        start_task = asyncio.create_task(transport.start())
        await asyncio.sleep(0.5)  # Give server time to start
        
        # Check server is running
        assert transport._server_task is not None
        
        # Stop server
        await transport.stop()
        await asyncio.sleep(0.5)  # Give server time to stop
        
        # Cleanup start task
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

