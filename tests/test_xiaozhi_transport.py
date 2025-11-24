"""
Unit tests for XiaozhiTransport (FastAPI version)
"""

import pytest
import asyncio
import json
from httpx import AsyncClient, ASGITransport
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
    
    def test_auth_initialization(self):
        """Test authentication initialization"""
        config = {
            "server": {
                "auth": {
                    "enabled": True,
                    "allowed_devices": ["device-1", "device-2"],
                    "expire_seconds": 3600,
                },
                "auth_key": "test_secret_key"
            }
        }
        
        transport = XiaozhiTransport(config=config)
        
        assert transport.auth_enable == True
        assert len(transport.allowed_devices) == 2
        assert "device-1" in transport.allowed_devices
        assert transport.auth_manager.secret_key == "test_secret_key"
        assert transport.auth_manager.expire_seconds == 3600
    
    def test_websocket_url_generation(self):
        """Test WebSocket URL generation"""
        config = {
            "server": {
                "websocket": "ws://custom-host:9999/custom/path/"
            }
        }
        
        transport = XiaozhiTransport(port=8080, config=config)
        url = transport._get_websocket_url()
        
        assert url == "ws://custom-host:9999/custom/path/"
    
    def test_websocket_url_auto_generation(self):
        """Test automatic WebSocket URL generation"""
        transport = XiaozhiTransport(port=8080)
        url = transport._get_websocket_url()
        
        # Should contain port and path
        assert ":8080" in url
        assert "/xiaozhi/v1/" in url
        assert url.startswith("ws://")


class TestXiaozhiTransportHTTP:
    """Test Xiaozhi transport HTTP endpoints"""
    
    @pytest.fixture
    async def transport_with_app(self):
        """Create transport with running app for HTTP testing"""
        config = {
            "server": {
                "auth": {
                    "enabled": True,
                    "allowed_devices": [],
                    "expire_seconds": 3600,
                },
                "auth_key": "test_secret_key",
                "timezone_offset": 8,
            }
        }
        
        transport = XiaozhiTransport(
            host="127.0.0.1",
            port=18081,
            config=config
        )
        
        yield transport
    
    @pytest.mark.asyncio
    async def test_root_endpoint(self, transport_with_app):
        """Test root endpoint"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            response = await client.get("/")
            
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Vixio Xiaozhi Server"
            assert data["version"] == "0.1.0"
            assert data["status"] == "running"
            assert "connections" in data
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self, transport_with_app):
        """Test health check endpoint"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            response = await client.get("/health")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "connections" in data
    
    @pytest.mark.asyncio
    async def test_connections_endpoint(self, transport_with_app):
        """Test connections endpoint"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            response = await client.get("/connections")
            
            assert response.status_code == 200
            data = response.json()
            assert "count" in data
            assert "sessions" in data
            assert data["count"] == 0  # No active connections
    
    @pytest.mark.asyncio
    async def test_ota_get_endpoint(self, transport_with_app):
        """Test OTA GET endpoint"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            response = await client.get("/xiaozhi/ota/")
            
            assert response.status_code == 200
            data = response.json()
            assert "websocket_url" in data
            assert "status" in data
            assert data["status"] == "available"
    
    @pytest.mark.asyncio
    async def test_ota_post_endpoint_websocket_mode(self, transport_with_app):
        """Test OTA POST endpoint (WebSocket mode)"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            request_body = {
                "application": {
                    "version": "1.0.0"
                },
                "device": {
                    "model": "XiaoZhi-ESP32"
                }
            }
            
            response = await client.post(
                "/xiaozhi/ota/",
                json=request_body,
                headers={
                    "device-id": "AA:BB:CC:DD:EE:FF",
                    "client-id": "client-123"
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            
            # Should have server time
            assert "server_time" in data
            assert "timestamp" in data["server_time"]
            assert "timezone_offset" in data["server_time"]
            
            # Should have firmware info
            assert "firmware" in data
            assert data["firmware"]["version"] == "1.0.0"
            
            # Should have websocket config (no MQTT gateway configured)
            assert "websocket" in data
            assert "url" in data["websocket"]
            assert "token" in data["websocket"]
            
            # CORS headers
            assert "Access-Control-Allow-Origin" in response.headers
    
    @pytest.mark.asyncio
    async def test_ota_post_endpoint_mqtt_mode(self):
        """Test OTA POST endpoint (MQTT mode)"""
        config = {
            "server": {
                "mqtt_gateway": "mqtt://localhost:1883",
                "mqtt_signature_key": "mqtt_secret",
                "timezone_offset": 8,
            }
        }
        
        transport = XiaozhiTransport(config=config)
        
        async with AsyncClient(transport=ASGITransport(app=transport.app), base_url="http://test") as client:
            request_body = {
                "application": {
                    "version": "1.0.0"
                },
                "device": {
                    "model": "XiaoZhi-ESP32"
                }
            }
            
            response = await client.post(
                "/xiaozhi/ota/",
                json=request_body,
                headers={
                    "device-id": "AA:BB:CC:DD:EE:FF",
                    "client-id": "client-123"
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            
            # Should have MQTT config
            assert "mqtt" in data
            assert "endpoint" in data["mqtt"]
            assert data["mqtt"]["endpoint"] == "mqtt://localhost:1883"
            assert "client_id" in data["mqtt"]
            assert "username" in data["mqtt"]
            assert "password" in data["mqtt"]
            assert "publish_topic" in data["mqtt"]
            assert "subscribe_topic" in data["mqtt"]
    
    @pytest.mark.asyncio
    async def test_ota_post_endpoint_missing_headers(self, transport_with_app):
        """Test OTA POST endpoint with missing headers"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            request_body = {
                "application": {"version": "1.0.0"}
            }
            
            # Missing device-id header
            response = await client.post(
                "/xiaozhi/ota/",
                json=request_body,
                headers={"client-id": "client-123"}
            )
            
            assert response.status_code == 200  # Still returns 200 with error message
            data = response.json()
            assert "success" in data or "message" in data
    
    @pytest.mark.asyncio
    async def test_ota_options_endpoint(self, transport_with_app):
        """Test OTA OPTIONS endpoint for CORS"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            response = await client.options("/xiaozhi/ota/")
            
            assert response.status_code == 200
            
            # Check CORS headers
            headers = response.headers
            assert "Access-Control-Allow-Origin" in headers
            assert "Access-Control-Allow-Methods" in headers
            assert "Access-Control-Allow-Headers" in headers
            
            # Should allow required methods
            assert "POST" in headers["Access-Control-Allow-Methods"]
            assert "GET" in headers["Access-Control-Allow-Methods"]
    
    @pytest.mark.asyncio
    async def test_vision_endpoint(self, transport_with_app):
        """Test vision analysis endpoint"""
        async with AsyncClient(transport=ASGITransport(app=transport_with_app.app), base_url="http://test") as client:
            response = await client.post("/mcp/vision/explain", json={})
            
            assert response.status_code == 200
            data = response.json()
            assert "status" in data or "message" in data

