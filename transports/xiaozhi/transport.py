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
from loguru import logger

from core.transport import TransportBase, TransportBufferMixin
from core.chunk import (
    Chunk, ChunkType, AudioChunk, TextChunk, ControlChunk, EventChunk
)
from transports.xiaozhi.protocol import (
    XiaozhiProtocol,
    XiaozhiMessageType,
    XiaozhiControlAction,
)
from utils import get_local_ip, AuthManager, generate_password_signature, get_latency_monitor
from utils.audio import get_opus_codec, OPUS_AVAILABLE


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
        self._connection_events: Dict[str, asyncio.Event] = {}  # Signal when connection should close
        self._connection_handler: Optional[ConnectionHandler] = None
        self._server_task: Optional[asyncio.Task] = None
        self._pipeline_ready_events: Dict[str, asyncio.Event] = {}  # Signal when pipeline is ready
        
        # Audio flow control state per session
        # Format: {session_id: {"last_send_time": float, "packet_count": int, "start_time": float, "sequence": int}}
        self._audio_flow_control: Dict[str, Dict[str, Any]] = {}
        
        # Async send queues per session (for non-blocking audio transmission)
        # This allows TTS to continue generating next sentence while current audio is being sent
        self._send_queues: Dict[str, asyncio.Queue] = {}
        self._send_tasks: Dict[str, asyncio.Task] = {}
        
        # Per-session Opus codecs for thread-safe audio encoding/decoding
        # Each session gets its own codec instance to avoid race conditions
        self._opus_codecs: Dict[str, 'OpusCodec'] = {}
        
        # Override logger from mixin with more specific name
        self.logger = logger.bind(transport="XiaozhiTransport")

        # Check Opus availability (but don't create codec yet)
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
    
    async def on_new_connection(self, handler: ConnectionHandler) -> None:
        """
        Register callback for new connections (TransportBase interface).
        
        Args:
            handler: Async function called with connection_id when client connects
        """
        self.set_connection_handler(handler)
    
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
        # Signal all connections to close
        for session_id in list(self._connection_events.keys()):
            self._connection_events[session_id].set()
        
        # Close all connections
        for session_id in list(self._connections.keys()):
            try:
                websocket = self._connections[session_id]
                await websocket.close()
            except Exception as e:
                self.logger.error(f"Error closing connection {session_id}: {e}")
        
        self._connections.clear()
        self._connection_events.clear()
        # Cleanup all buffers (from TransportBufferMixin)
        for session_id in list(self._input_buffers.keys()):
            self._cleanup_buffers(session_id)
        # Cleanup flow control state
        self._audio_flow_control.clear()
        # Stop all send tasks
        for session_id in list(self._send_tasks.keys()):
            await self._stop_sender_task(session_id)
        
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
        
        # Initialize buffers for this session
        self._init_buffers(session_id)
        
        try:
            while True:
                # Receive message from WebSocket
                data = await websocket.receive()
                
                if "text" in data:
                    self.logger.debug(f"Received text message: {data['text']}")
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
            self.logger.info(f"WebSocket disconnected: {session_id[:8]}")
        except RuntimeError as e:
            # Handle normal disconnection (e.g., "Cannot call receive once a disconnect message has been received")
            if "disconnect" in str(e).lower() or "receive" in str(e).lower():
                self.logger.info(f"WebSocket closed by client: {session_id[:8]}")
            else:
                self.logger.error(f"Runtime error in input stream for {session_id[:8]}: {e}")
                raise
        except Exception as e:
            self.logger.error(f"Error in input stream for {session_id[:8]}: {e}")
            raise
        finally:
            self._cleanup_buffers(session_id)
            # Signal to _handle_websocket that connection should close
            if session_id in self._connection_events:
                self._connection_events[session_id].set()
                self.logger.debug(f"Set close event for session {session_id}")
    
    async def output_chunk(self, session_id: str, chunk: Chunk) -> None:
        """
        Send output chunk to client.
        
        Uses async queue for TTS audio to enable parallel processing:
        - TTS can generate next sentence while current audio is being sent
        - Text events (EVENT_TTS_SENTENCE_START) and audio are sent in order
        - Flow control applied to audio frames (60ms intervals)
        
        Args:
            session_id: Session identifier
            chunk: Chunk to send
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            # Connection closed - silently drop chunks (this is expected during shutdown)
            return
        
        # Check if WebSocket is still open/connected
        # FastAPI WebSocket has client_state attribute
        try:
            if hasattr(websocket, 'client_state') and websocket.client_state.name not in ('CONNECTED', 'CONNECTING'):
                # WebSocket is closing or closed, silently drop
                return
        except Exception:
            # If we can't check state, try to send anyway
            pass
        
        # Special handling for AUDIO_RAW with async queue and flow control
        # Only apply to TTS audio output (source contains "TTS")
        if chunk.type == ChunkType.AUDIO_RAW:
            # Only send TTS audio to client, discard user input audio (prevent echo)
            if chunk.data and "TTS" in chunk.source:
                # Ensure sender task is running
                await self._ensure_sender_task(session_id)
                # Put chunk in queue (non-blocking, allows TTS to continue)
                await self._send_queues[session_id].put(chunk)
            # Discard all other AUDIO_RAW (e.g. user input passthrough from VAD/ASR)
            return
        
        # For TTS events (especially SENTENCE_START), also use queue to maintain order
        if chunk.type in (ChunkType.EVENT_TTS_START, ChunkType.EVENT_TTS_SENTENCE_START, 
                          ChunkType.EVENT_TTS_SENTENCE_END, ChunkType.EVENT_TTS_STOP, ChunkType.TEXT):
            await self._ensure_sender_task(session_id)
            await self._send_queues[session_id].put(chunk)
            return
        
        # Regular message encoding and sending (bypass queue for immediate delivery)
        message = await self._encode_chunk(chunk)
        if not message:
            return
        
        # Send message immediately
        try:
            encoded = self.protocol.encode_message(message)
            
            if isinstance(encoded, bytes):
                await websocket.send_bytes(encoded)
            else:
                await websocket.send_text(encoded)
        
        except Exception as e:
            self.logger.error(f"Error sending to {session_id}: {e}")
    
    async def _on_connection_established(self, session_id: str) -> None:
        """
        Handle connection establishment.
        
        NOTE: HELLO message is NOT sent here anymore!
        It will be sent when pipeline is ready (via on_pipeline_ready).
        This prevents clients from sending audio before the pipeline is ready to process it.
        
        Args:
            session_id: Session identifier
        """
        self.logger.info(f"New connection: {session_id}")
        
        # Create pipeline ready event
        ready_event = asyncio.Event()
        self._pipeline_ready_events[session_id] = ready_event
        
        # Log that we're waiting for pipeline
        self.logger.debug(f"Waiting for pipeline ready for session {session_id[:8]}...")
    
    async def on_pipeline_ready(self, session_id: str) -> None:
        """
        Called by SessionManager when pipeline is ready for this session.
        Now it's safe to send HELLO and start receiving audio.
        
        Args:
            session_id: Session identifier
        """
        self.logger.info(f"Pipeline ready for session {session_id[:8]}, sending HELLO")
        
        # Send HELLO message
        hello_msg = self.protocol.create_hello_message(session_id=session_id)
        self.logger.debug(f"Sending HELLO message: {hello_msg}")
        await self._send_message(session_id, hello_msg)
        
        # Signal that pipeline is ready
        if session_id in self._pipeline_ready_events:
            self._pipeline_ready_events[session_id].set()
    
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
            # Create event to signal when connection should close
            close_event = asyncio.Event()
            self._connection_events[session_id] = close_event
            
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
            
            # Notify new connection (send HELLO message)
            await self._on_connection_established(session_id)
            
            # Call connection handler if set
            if self._connection_handler:
                # Handler will create background tasks (e.g., pipeline)
                # We just wait here until the connection should be closed
                await self._connection_handler(session_id)
                
                # Wait for input_stream to signal completion
                # input_stream will read from WebSocket, we just wait here
                try:
                    await close_event.wait()
                except (WebSocketDisconnect, RuntimeError):
                    pass
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
            self._connection_events.pop(session_id, None)
            self._pipeline_ready_events.pop(session_id, None)
            self._cleanup_buffers(session_id)
            # Cleanup flow control state
            self._audio_flow_control.pop(session_id, None)
            # Cleanup session-specific Opus codec
            self._opus_codecs.pop(session_id, None)
            # Stop sender task
            await self._stop_sender_task(session_id)
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
        
        # Audio message (Opus encoded from client)
        # Transport responsibility: Decode Opus -> PCM
        if msg_type == XiaozhiMessageType.AUDIO:
            opus_data = message.get("audio_data", b"")
            
            # Decode Opus to PCM using session-specific codec
            pcm_data = opus_data
            opus_codec = self._opus_codecs.get(session_id)
            if opus_codec and opus_data:
                try:
                    pcm_data = opus_codec.decode(opus_data)
                except Exception as e:
                    self.logger.error(f"Failed to decode Opus audio for session {session_id[:8]}: {e}")
                    return None
            elif not opus_codec:
                self.logger.warning(f"Opus codec not available for session {session_id[:8]}, cannot decode audio")
                return None
            
            return AudioChunk(
                type=ChunkType.AUDIO_RAW,  # Always PCM in chunks
                data=pcm_data,
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
        
        # Abort message (client interrupt request)
        elif msg_type == "abort":
            self.logger.info(f"Received abort request from client for session {session_id[:8]}")
            return ControlChunk(
                type=ChunkType.CONTROL_INTERRUPT,
                command="abort",
                params={"reason": "client_abort"},
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
        # Audio chunk (PCM from pipeline)
        # Transport responsibility: Encode PCM -> Opus for client
        if chunk.type == ChunkType.AUDIO_RAW:
            pcm_data = chunk.data if chunk.data else b""
            
            # Encode PCM to Opus using session-specific codec
            opus_data = pcm_data
            opus_codec = self._opus_codecs.get(chunk.session_id) if chunk.session_id else None
            if opus_codec and pcm_data:
                try:
                    opus_data = opus_codec.encode(pcm_data)
                except Exception as e:
                    session_str = chunk.session_id[:8] if chunk.session_id else 'unknown'
                    self.logger.error(f"Failed to encode PCM to Opus for session {session_str}: {e}")
                    return None
            elif not opus_codec and pcm_data:
                session_str = chunk.session_id[:8] if chunk.session_id else 'unknown'
                self.logger.warning(f"Opus codec not available for session {session_str}, sending raw PCM")
            
            return {
                "type": XiaozhiMessageType.AUDIO,
                "audio_data": opus_data,
            }
        
        # TTS events
        elif chunk.type == ChunkType.EVENT_TTS_START:
            return self.protocol.create_tts_message(
                session_id=chunk.session_id,
                state="start",
                text=chunk.event_data.get("text") if hasattr(chunk, 'event_data') else None
            )
        
        elif chunk.type == ChunkType.EVENT_TTS_SENTENCE_START:
            # Send sentence_start event with text (for LLM output display)
            text = chunk.event_data.get("text") if hasattr(chunk, 'event_data') else None
            return self.protocol.create_tts_message(
                session_id=chunk.session_id,
                state="sentence_start",
                text=text
            )
        
        elif chunk.type == ChunkType.EVENT_TTS_SENTENCE_END:
            return self.protocol.create_tts_message(
                session_id=chunk.session_id,
                state="sentence_end"
            )
        
        elif chunk.type == ChunkType.EVENT_TTS_STOP:
            return self.protocol.create_tts_message(
                session_id=chunk.session_id,
                state="stop"
            )
        
        # Text chunk - send as STT (ASR result) or skip (Agent output sent via TTS event)
        elif chunk.type == ChunkType.TEXT:
            text_content = chunk.content if hasattr(chunk, 'content') else str(chunk.data)
            
            # If source is "asr" (from TextAggregator, which preserves ASR source), send as STT message
            if "asr" in chunk.source.lower():
                self.logger.debug(f"Sending ASR result as STT: {text_content[:50]}...")
                return self.protocol.create_stt_message(
                    text=text_content,
                    session_id=chunk.session_id
                )
            # If source is "agent", it will be sent via TTS sentence_start event
            # So we don't send it here separately
            # (TTS station will emit EVENT_TTS_SENTENCE_START with the text)
            elif "agent" in chunk.source.lower():
                # Skip sending - will be sent as TTS sentence_start
                # self.logger.debug(f"Skipping TEXT from agent (will be sent via TTS event): {text_content[:50]}...")
                return None
            
            # For other sources, log warning and skip
            # (Client doesn't support generic "text" type)
            else:
                self.logger.warning(f"TEXT chunk from unknown source '{chunk.source}', skipping: {text_content[:50]}...")
                return None
        
        
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
    
    async def _ensure_sender_task(self, session_id: str) -> None:
        """
        Ensure async sender task is running for this session.
        
        The sender task processes chunks from queue serially, ensuring:
        - Text events and audio frames are sent in correct order
        - Audio frames have flow control applied
        - TTS can continue generating while audio is being sent
        
        Args:
            session_id: Session identifier
        """
        if session_id not in self._send_tasks or self._send_tasks[session_id].done():
            # Create queue for this session
            queue = asyncio.Queue()
            self._send_queues[session_id] = queue
            
            # Start sender worker task
            self._send_tasks[session_id] = asyncio.create_task(
                self._sender_worker(session_id, queue)
            )
            self.logger.debug(f"Started async sender task for session {session_id}")
    
    def clear_send_queue(self, session_id: str) -> None:
        """
        Clear send queue for session (used on interrupt to stop pending audio).
        
        Args:
            session_id: Session identifier
        """
        if session_id in self._send_queues:
            queue = self._send_queues[session_id]
            cleared_count = 0
            
            # Clear all pending chunks in the queue
            while not queue.empty():
                try:
                    queue.get_nowait()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break
            
            if cleared_count > 0:
                self.logger.info(f"Cleared {cleared_count} pending chunks from send queue for session {session_id[:8]}")
    
    async def send_immediate(self, session_id: str, chunk: Chunk) -> None:
        """
        Send chunk immediately, bypassing the queue.
        
        Used for urgent control messages like TTS_STOP on interrupt.
        
        Args:
            session_id: Session identifier
            chunk: Chunk to send immediately
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            self.logger.warning(f"Cannot send immediate to {session_id}: connection not found")
            return
        
        # Encode and send immediately
        message = await self._encode_chunk(chunk)
        if not message:
            return
        
        try:
            encoded = self.protocol.encode_message(message)
            
            if isinstance(encoded, bytes):
                await websocket.send_bytes(encoded)
            else:
                await websocket.send_text(encoded)
            
            self.logger.debug(f"Sent immediate message: {chunk.type.value}")
        
        except Exception as e:
            self.logger.error(f"Error sending immediate to {session_id}: {e}")
    
    async def _stop_sender_task(self, session_id: str) -> None:
        """
        Stop sender task for session.
        
        Args:
            session_id: Session identifier
        """
        if session_id in self._send_queues:
            # Send stop signal
            await self._send_queues[session_id].put(None)
            self._send_queues.pop(session_id, None)
        
        if session_id in self._send_tasks:
            task = self._send_tasks.pop(session_id)
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.logger.debug(f"Stopped async sender task for session {session_id}")
    
    async def _sender_worker(self, session_id: str, queue: asyncio.Queue) -> None:
        """
        Background worker that sends chunks from queue.
        
        Processes chunks serially to maintain order:
        1. TTS events (text) - sent immediately
        2. Audio chunks - split into frames with flow control
        
        This design allows:
        - TTS to generate next sentence while current audio is being sent
        - Text and audio stay synchronized (text event sent just before audio)
        
        Args:
            session_id: Session identifier
            queue: Queue of chunks to send
        """
        websocket = self._connections.get(session_id)
        if not websocket:
            # Connection closed before sender started - silently exit
            return
        
        self.logger.debug(f"Sender worker started for session {session_id}")
        
        try:
            while True:
                # Check if connection still exists
                if session_id not in self._connections:
                    self.logger.debug(f"Connection closed, stopping sender for {session_id}")
                    break
                
                # Get next chunk from queue
                chunk = await queue.get()
                
                # Stop signal
                if chunk is None:
                    self.logger.debug(f"Sender worker stopping for session {session_id}")
                    break
                
                # Check WebSocket state before sending
                try:
                    if hasattr(websocket, 'client_state') and websocket.client_state.name not in ('CONNECTED', 'CONNECTING'):
                        # WebSocket is closing or closed, stop sending
                        self.logger.debug(f"WebSocket not connected, stopping sender for {session_id}")
                        break
                except Exception:
                    pass
                
                # Process chunk based on type
                if chunk.type == ChunkType.AUDIO_RAW:
                    # Audio chunk: apply flow control
                    success = await self._send_audio_with_flow_control(session_id, websocket, chunk)
                    if not success:
                        # Connection closed during send, stop worker
                        break
                
                else:
                    # Event chunk: send immediately (no delay)
                    message = await self._encode_chunk(chunk)
                    if message:
                        try:
                            encoded = self.protocol.encode_message(message)
                            
                            if isinstance(encoded, bytes):
                                await websocket.send_bytes(encoded)
                            else:
                                await websocket.send_text(encoded)
                        
                        except Exception as e:
                            # Connection closed, stop sending
                            self.logger.debug(f"Error sending event to {session_id}, connection likely closed: {e}")
                            break
        
        except Exception as e:
            self.logger.error(f"Sender worker error for {session_id}: {e}", exc_info=True)
        
        finally:
            self.logger.debug(f"Sender worker stopped for session {session_id}")
    
    async def _send_audio_with_flow_control(
        self,
        session_id: str,
        websocket: WebSocket,
        chunk: Chunk
    ) -> bool:
        """
        Send audio chunk with flow control.
        
        Implements the flow control logic from xiaozhi-server:
        - First 5 packets: fast (pre-buffering)
        - Subsequent packets: timed at 60ms intervals
        
        Args:
            session_id: Session identifier
            websocket: WebSocket connection
            chunk: Audio chunk to send
            
        Returns:
            True if sent successfully, False if connection closed
        """
        pcm_data = chunk.data
        if not pcm_data:
            return True  # Nothing to send, but not an error
        
        # Check if connection still exists
        if session_id not in self._connections:
            return False  # Connection closed
        
        # Initialize flow control for this session if needed
        if session_id not in self._audio_flow_control:
            self._audio_flow_control[session_id] = {
                "last_send_time": 0.0,
                "packet_count": 0,
                "start_time": time.time(),
                "sequence": 0,
            }
        
        flow_control = self._audio_flow_control[session_id]
        
        # Split PCM into 60ms frames (16kHz, mono, 16-bit = 1920 bytes per 60ms)
        frame_size = 1920  # 60ms at 16kHz mono
        frame_duration_s = 0.06  # 60ms in seconds
        pre_buffer_count = 5  # First 5 packets sent quickly
        
        # Split audio into frames
        frames = []
        for i in range(0, len(pcm_data), frame_size):
            frames.append(pcm_data[i:i + frame_size])
        
        self.logger.debug(f"Sending {len(frames)} audio frames with flow control , chunk source: {chunk.source}")
        
        # Send frames with flow control
        opus_codec = self._opus_codecs.get(session_id)
        for frame_idx, frame in enumerate(frames):
            # Encode PCM to Opus using session-specific codec
            opus_data = frame
            if opus_codec and frame:
                try:
                    opus_data = opus_codec.encode(frame)
                except Exception as e:
                    self.logger.error(f"Failed to encode PCM to Opus for session {session_id[:8]}: {e}")
                    continue
            elif not opus_codec:
                self.logger.warning(f"Opus codec not available for session {session_id[:8]}, sending raw PCM")
            
            # Apply flow control timing
            current_time = time.time()
            
            if flow_control["packet_count"] < pre_buffer_count:
                # Pre-buffer: send immediately
                pass
            else:
                # Calculate expected time based on frame duration
                effective_packet = flow_control["packet_count"] - pre_buffer_count
                expected_time = flow_control["start_time"] + (effective_packet * frame_duration_s)
                delay = expected_time - current_time
                
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # Correct timing drift
                    flow_control["start_time"] += abs(delay)
            
            # Send the audio frame
            try:
                message = {
                    "type": XiaozhiMessageType.AUDIO,
                    "audio_data": opus_data,
                }
                encoded = self.protocol.encode_message(message)
                await websocket.send_bytes(encoded)
                
                # Record T6: first_audio_sent (only for very first frame per turn)
                session_turn_key = f"{chunk.session_id}_{chunk.turn_id}"
                if frame_idx == 0 and session_turn_key not in self._first_audio_sent_recorded:
                    self._latency_monitor.record(
                        chunk.session_id,
                        chunk.turn_id,
                        "first_audio_sent"
                    )
                    self._first_audio_sent_recorded[session_turn_key] = True
                    self.logger.debug("Recorded first audio sent to client")
                    
                    # Generate and log latency report for this turn
                    self._latency_monitor.log_report(
                        chunk.session_id,
                        chunk.turn_id
                    )
                    self._latency_monitor.print_summary(
                        chunk.session_id,
                        chunk.turn_id
                    )
                
                # Update flow control state
                flow_control["packet_count"] += 1
                flow_control["sequence"] += 1
                flow_control["last_send_time"] = time.time()
            
            except Exception as e:
                # Connection closed during send (common during disconnect)
                self.logger.debug(f"Audio send failed for {session_id[:8]}, connection likely closed")
                return False
        
        return True  # All frames sent successfully
