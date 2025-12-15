"""
Xiaozhi Transport implementation

Provides WebSocket and HTTP endpoints using FastAPI
"""

import asyncio
import uuid
import json
from typing import Dict, Optional, Callable, Any, Union, List, TYPE_CHECKING, Awaitable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
import uvicorn
from loguru import logger

from vixio.core.transport import TransportBase
from vixio.core.protocol import ProtocolBase
from vixio.transports.xiaozhi.protocol import XiaozhiProtocol
from vixio.transports.xiaozhi.flow_control import AudioFlowController
from vixio.transports.xiaozhi.ota_router import create_ota_router
from vixio.transports.xiaozhi.vision_router import create_vision_router
from vixio.transports.xiaozhi.auth import XiaozhiAuth, generate_mqtt_password
from vixio.utils import get_local_ip, get_latency_monitor
from vixio.utils.audio import get_opus_codec, OPUS_AVAILABLE

if TYPE_CHECKING:
    from vixio.utils.audio import OpusCodec
    from vixio.transports.xiaozhi.device_tools import XiaozhiDeviceToolClient
    from vixio.core.tools.types import ToolDefinition


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
        
        # ============ Device tool clients (for MCP/function tools) ============
        self._device_tool_clients: Dict[str, 'XiaozhiDeviceToolClient'] = {}
        self._device_tools_callback: Optional[Callable[[str, List], Awaitable[None]]] = None
        
        # ============ Session-Device mapping (for database association) ============
        # Maps session_id -> device_id after device sends hello message
        self._session_device_map: Dict[str, str] = {}
        
        # ============ Protocol instance (shared, created eagerly) ============
        # Protocol is stateless and shared across all sessions, no need for lazy loading
        self._protocol: XiaozhiProtocol = XiaozhiProtocol(
            sample_rate=16000,
            channels=1,
            frame_duration=60
        )
        
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
        ota_expire_seconds = auth_config.get("expire_seconds", 60 * 60 * 24 * 30)
        vision_expire_seconds = auth_config.get("vision_expire_seconds", 60 * 60 * 24)
        
        # Use unified XiaozhiAuth for both OTA and Vision authentication
        self.auth = XiaozhiAuth(
            secret_key=secret_key,
            ota_expire_seconds=ota_expire_seconds,
            vision_expire_seconds=vision_expire_seconds
        )
        
        # Backward compatibility alias
        self.auth_manager = self.auth
    
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
        """Return the protocol instance (already created in __init__)"""
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
        
        # Cleanup device tool client
        client = self._device_tool_clients.pop(session_id, None)
        if client:
            client.cancel_all()
            # Clear logger reference to help GC
            if hasattr(client, "logger"):
                client.logger = None
        
        # Cleanup session-device mapping
        self._session_device_map.pop(session_id, None)
    
    async def on_pipeline_ready(self, session_id: str) -> None:
        """
        Send hello handshake message when pipeline is ready.
        """
        protocol = self.get_protocol()
        handshake_msg = protocol.handshake(session_id)
        
        if handshake_msg:
            encoded = protocol.encode_message(handshake_msg)
            await self._do_write(session_id, encoded)
            self.logger.info(f"Handshake sent for session {session_id[:8]}")
    
    def _get_vision_config(self, device_id: str) -> dict:
        """
        Get vision configuration for MCP initialize capabilities.
        
        Returns dict with 'url' and 'token' keys matching xiaozhi-server format.
        Used in MCP initialize message: capabilities.vision = {...}
        
        Args:
            device_id: Device ID for token generation
            
        Returns:
            Dict with 'url' and 'token' keys
        """
        server_config = self.config.get("server", {})
        vision_explain = server_config.get("vision_explain", "")
        
        # Auto-generate vision_explain URL if not configured
        if not vision_explain or "你的" in vision_explain:
            local_ip = get_local_ip()
            vision_explain = f"http://{local_ip}:{self.port}/mcp/vision/explain"
        
        # Generate vision token using XiaozhiAuth
        token = self.auth.generate_vision_token(device_id)
        
        return {
            "url": vision_explain,
            "token": token,
        }
    
    def register_device(self, session_id: str, device_id: str) -> None:
        """
        Register device_id for a session.
        
        Called after processing device's hello message.
        Enables session_id -> device_id lookup for database association.
        
        Args:
            session_id: Session ID
            device_id: Device ID (MAC address)
        """
        self._session_device_map[session_id] = device_id
        self.logger.info(f"Registered device {device_id} for session {session_id[:8]}")
    
    def get_device_id(self, session_id: str) -> Optional[str]:
        """
        Get device_id for a session.
        
        Args:
            session_id: Session ID
            
        Returns:
            Device ID or None if not registered
        """
        return self._session_device_map.get(session_id)
    
    # ============ Device tools (MCP) support ============
    
    def set_device_tools_callback(
        self,
        callback: Callable[[str, List['ToolDefinition']], Awaitable[None]]
    ) -> None:
        """
        Set callback for device tools notification.
        
        Called when device tools become available after MCP initialization.
        
        Args:
            callback: Async function(session_id, tool_definitions) -> None
        """
        self._device_tools_callback = callback
    
    async def _init_device_tools(self, session_id: str) -> None:
        """
        Initialize device tool client and fetch tools.
        
        Called after hello handshake when device supports MCP.
        """
        from vixio.transports.xiaozhi.device_tools import XiaozhiDeviceToolClient
        
        # Create send callback
        async def send_mcp_message(message: Dict[str, Any]) -> None:
            """Send MCP message to device."""
            encoded = self._protocol.encode_message(message)
            await self._do_write(session_id, encoded)
        
        # Get vision config for MCP initialize message
        # At this point we may have device_id from hello message
        device_id = self._session_device_map.get(session_id, session_id)
        vision_config = self._get_vision_config(device_id)
        
        # Create client with vision config
        client = XiaozhiDeviceToolClient(
            send_callback=send_mcp_message,
            session_id=session_id,
            timeout=30.0,
            vision_config=vision_config,
        )
        self._device_tool_clients[session_id] = client
        
        # Initialize and fetch tools
        if await client.initialize():
            # Notify callback with tool definitions
            if self._device_tools_callback and client.has_tools:
                tool_defs = client.get_tool_definitions()
                self.logger.info(f"Session {session_id[:8]} has {len(tool_defs)} device tools")
                await self._device_tools_callback(session_id, tool_defs)
        else:
            self.logger.warning(f"Failed to initialize MCP for session {session_id[:8]}")
    
    def _route_mcp_message(self, session_id: str, message: Dict[str, Any]) -> bool:
        """
        Route MCP message to device tool client.
        
        Args:
            session_id: Session ID
            message: Parsed message (should have type="mcp")
            
        Returns:
            True if message was handled, False otherwise
        """
        if not self._protocol.is_mcp_message(message):
            return False
        
        client = self._device_tool_clients.get(session_id)
        if client:
            payload = self._protocol.get_mcp_payload(message)
            if payload:
                client.on_tools_message(payload)
                return True
        else:
            self.logger.warning(f"No device tool client for session {session_id[:8]}")
        
        return False
    
    def get_device_tool_client(self, session_id: str) -> Optional['XiaozhiDeviceToolClient']:
        """
        Get device tool client for session.
        
        Args:
            session_id: Session ID
            
        Returns:
            XiaozhiDeviceToolClient or None
        """
        return self._device_tool_clients.get(session_id)
    
    # ============ Message handling hook (implements core hook) ============
    
    async def _on_message_received(self, session_id: str, data: Union[bytes, str]) -> bool:
        """
        Handle Xiaozhi-specific messages before Pipeline.
        
        Implements the core TransportBase._on_message_received() hook.
        
        Handles:
        - hello message: Check for MCP support and initialize device tools
        - mcp message: Route to DeviceToolClient (bypass Pipeline)
        - image message: Route to video_queue (bypass Pipeline, for vision support)
        
        Args:
            session_id: Session ID
            data: Raw message data
            
        Returns:
            True if message was handled (skip Pipeline)
            False if message should continue to Pipeline
        """
        # Only handle string messages (JSON)
        if not isinstance(data, str):
            return False  # Audio data goes to Pipeline
        
        try:
            message = json.loads(data)
            msg_type = message.get("type")
            
            # Handle hello message - check for MCP support
            if msg_type == "hello":
                # Check for audio parameters (PCM support)
                audio_params = message.get("audio_params", {})
                if audio_params.get("format") == "pcm":
                    codec = self._opus_codecs.get(session_id)
                    if codec:
                        codec.bypass = True
                        self.logger.info(f"Enabled PCM passthrough for session {session_id[:8]}")

                features = message.get("features", {})
                if features.get("mcp"):
                    self.logger.info(f"Device supports MCP, initializing tools for session {session_id[:8]}")
                    # Start MCP initialization in background
                    asyncio.create_task(self._init_device_tools(session_id))
                # hello still goes to Pipeline for normal handshake processing
                return False
            
            # Handle MCP messages - route to DeviceToolClient
            if msg_type == "mcp":
                if self._route_mcp_message(session_id, message):
                    return True  # MCP handled, skip Pipeline
                else:
                    self.logger.warning(f"MCP message not handled for session {session_id[:8]}")
                    return True  # Still skip Pipeline even if not handled
            
            # Handle IMAGE messages - route to video_queue (vision support)
            if msg_type == "image":
                await self._handle_image_message(session_id, message)
                return True  # Image handled, skip Pipeline
        
        except json.JSONDecodeError:
            pass  # Not JSON, let it go to Pipeline
        
        return False  # Pass to Pipeline
    
    async def _handle_image_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """
        Handle image message from device.
        
        Puts image data into video_queue for InputStation to process.
        
        Expected message format:
        {
            "type": "image",
            "data": "<base64 encoded image>",
            "trigger": "vad_start" | "heartbeat",
            "timestamp": 1701234567890,
            "width": 640,
            "height": 480,
            "format": "jpeg"
        }
        
        Args:
            session_id: Session ID
            message: Parsed image message
        """
        protocol = self.get_protocol()
        image_data = protocol.get_image_data(message)
        
        if not image_data:
            self.logger.warning(f"Failed to parse image data for session {session_id[:8]}")
            return
        
        video_queue = self.get_video_queue(session_id)
        
        try:
            # Use put_nowait to avoid blocking, drop frame if queue is full
            video_queue.put_nowait(image_data)
            trigger = image_data.get("trigger", "unknown")
            self.logger.debug(f"Image frame received: trigger={trigger}, session={session_id[:8]}")
        except asyncio.QueueFull:
            self.logger.warning(f"Video queue full, dropping frame for session {session_id[:8]}")
    
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
        
        # ============ Basic endpoints ============
        
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
        
        # ============ Include routers ============
        
        # OTA Router
        ota_router = create_ota_router(
            config=self.config,
            auth=self.auth,
            auth_enabled=self.auth_enable,
            allowed_devices=self.allowed_devices,
            get_websocket_url=self._get_websocket_url,
        )
        self.app.include_router(ota_router)
        
        # Vision Router
        vision_router = create_vision_router(
            config=self.config,
            auth=self.auth,
        )
        self.app.include_router(vision_router)
        
        # ============ WebSocket endpoint ============
        
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