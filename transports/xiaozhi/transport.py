"""
Xiaozhi Transport implementation

Provides WebSocket and HTTP endpoints using FastAPI
"""

import asyncio
import uuid
import json
import time
import base64
from typing import Dict, Optional, Callable, Coroutine, Any, Union, TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Header, Query
from fastapi.responses import JSONResponse
import uvicorn
from loguru import logger

from core.transport import TransportBase
from core.protocol import ProtocolBase
from transports.xiaozhi.protocol import XiaozhiProtocol
from transports.xiaozhi.flow_control import AudioFlowController
from utils import get_local_ip, AuthManager, generate_password_signature, get_latency_monitor
from utils.audio import get_opus_codec, OPUS_AVAILABLE

if TYPE_CHECKING:
    from utils.audio import OpusCodec


class XiaozhiTransport(TransportBase):
    """
    Xiaozhi WebSocket Transport.
    
    Inherits TransportBase, implements protocol-specific:
    - _do_read(): Read from WebSocket
    - _do_write(): Write to WebSocket
    - _create_protocol(): Create XiaozhiProtocol
    - _create_audio_codec(): Create Opus codec
    - _create_output_controller(): Create AudioFlowController
    
    Features:
    - WebSocket endpoint for voice chat
    - HTTP endpoints for health check and OTA
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        websocket_path: str = "/xiaozhi/v1/",
        app: Optional[FastAPI] = None,
        config: Optional[Dict] = None,
    ):
        """
        Initialize Xiaozhi Transport.
        
        Args:
            host: Host to bind to
            port: Port to listen on
            websocket_path: WebSocket endpoint path
            app: Optional FastAPI app instance
            config: Optional configuration dict
        """
        super().__init__(name="XiaozhiTransport")
        
        self.host = host
        self.port = port
        self.websocket_path = websocket_path
        self.config = config or {}
        
        # Create or use provided FastAPI app
        self.app = app or FastAPI(
            title="Vixio Xiaozhi Server",
            version="0.1.0",
            description="Voice-powered AI agent server for Xiaozhi devices"
        )
        
        # ============ Connection management ============
        self._connections: Dict[str, WebSocket] = {}
        self._server_task: Optional[asyncio.Task] = None
        
        # ============ Audio codec (per session) ============
        self._opus_codecs: Dict[str, 'OpusCodec'] = {}
        
        # ============ Protocol instance (shared) ============
        self._protocol: Optional[XiaozhiProtocol] = None
        
        # Check Opus availability
        if not OPUS_AVAILABLE:
            self.logger.warning("Opus codec not available, audio conversion will be disabled")
        
        # Initialize auth manager
        self._init_auth()
        
        # Set Latency Monitor
        self.set_latency_monitor(get_latency_monitor())
        
        # Setup routes
        self._setup_routes()
    
    def _init_auth(self) -> None:
        """Initialize authentication manager"""
        server_config = self.config.get("server", {})
        auth_config = server_config.get("auth", {})
        
        self.auth_enable = auth_config.get("enabled", False)
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        
        secret_key = server_config.get("auth_key", "default_secret_key")
        expire_seconds = auth_config.get("expire_seconds", 60 * 60 * 24 * 30)
        
        self.auth_manager = AuthManager(
            secret_key=secret_key,
            expire_seconds=expire_seconds
        )
    
    # ============ TransportBase abstract method implementations ============
    
    async def start(self) -> None:
        """Start FastAPI server"""
        self._running = True
        
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        
        self._server_task = asyncio.create_task(server.serve())
        self.logger.info(
            f"Xiaozhi server started on http://{self.host}:{self.port}"
        )
        self.logger.info(
            f"WebSocket endpoint: ws://{self.host}:{self.port}{self.websocket_path}"
        )
    
    async def stop(self) -> None:
        """Stop FastAPI server"""
        self._running = False
        
        # Close all connections
        for session_id in list(self._connections.keys()):
            try:
                await self._cleanup_session(session_id)
            except Exception as e:
                self.logger.error(f"Error cleaning up session {session_id}: {e}")
        
        # Stop server
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("Xiaozhi server stopped")
    
    async def _do_read(self, session_id: str) -> Optional[Union[bytes, str]]:
        """
        Read one message from WebSocket connection.
        
        Called by framework's _read_worker in a loop.
        Returns None when connection is closed.
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            return None
        
        try:
            data = await websocket.receive()
            
            if "text" in data:
                return data["text"]
            elif "bytes" in data:
                return data["bytes"]
            elif data.get("type") == "websocket.disconnect":
                return None
            else:
                return None
        except WebSocketDisconnect:
            return None
        except Exception as e:
            self.logger.debug(f"WebSocket read error for {session_id[:8]}: {e}")
            return None
    
    async def _do_write(self, session_id: str, data: Union[bytes, str]) -> None:
        """Write data to WebSocket"""
        websocket = self._connections.get(session_id)
        if not websocket:
            raise ConnectionError(f"Session {session_id} not found")
        
        try:
            if isinstance(data, bytes):
                await websocket.send_bytes(data)
            else:
                await websocket.send_text(data)
        except Exception as e:
            self.logger.error(f"Failed to write to {session_id[:8]}: {e}")
            raise
    
    def _create_protocol(self) -> ProtocolBase:
        """Create XiaozhiProtocol"""
        if self._protocol is None:
            self._protocol = XiaozhiProtocol(
                sample_rate=16000, 
                channels=1, 
                frame_duration=60
            )
        return self._protocol
    
    def _create_audio_codec(self, session_id: str) -> Optional[Any]:
        """Create Opus codec (per session)"""
        if session_id not in self._opus_codecs:
            if OPUS_AVAILABLE:
                self._opus_codecs[session_id] = get_opus_codec(
                    sample_rate=16000,
                    channels=1,
                    frame_duration_ms=60
                )
        return self._opus_codecs.get(session_id)
    
    def _create_output_controller(self, session_id: str) -> AudioFlowController:
        """Create audio flow controller"""
        return AudioFlowController(
            pre_buffer_count=5,
            frame_duration_ms=60
        )
    
    async def _on_session_start(self, session_id: str) -> None:
        """Session start hook - create Opus codec"""
        # Create isolated Opus codec for this session
        if OPUS_AVAILABLE:
            self._create_audio_codec(session_id)
    
    async def _on_session_end(self, session_id: str) -> None:
        """Cleanup resources when session ends"""
        # Cleanup Opus codec
        self._opus_codecs.pop(session_id, None)
    
    def _is_audio_message(self, data: Union[bytes, str]) -> bool:
        """Check if message is audio"""
        # Xiaozhi protocol: bytes is audio
        if isinstance(data, bytes):
            return True
        
        # Check JSON message
        try:
            message = json.loads(data)
            return message.get("type") == "tts" and "audio" in message
        except Exception:
            return False
    
    def _is_tts_stop_message(self, data: Union[bytes, str]) -> bool:
        """Check if message is TTS_STOP"""
        if isinstance(data, bytes):
            return False
        
        try:
            message = json.loads(data)
            return message.get("type") == "tts" and message.get("state") == "stop"
        except Exception:
            return False
    
    # ============ WebSocket handling ============
    
    async def _handle_websocket(self, websocket: WebSocket, token: Optional[str] = None) -> None:
        """
        Handle WebSocket connection lifecycle.
        
        Args:
            websocket: WebSocket connection
            token: Optional authentication token
        """
        session_id = str(uuid.uuid4())
        
        try:
            # Accept connection
            await websocket.accept()
            
            # Authentication check
            if self.auth_enable and token:
                client_id = f"{websocket.client.host}:{websocket.client.port}"
                device_id = session_id
                
                if not self.auth_manager.verify_token(token, client_id, device_id):
                    self.logger.warning(f"Authentication failed for session {session_id}")
                    await websocket.close(code=1008, reason="Authentication failed")
                    return
                
                self.logger.info(f"Authentication successful for session {session_id}")
            
            # Register connection (must be done before calling connection handler)
            self._connections[session_id] = websocket
            
            self.logger.info(f"WebSocket accepted: {session_id}")
            
            try:
                # Call connection handler (creates Pipeline and starts workers)
                # This will start _read_worker which calls _do_read
                if self._connection_handler:
                    await self._connection_handler(session_id)
                
                # Wait for read_worker to finish (connection closed or error)
                # read_worker is started by start_workers() called from SessionManager
                if session_id in self._read_workers:
                    await self._read_workers[session_id]
            
            finally:
                # Cleanup
                await self._cleanup_session(session_id)
        
        except WebSocketDisconnect:
            self.logger.info(f"WebSocket disconnected: {session_id}")
        except Exception as e:
            self.logger.error(f"WebSocket error for {session_id}: {e}", exc_info=True)
        finally:
            # Call disconnect handler
            if self._disconnect_handler:
                try:
                    await self._disconnect_handler(session_id)
                except Exception as e:
                    self.logger.error(f"Error in disconnect handler for {session_id}: {e}")
    
    async def _cleanup_session(self, session_id: str) -> None:
        """Cleanup session resources"""
        # Close WebSocket
        websocket = self._connections.pop(session_id, None)
        if websocket:
            try:
                await websocket.close()
            except Exception:
                pass
        
        # Stop workers (base class handles most cleanup)
        await self.stop_workers(session_id)
        
        self.logger.info(f"Session cleaned up: {session_id[:8]}")
    
    # ============ Helper methods ============
    
    def _get_websocket_url(self) -> str:
        """Get WebSocket URL"""
        server_config = self.config.get("server", {})
        websocket_config = server_config.get("websocket", "")
        
        if websocket_config:
            return websocket_config
        else:
            local_ip = get_local_ip()
            return f"ws://{local_ip}:{self.port}{self.websocket_path}"
    
    # ============ FastAPI route setup ============
    
    def _setup_routes(self) -> None:
        """Setup FastAPI routes"""
        
        @self.app.get("/")
        async def root():
            """Root endpoint - server info"""
            return JSONResponse({
                "name": "Vixio Xiaozhi Server",
                "version": "0.1.0",
                "status": "running",
                "connections": len(self._connections),
                "websocket_path": self.websocket_path,
            })
        
        @self.app.get("/health")
        async def health():
            """Health check endpoint"""
            return JSONResponse({
                "status": "healthy",
                "connections": len(self._connections),
            })
        
        @self.app.get("/connections")
        async def connections():
            """Get active connections"""
            return JSONResponse({
                "count": len(self._connections),
                "sessions": list(self._connections.keys()),
            })
        
        # OTA endpoint - GET
        @self.app.get("/xiaozhi/ota/")
        async def ota_get():
            """Handle OTA GET request"""
            try:
                websocket_url = self._get_websocket_url()
                message = f"OTA interface is running, websocket URL sent to device: {websocket_url}"
                return JSONResponse({
                    "message": message,
                    "websocket_url": websocket_url,
                    "status": "available",
                })
            except Exception as e:
                self.logger.error(f"OTA GET request error: {e}")
                return JSONResponse({
                    "message": "OTA interface error",
                    "status": "error",
                })
        
        # OTA endpoint - POST
        @self.app.post("/xiaozhi/ota/")
        async def ota_post(
            request: Request,
            device_id: Optional[str] = Header(None, alias="device-id"),
            client_id: Optional[str] = Header(None, alias="client-id"),
        ):
            """Handle OTA POST request"""
            try:
                body = await request.body()
                body_text = body.decode("utf-8")
                
                self.logger.debug(f"OTA request method: {request.method}")
                self.logger.debug(f"OTA request headers: {request.headers}")
                self.logger.debug(f"OTA request data: {body_text}")
                
                if not device_id:
                    raise ValueError("OTA request device-id header is empty")
                
                self.logger.info(f"OTA request device ID: {device_id}")
                
                if not client_id:
                    raise ValueError("OTA request client-id header is empty")
                
                self.logger.info(f"OTA request client ID: {client_id}")
                
                data_json = json.loads(body_text)
                
                server_config = self.config.get("server", {})
                
                return_json = {
                    "server_time": {
                        "timestamp": int(round(time.time() * 1000)),
                        "timezone_offset": server_config.get("timezone_offset", 8) * 60,
                    },
                    "firmware": {
                        "version": data_json.get("application", {}).get("version", "1.0.0"),
                        "url": "",
                    },
                }
                
                mqtt_gateway_endpoint = server_config.get("mqtt_gateway")
                
                if mqtt_gateway_endpoint:
                    # MQTT gateway configuration
                    device_model = "default"
                    try:
                        if "device" in data_json and isinstance(data_json["device"], dict):
                            device_model = data_json["device"].get("model", "default")
                        elif "model" in data_json:
                            device_model = data_json["model"]
                        group_id = f"GID_{device_model}".replace(":", "_").replace(" ", "_")
                    except Exception as e:
                        self.logger.error(f"Failed to get device model: {e}")
                        group_id = "GID_default"
                    
                    mac_address_safe = device_id.replace(":", "_")
                    mqtt_client_id = f"{group_id}@@@{mac_address_safe}@@@{mac_address_safe}"
                    
                    user_data = {"ip": "unknown"}
                    try:
                        user_data_json = json.dumps(user_data)
                        username = base64.b64encode(user_data_json.encode("utf-8")).decode("utf-8")
                    except Exception as e:
                        self.logger.error(f"Failed to generate username: {e}")
                        username = ""
                    
                    password = ""
                    signature_key = server_config.get("mqtt_signature_key", "")
                    if signature_key:
                        password = generate_password_signature(
                            mqtt_client_id + "|" + username,
                            signature_key
                        )
                        if not password:
                            password = ""
                    else:
                        self.logger.warning("Missing MQTT signature key, password left empty")
                    
                    return_json["mqtt"] = {
                        "endpoint": mqtt_gateway_endpoint,
                        "client_id": mqtt_client_id,
                        "username": username,
                        "password": password,
                        "publish_topic": "device-server",
                        "subscribe_topic": f"devices/p2p/{mac_address_safe}",
                    }
                    self.logger.info(f"Configured MQTT gateway for device {device_id}")
                
                else:
                    # WebSocket configuration
                    token = ""
                    if self.auth_enable:
                        if self.allowed_devices:
                            if device_id not in self.allowed_devices:
                                token = self.auth_manager.generate_token(client_id, device_id)
                        else:
                            token = self.auth_manager.generate_token(client_id, device_id)
                    
                    return_json["websocket"] = {
                        "url": self._get_websocket_url(),
                        "token": token,
                    }
                    self.logger.info(f"No MQTT gateway configured, sent WebSocket config for device {device_id}")
                
                return JSONResponse(
                    content=return_json,
                    headers={
                        "Access-Control-Allow-Headers": "client-id, content-type, device-id",
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
                
            except Exception as e:
                self.logger.error(f"OTA POST request error: {e}", exc_info=True)
                return JSONResponse(
                    content={"success": False, "message": "request error."},
                    headers={
                        "Access-Control-Allow-Headers": "client-id, content-type, device-id",
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
        
        # OTA endpoint - OPTIONS (CORS)
        @self.app.options("/xiaozhi/ota/")
        async def ota_options():
            """Handle OTA OPTIONS request (CORS)"""
            return JSONResponse(
                content={},
                headers={
                    "Access-Control-Allow-Headers": "client-id, content-type, device-id",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                }
            )
        
        # Vision analysis endpoint
        @self.app.post("/mcp/vision/explain")
        async def vision_explain(request: Request):
            """Vision analysis endpoint"""
            try:
                return JSONResponse({
                    "status": "success",
                    "message": "Vision analysis not yet implemented",
                })
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        # WebSocket endpoint
        @self.app.websocket(self.websocket_path)
        async def websocket_endpoint(
            websocket: WebSocket,
            token: Optional[str] = Query(None),
        ):
            """WebSocket endpoint for voice chat"""
            await self._handle_websocket(websocket, token)
    
    # ============ Legacy interface compatibility ============
    
    def set_connection_handler(self, handler) -> None:
        """Set connection handler (legacy interface)"""
        self._connection_handler = handler
    
    def set_disconnect_handler(self, handler) -> None:
        """Set disconnect handler (legacy interface)"""
        self._disconnect_handler = handler
