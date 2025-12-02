"""
Xiaozhi transport implementation using FastAPI

Provides WebSocket and HTTP endpoints for Xiaozhi devices
"""

import asyncio
import uuid
import json
import time
import base64
from typing import AsyncIterator, Dict, Optional, Callable, Coroutine, Any, Union, TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Header, Query
from fastapi.responses import JSONResponse
import uvicorn
from loguru import logger

from core.transport import TransportBase
from core.station import Station
from core.chunk import (
    Chunk, ChunkType, AudioChunk, TextChunk, ControlChunk, EventChunk
)
from transports.xiaozhi.protocol import (
    XiaozhiProtocol,
    XiaozhiMessageType,
    XiaozhiControlAction,
)
from transports.xiaozhi.flow_control import AudioFlowController
from stations.transport_stations import InputStation, OutputStation
from utils import get_local_ip, AuthManager, generate_password_signature, get_latency_monitor
from utils.audio import get_opus_codec, OPUS_AVAILABLE

if TYPE_CHECKING:
    from core.control_bus import ControlBus
    from utils.audio import OpusCodec


# Type alias for connection handler
ConnectionHandler = Callable[[str], Coroutine[Any, Any, None]]


class XiaozhiTransport(TransportBase):
    """
    Xiaozhi WebSocket Transport.
    
    Composes Protocol + Codec + FlowController,
    provides InputStation and OutputStation.
    
    Features:
    - WebSocket endpoint for voice chat
    - HTTP endpoints for health check and status
    - Protocol-based message encoding/decoding
    - Provides InputStation and OutputStation for Pipeline
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
        Initialize Xiaozhi transport.
        
        Args:
            host: Host to bind to
            port: Port to listen on
            websocket_path: WebSocket endpoint path
            app: Optional FastAPI app instance
            config: Optional configuration dict
        """
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
        
        # ============ Create internal components ============
        
        # Protocol
        self.protocol = XiaozhiProtocol(sample_rate=16000, channels=1, frame_duration=60)
        
        # Codec (one instance per session, lazy create)
        self._opus_codecs: Dict[str, 'OpusCodec'] = {}
        
        # FlowController (one instance per session, lazy create)
        self._flow_controllers: Dict[str, AudioFlowController] = {}
        
        # ============ Connection management ============
        self._connections: Dict[str, WebSocket] = {}
        self._read_queues: Dict[str, asyncio.Queue] = {}  # Receive queues
        self._connection_handler: Optional[ConnectionHandler] = None
        self._disconnect_handler: Optional[ConnectionHandler] = None
        self._server_task: Optional[asyncio.Task] = None
        
        # ============ ControlBus references ============
        self._control_buses: Dict[str, 'ControlBus'] = {}
        
        # Logger
        self.logger = logger.bind(transport="XiaozhiTransport")

        # Check Opus availability
        if not OPUS_AVAILABLE:
            self.logger.warning("Opus codec not available, audio conversion will be disabled")

        # Initialize auth manager
        self._init_auth()
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
        self._first_audio_sent_recorded = {}
        
        # Setup routes
        self._setup_routes()
    
    def _init_auth(self) -> None:
        """Initialize authentication manager."""
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
    
    def set_control_bus(self, connection_id: str, control_bus: 'ControlBus') -> None:
        """
        Set ControlBus for a connection (injected by SessionManager).
        
        Args:
            connection_id: Connection identifier
            control_bus: ControlBus instance for this connection
        """
        self._control_buses[connection_id] = control_bus
        self.logger.info(f"ControlBus set for connection {connection_id[:8]}")
    
    def get_control_bus(self, connection_id: str) -> Optional['ControlBus']:
        """
        Get ControlBus for a connection.
        
        Args:
            connection_id: Connection identifier
            
        Returns:
            ControlBus instance or None
        """
        return self._control_buses.get(connection_id)
    
    # ============ TransportBase interface implementation ============
    
    def get_input_station(self, session_id: str) -> Station:
        """
        Get InputStation (TransportBase interface).
        
        Creates an InputStation bound to this session's read function.
        """
        opus_codec = self._get_or_create_opus_codec(session_id)
        
        input_station = InputStation(
            session_id=session_id,
            read_func=lambda: self._read_raw(session_id),
            protocol=self.protocol,
            audio_codec=opus_codec,
            name=f"InputStation-{session_id[:8]}"
        )
        
        return input_station
    
    def get_output_station(self, session_id: str) -> Station:
        """
        Get OutputStation (TransportBase interface).
        
        Creates an OutputStation bound to this session's write function.
        """
        opus_codec = self._get_or_create_opus_codec(session_id)
        flow_controller = self._get_or_create_flow_controller(session_id)
        
        output_station = OutputStation(
            session_id=session_id,
            write_func=lambda data: self._write_raw(session_id, data),
            protocol=self.protocol,
            audio_codec=opus_codec,
            flow_controller=flow_controller,
            latency_monitor=self._latency_monitor,
            name=f"OutputStation-{session_id[:8]}"
        )
        
        return output_station
    
    async def on_pipeline_ready(self, session_id: str) -> None:
        """Pipeline ready - send handshake message (TransportBase interface)."""
        handshake_msg = self.protocol.handshake(session_id)
        if handshake_msg:
            encoded = self.protocol.encode_message(handshake_msg)
            await self._write_raw(session_id, encoded)
            self.logger.info(f"Handshake sent for {session_id[:8]}")
    
    def on_new_connection(self, handler: Callable[[str], Coroutine[Any, Any, None]]) -> None:
        """Register new connection callback (TransportBase interface)."""
        self._connection_handler = handler
    
    # ============ Internal helper methods ============
    
    def _get_or_create_opus_codec(self, session_id: str):
        """Get or create Opus codec for session"""
        if session_id not in self._opus_codecs:
            if OPUS_AVAILABLE:
                self._opus_codecs[session_id] = get_opus_codec(
                    sample_rate=16000,
                    channels=1,
                    frame_duration_ms=60
                )
        return self._opus_codecs.get(session_id)
    
    def _get_or_create_flow_controller(self, session_id: str) -> AudioFlowController:
        """Get or create flow controller for session"""
        if session_id not in self._flow_controllers:
            self._flow_controllers[session_id] = AudioFlowController(
                pre_buffer_count=5,
                frame_duration_ms=60
            )
        return self._flow_controllers[session_id]
    
    async def _read_raw(self, session_id: str) -> Union[bytes, str]:
        """Read raw data from WebSocket"""
        if session_id not in self._read_queues:
            raise ConnectionError(f"Session {session_id} not found")
        
        queue = self._read_queues[session_id]
        data = await queue.get()
        
        if data is None:
            raise ConnectionError(f"Session {session_id} closed")
        
        return data
    
    async def _write_raw(self, session_id: str, data: Union[bytes, str]) -> None:
        """Write raw data to WebSocket"""
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
    
    def _get_websocket_url(self) -> str:
        """Get websocket URL."""
        server_config = self.config.get("server", {})
        websocket_config = server_config.get("websocket", "")
        
        # If websocket URL is configured and valid, use it
        if websocket_config:
            return websocket_config
        else:
            # Otherwise, construct URL from local IP and port
            local_ip = get_local_ip()
            return f"ws://{local_ip}:{self.port}{self.websocket_path}"
    
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
            """Handle OTA GET request."""
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
            """Handle OTA POST request."""
            try:
                # Get request body
                body = await request.body()
                body_text = body.decode("utf-8")
                
                self.logger.debug(f"OTA request method: {request.method}")
                self.logger.debug(f"OTA request headers: {request.headers}")
                self.logger.debug(f"OTA request data: {body_text}")
                
                # Validate device_id
                if not device_id:
                    raise ValueError("OTA request device-id header is empty")
                
                self.logger.info(f"OTA request device ID: {device_id}")
                
                # Validate client_id
                if not client_id:
                    raise ValueError("OTA request client-id header is empty")
                
                self.logger.info(f"OTA request client ID: {client_id}")
                
                # Parse request data
                data_json = json.loads(body_text)
                
                server_config = self.config.get("server", {})
                local_ip = get_local_ip()
                
                # Build response
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
                
                if mqtt_gateway_endpoint:  # If MQTT gateway is configured
                    # Get device model from request data
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
                    
                    # Build user data
                    user_data = {"ip": "unknown"}
                    try:
                        user_data_json = json.dumps(user_data)
                        username = base64.b64encode(user_data_json.encode("utf-8")).decode("utf-8")
                    except Exception as e:
                        self.logger.error(f"Failed to generate username: {e}")
                        username = ""
                    
                    # Generate password
                    password = ""
                    signature_key = server_config.get("mqtt_signature_key", "")
                    if signature_key:
                        password = generate_password_signature(
                            mqtt_client_id + "|" + username,
                            signature_key
                        )
                        if not password:
                            password = ""  # Signature failed, leave empty
                    else:
                        self.logger.warning("Missing MQTT signature key, password left empty")
                    
                    # Build MQTT config
                    return_json["mqtt"] = {
                        "endpoint": mqtt_gateway_endpoint,
                        "client_id": mqtt_client_id,
                        "username": username,
                        "password": password,
                        "publish_topic": "device-server",
                        "subscribe_topic": f"devices/p2p/{mac_address_safe}",
                    }
                    self.logger.info(f"Configured MQTT gateway for device {device_id}")
                
                else:  # No MQTT gateway, send WebSocket config
                    # If authentication is enabled, generate token
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
                    self.logger.info(f"Response: {return_json}")
                
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
        
        # OTA endpoint - OPTIONS (for CORS)
        @self.app.options("/xiaozhi/ota/")
        async def ota_options():
            """Handle OTA OPTIONS request for CORS."""
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
            """Vision analysis endpoint."""
            try:
                # TODO: Implement vision analysis
                return JSONResponse({
                    "status": "success",
                    "message": "Vision analysis not yet implemented",
                })
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.websocket(self.websocket_path)
        async def websocket_endpoint(
            websocket: WebSocket,
            token: Optional[str] = Query(None),
        ):
            """WebSocket endpoint for voice chat"""
            await self._handle_websocket(websocket, token)
    
    def set_connection_handler(self, handler: ConnectionHandler) -> None:
        """
        Set connection handler callback.
        
        Args:
            handler: Async function to handle new connections
        """
        self._connection_handler = handler
    
    def set_disconnect_handler(self, handler: ConnectionHandler) -> None:
        """
        Set disconnect handler callback.
        
        Args:
            handler: Async function called when connection closes
        """
        self._disconnect_handler = handler
    
    
    async def start(self) -> None:
        """Start the FastAPI server"""
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
        """Stop the FastAPI server"""
        # Close all connections
        for session_id in list(self._connections.keys()):
            try:
                await self._cleanup_session(session_id)
                websocket = self._connections.get(session_id)
                if websocket:
                    await websocket.close()
            except Exception as e:
                self.logger.error(f"Error closing connection {session_id}: {e}")
        
        self._connections.clear()
        self._read_queues.clear()
        
        # Stop server
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("Xiaozhi server stopped")
    
    
    async def _handle_websocket(self, websocket: WebSocket, token: Optional[str] = None) -> None:
        """
        Handle WebSocket connection lifecycle.
        
        Args:
            websocket: WebSocket connection
            token: Optional authentication token
        """
        # Generate session ID
        session_id = str(uuid.uuid4())
        
        try:
            # Accept connection
            await websocket.accept()
            
            # Authentication check
            if self.auth_enable and token:
                # Extract client info from websocket
                client_id = f"{websocket.client.host}:{websocket.client.port}"
                device_id = session_id  # Use session_id as device_id for now
                
                # Verify token
                if not self.auth_manager.verify_token(token, client_id, device_id):
                    self.logger.warning(f"Authentication failed for session {session_id}")
                    await websocket.close(code=1008, reason="Authentication failed")
                    return
                
                self.logger.info(f"Authentication successful for session {session_id}")
            
            self._connections[session_id] = websocket
            self._read_queues[session_id] = asyncio.Queue()
            
            # Create isolated Opus codec for this session
            if OPUS_AVAILABLE:
                try:
                    from utils.audio import OpusCodec
                    self._opus_codecs[session_id] = OpusCodec(
                        sample_rate=16000,
                        channels=1,
                        frame_duration_ms=60
                    )
                    self.logger.debug(f"Created isolated Opus codec for session {session_id[:8]}")
                except Exception as e:
                    self.logger.warning(f"Failed to create Opus codec for session {session_id[:8]}: {e}")
            
            self.logger.info(f"WebSocket accepted: {session_id}")
            
            # Start receive loop (feeds data to InputStation)
            receive_task = asyncio.create_task(
                self._receive_loop(session_id, websocket)
            )
            
            try:
                # Call connection handler if set (creates pipeline task)
                if self._connection_handler:
                    await self._connection_handler(session_id)
                
                # Wait for receive loop to finish (connection closed by client or error)
                # This keeps the WebSocket alive while pipeline processes data
                await receive_task
            
            finally:
                # Cleanup
                receive_task.cancel()
                await self._cleanup_session(session_id)
        
        except WebSocketDisconnect:
            self.logger.info(f"WebSocket disconnected: {session_id}")
        except Exception as e:
            self.logger.error(f"WebSocket error for {session_id}: {e}", exc_info=True)
        finally:
            # Call disconnect handler to cancel pipeline
            if self._disconnect_handler:
                try:
                    await self._disconnect_handler(session_id)
                except Exception as e:
                    self.logger.error(f"Error in disconnect handler for {session_id}: {e}")
    
    def _get_current_turn_id(self, session_id: str) -> int:
        """
        Get current turn_id for a session from ControlBus.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Current turn_id or 0 if ControlBus not available
        """
        control_bus = self._control_buses.get(session_id)
        if control_bus:
            return control_bus.get_current_turn_id()
        return 0
    
    # ============ WebSocket server methods ============
    
    async def _receive_loop(self, session_id: str, websocket: WebSocket) -> None:
        """
        Receive loop: read from WebSocket and put into queue.
        
        This runs in the background, feeding data to InputStation via read queue.
        """
        queue = self._read_queues[session_id]
        
        try:
            while True:
                data = await websocket.receive()
                
                if "text" in data:
                    await queue.put(data["text"])
                elif "bytes" in data:
                    await queue.put(data["bytes"])
                elif data.get("type") == "websocket.disconnect":
                    break
        
        except (WebSocketDisconnect, Exception) as e:
            self.logger.debug(f"WebSocket closed for {session_id[:8]}: {e}")
        
        finally:
            await queue.put(None)  # Signal end of stream
    
    async def _cleanup_session(self, session_id: str) -> None:
        """
        Cleanup session resources.
        
        Args:
            session_id: Session identifier
        """
        self._connections.pop(session_id, None)
        self._read_queues.pop(session_id, None)
        self._opus_codecs.pop(session_id, None)
        self._flow_controllers.pop(session_id, None)
        self._control_buses.pop(session_id, None)
        self.logger.info(f"Session cleaned up: {session_id[:8]}")
