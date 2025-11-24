"""
Xiaozhi transport implementation using FastAPI

Provides WebSocket and HTTP endpoints for Xiaozhi devices
"""

import asyncio
import uuid
import json
import time
import base64
from typing import AsyncIterator, Dict, Optional, Callable, Coroutine, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Header, Query
from fastapi.responses import JSONResponse
import uvicorn
import logging

from core.transport import TransportBase, TransportBufferMixin
from core.chunk import (
    Chunk, ChunkType, AudioChunk, TextChunk, ControlChunk, EventChunk
)
from transports.xiaozhi.protocol import (
    XiaozhiProtocol,
    XiaozhiMessageType,
    XiaozhiControlAction,
)
from utils import get_local_ip, AuthManager, generate_password_signature


# Type alias for connection handler
ConnectionHandler = Callable[[str], Coroutine[Any, Any, None]]


class XiaozhiTransport(TransportBase, TransportBufferMixin):
    """
    Xiaozhi transport implementation using FastAPI.
    
    Features:
    - WebSocket endpoint for voice chat
    - HTTP endpoints for health check and status
    - Protocol-based message encoding/decoding
    - Input/output buffering support
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
        # Initialize TransportBufferMixin
        TransportBufferMixin.__init__(self)
        
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
        
        # Protocol handler
        self.protocol = XiaozhiProtocol()
        
        # Connection management
        self._connections: Dict[str, WebSocket] = {}
        self._connection_handler: Optional[ConnectionHandler] = None
        self._server_task: Optional[asyncio.Task] = None
        
        # Override logger from mixin with more specific name
        self.logger = logging.getLogger("XiaozhiTransport")
        
        self.logger.setLevel(logging.DEBUG)

        # Initialize auth manager
        self._init_auth()
        
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
                websocket = self._connections[session_id]
                await websocket.close()
            except Exception as e:
                self.logger.error(f"Error closing connection {session_id}: {e}")
        
        self._connections.clear()
        # Cleanup all buffers (from TransportBufferMixin)
        for session_id in list(self._input_buffers.keys()):
            self._cleanup_buffers(session_id)
        
        # Stop server
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("Xiaozhi server stopped")
    
    async def input_stream(self, session_id: str) -> AsyncIterator[Chunk]:
        """
        Get input stream for a session.
        
        Args:
            session_id: Session identifier
            
        Yields:
            Input chunks from client
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            raise ConnectionError(f"Session {session_id} not found")
        
        # Initialize buffer for this session
        self._init_buffer(session_id)
        
        try:
            while True:
                # Receive message from WebSocket
                data = await websocket.receive()
                
                if "text" in data:
                    # JSON message
                    message = self.protocol.parse_message(data["text"])
                    chunk = await self._decode_message(message, session_id)
                    if chunk:
                        yield chunk
                
                elif "bytes" in data:
                    # Binary audio message
                    message = self.protocol.parse_message(data["bytes"])
                    chunk = await self._decode_message(message, session_id)
                    if chunk:
                        yield chunk
        
        except WebSocketDisconnect:
            self.logger.info(f"WebSocket disconnected: {session_id}")
        except Exception as e:
            self.logger.error(f"Error in input stream for {session_id}: {e}")
            raise
        finally:
            self._cleanup_buffer(session_id)
    
    async def output_chunk(self, session_id: str, chunk: Chunk) -> None:
        """
        Send output chunk to client.
        
        Args:
            session_id: Session identifier
            chunk: Chunk to send
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            self.logger.warning(f"Cannot send to {session_id}: connection not found")
            return
        
        # Encode chunk to message
        message = await self._encode_chunk(chunk)
        if not message:
            return
        
        # Send message
        try:
            encoded = self.protocol.encode_message(message)
            
            if isinstance(encoded, bytes):
                await websocket.send_bytes(encoded)
            else:
                await websocket.send_text(encoded)
        
        except Exception as e:
            self.logger.error(f"Error sending to {session_id}: {e}")
    
    async def on_new_connection(self, session_id: str) -> None:
        """
        Handle new connection.
        
        Args:
            session_id: Session identifier
        """
        self.logger.info(f"New connection: {session_id}")
        
        # Send HELLO message
        hello_msg = self.protocol.create_hello_message(session_id=session_id)
        self.logger.debug(f"Sending HELLO message: {hello_msg}")
        await self._send_message(session_id, hello_msg)
    
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
            self.logger.info(f"WebSocket accepted: {session_id}")
            
            # Notify new connection
            await self.on_new_connection(session_id)
            
            # Call connection handler if set
            if self._connection_handler:
                await self._connection_handler(session_id)
            else:
                # Default: just keep connection alive
                self.logger.info(f"No connection handler set, keeping connection alive for session {session_id}")
                try:
                    while True:
                        message = await websocket.receive()
                        # Check if disconnect message
                        if message.get("type") == "websocket.disconnect":
                            break
                except (WebSocketDisconnect, RuntimeError):
                    pass
        
        except WebSocketDisconnect:
            self.logger.info(f"WebSocket disconnected: {session_id}")
        except Exception as e:
            self.logger.error(f"WebSocket error for {session_id}: {e}", exc_info=True)
        finally:
            # Cleanup
            self._connections.pop(session_id, None)
            self._cleanup_buffers(session_id)
            self.logger.info(f"WebSocket closed: {session_id}")
    
    async def _decode_message(
        self,
        message: Dict[str, Any],
        session_id: str
    ) -> Optional[Chunk]:
        """
        Decode Xiaozhi message to Chunk.
        
        Args:
            message: Parsed message dictionary
            session_id: Session identifier
            
        Returns:
            Chunk or None
        """
        msg_type = message.get("type")
        
        # Audio message
        if msg_type == XiaozhiMessageType.AUDIO:
            return AudioChunk(
                type=ChunkType.AUDIO_RAW,
                data=message.get("audio_data", b""),
                sample_rate=self.protocol.sample_rate,
                channels=self.protocol.channels,
                session_id=session_id
            )
        
        # Control message
        elif msg_type == XiaozhiMessageType.CONTROL:
            action = message.get("action", "")
            
            if action == XiaozhiControlAction.INTERRUPT:
                return ControlChunk(
                    type=ChunkType.CONTROL_INTERRUPT,
                    command="interrupt",
                    params={},
                    session_id=session_id
                )
            elif action == XiaozhiControlAction.STOP:
                return ControlChunk(
                    type=ChunkType.CONTROL_STOP,
                    command="stop",
                    params={},
                    session_id=session_id
                )
        
        # HELLO message
        elif msg_type == XiaozhiMessageType.HELLO:
            return ControlChunk(
                type=ChunkType.CONTROL_HELLO,
                command="hello",
                params=message,
                session_id=session_id
            )
        
        # Text message (not commonly used in voice chat)
        elif msg_type == XiaozhiMessageType.TEXT:
            content = message.get("content", message.get("text", ""))
            if content:
                return TextChunk(
                    type=ChunkType.TEXT,
                    content=content,
                    session_id=session_id
                )
        
        return None
    
    async def _encode_chunk(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        """
        Encode Chunk to Xiaozhi message.
        
        Args:
            chunk: Chunk to encode
            
        Returns:
            Message dictionary or None
        """
        # Audio chunk
        if chunk.type == ChunkType.AUDIO_ENCODED or chunk.type == ChunkType.AUDIO_RAW:
            return {
                "type": XiaozhiMessageType.AUDIO,
                "audio_data": chunk.data if chunk.data else b"",
            }
        
        # TTS events
        elif chunk.type == ChunkType.EVENT_TTS_START:
            return self.protocol.create_tts_message(
                session_id=chunk.session_id,
                state="start",
                text=chunk.event_data.get("text") if hasattr(chunk, 'event_data') else None
            )
        
        elif chunk.type == ChunkType.EVENT_TTS_STOP:
            return self.protocol.create_tts_message(
                session_id=chunk.session_id,
                state="stop"
            )
        
        # State events
        elif chunk.type == ChunkType.EVENT_STATE_IDLE:
            return self.protocol.create_state_message("idle", chunk.session_id)
        
        elif chunk.type == ChunkType.EVENT_STATE_LISTENING:
            return self.protocol.create_state_message("listening", chunk.session_id)
        
        elif chunk.type == ChunkType.EVENT_STATE_PROCESSING:
            return self.protocol.create_state_message("processing", chunk.session_id)
        
        elif chunk.type == ChunkType.EVENT_STATE_SPEAKING:
            return self.protocol.create_state_message("speaking", chunk.session_id)
        
        # Error events
        elif chunk.type == ChunkType.EVENT_ERROR:
            error_msg = chunk.event_data.get("error") if hasattr(chunk, 'event_data') else "Unknown error"
            return self.protocol.create_error_message(error_msg, chunk.session_id)
        
        # Text chunk (for debugging/logging)
        elif chunk.type == ChunkType.TEXT:
            return {
                "type": XiaozhiMessageType.TEXT,
                "content": chunk.content if hasattr(chunk, 'content') else str(chunk.data),
                "session_id": chunk.session_id,
            }
        
        return None
    
    async def _send_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """
        Send message to client.
        
        Args:
            session_id: Session identifier
            message: Message dictionary
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            return
        
        try:
            encoded = self.protocol.encode_message(message)
            
            if isinstance(encoded, bytes):
                await websocket.send_bytes(encoded)
            else:
                await websocket.send_text(encoded)
        
        except Exception as e:
            self.logger.error(f"Error sending message to {session_id}: {e}")
