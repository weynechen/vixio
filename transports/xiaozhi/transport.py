"""
Xiaozhi WebSocket Transport with built-in protocol

This implementation includes:
1. Xiaozhi protocol handling (audio + control messages)
2. Input buffering before VAD
3. Output buffering for smooth playback
4. WebSocket server management
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Awaitable, Optional, Dict
import websockets
from websockets.server import WebSocketServerProtocol

from vixio.core.transport import TransportBase, TransportBufferMixin
from vixio.core.chunk import (
    Chunk,
    ChunkType,
    AudioChunk,
    ControlChunk,
    EventChunk,
    is_event_chunk,
)

logger = logging.getLogger(__name__)


class XiaozhiTransport(TransportBase, TransportBufferMixin):
    """
    Xiaozhi WebSocket Transport.
    
    This implementation includes:
    1. Xiaozhi protocol handling (audio + control messages)
    2. Input buffering before VAD
    3. Output buffering for smooth playback
    4. WebSocket server management
    
    User only needs to instantiate this class - protocol is built-in.
    
    Xiaozhi Protocol:
    - Binary messages: PCM audio (16kHz, mono, 16-bit)
    - JSON messages: Control commands
      {"type": "interrupt"} - Interrupt bot
      {"type": "hello", "data": {...}} - Handshake
      {"type": "stop"} - Stop session
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        """
        Initialize Xiaozhi transport.
        
        Args:
            host: Host to bind to (default: 0.0.0.0)
            port: Port to listen on (default: 8080)
        """
        TransportBufferMixin.__init__(self)
        self._host = host
        self._port = port
        self._connections: Dict[str, WebSocketServerProtocol] = {}  # connection_id -> websocket
        self._connection_handlers = []
        self._server = None
        self._running = False
        self.logger = logging.getLogger("vixio.transport.Xiaozhi")
    
    async def start(self) -> None:
        """Start Xiaozhi WebSocket server"""
        if self._running:
            self.logger.warning("Server already running")
            return
        
        async def handle_client(websocket: WebSocketServerProtocol, path: str):
            """Handle new WebSocket connection"""
            connection_id = str(uuid.uuid4())
            self._connections[connection_id] = websocket
            self._init_buffers(connection_id)
            
            self.logger.info(f"New connection: {connection_id[:8]} from {websocket.remote_address}")
            
            try:
                # Notify handlers about new connection
                for handler in self._connection_handlers:
                    # Run handler in background
                    asyncio.create_task(handler(connection_id))
                
                # Keep connection alive until client disconnects
                # The actual message handling is done in input_stream()
                await websocket.wait_closed()
            
            except Exception as e:
                self.logger.error(f"Error handling connection {connection_id[:8]}: {e}")
            
            finally:
                # Cleanup
                self._cleanup_buffers(connection_id)
                if connection_id in self._connections:
                    del self._connections[connection_id]
                self.logger.info(f"Connection closed: {connection_id[:8]}")
        
        try:
            self._server = await websockets.serve(
                handle_client,
                self._host,
                self._port
            )
            self._running = True
            self.logger.info(f"Xiaozhi server started on ws://{self._host}:{self._port}")
        
        except Exception as e:
            self.logger.error(f"Failed to start server: {e}")
            raise
    
    async def stop(self) -> None:
        """Stop WebSocket server"""
        if not self._running:
            return
        
        self.logger.info("Stopping Xiaozhi server...")
        
        # Close all connections
        for connection_id, ws in list(self._connections.items()):
            try:
                await ws.close()
            except Exception as e:
                self.logger.error(f"Error closing connection {connection_id[:8]}: {e}")
        
        # Stop server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        self._running = False
        self.logger.info("Xiaozhi server stopped")
    
    async def input_stream(self, connection_id: str) -> AsyncIterator[Chunk]:
        """
        Convert Xiaozhi WebSocket messages to Chunk stream.
        
        Xiaozhi protocol:
        - Binary data: PCM audio (16kHz, mono, 16-bit)
        - JSON: Control messages (interrupt, hello, etc.)
        
        Buffering strategy:
        - Buffer audio chunks initially
        - When VAD detects voice (EVENT_VAD_START), flush buffer and passthrough
        - When VAD stops (EVENT_VAD_END), resume buffering
        
        Args:
            connection_id: Connection ID to get input from
            
        Yields:
            Chunks from client
        """
        websocket = self._connections.get(connection_id)
        if not websocket:
            self.logger.error(f"Connection not found: {connection_id[:8]}")
            return
        
        self.logger.debug(f"Starting input stream for connection {connection_id[:8]}")
        
        try:
            async for raw_message in websocket:
                # Decode Xiaozhi message to Chunk
                chunk = self._decode_xiaozhi_message(raw_message, connection_id)
                if not chunk:
                    continue
                
                # Handle VAD events to control buffering
                if chunk.type == ChunkType.EVENT_VAD_START:
                    self.logger.debug(f"VAD started for {connection_id[:8]}, flushing buffer")
                    self._enable_input_passthrough(connection_id)
                    
                    # Flush buffered audio first
                    buffered = self._get_buffered_input(connection_id)
                    for buffered_chunk in buffered:
                        yield buffered_chunk
                    
                    yield chunk
                
                elif chunk.type == ChunkType.EVENT_VAD_END:
                    self.logger.debug(f"VAD ended for {connection_id[:8]}, resuming buffering")
                    yield chunk
                    self._disable_input_passthrough(connection_id)
                
                # Audio chunks: buffer or passthrough based on VAD state
                elif chunk.type == ChunkType.AUDIO_RAW:
                    if self._should_buffer_input(connection_id):
                        # Buffer audio (before VAD detection)
                        self._add_to_input_buffer(connection_id, chunk)
                    else:
                        # Passthrough audio (after VAD detection)
                        yield chunk
                
                # Other chunks: always passthrough
                else:
                    yield chunk
        
        except websockets.exceptions.ConnectionClosed:
            self.logger.info(f"WebSocket connection closed for {connection_id[:8]}")
        
        except Exception as e:
            self.logger.error(f"Input stream error for {connection_id[:8]}: {e}", exc_info=True)
            yield EventChunk(
                type=ChunkType.EVENT_ERROR,
                event_data={"error": str(e), "source": "input_stream"},
                source_station="XiaozhiTransport",
                session_id=connection_id
            )
    
    async def output_chunk(self, connection_id: str, chunk: Chunk) -> None:
        """
        Convert Chunk to Xiaozhi message and send.
        
        Output buffering strategy:
        - Buffer audio chunks
        - Play at controlled rate (20ms per chunk)
        - This prevents overwhelming the client
        
        Args:
            connection_id: Connection to send to
            chunk: Chunk to send
        """
        websocket = self._connections.get(connection_id)
        if not websocket:
            self.logger.warning(f"Cannot send to disconnected client: {connection_id[:8]}")
            return
        
        try:
            # Convert chunk to Xiaozhi message
            raw_message = self._encode_xiaozhi_message(chunk)
            if not raw_message:
                return
            
            # Audio chunks: buffer and control playback
            if chunk.type == ChunkType.AUDIO_ENCODED:
                self._add_to_output_buffer(connection_id, chunk)
                
                # Start playback if not already playing
                if not self._output_playing.get(connection_id):
                    await self._flush_output_buffer(
                        connection_id,
                        lambda data: websocket.send(data)
                    )
            else:
                # Non-audio: send immediately
                await websocket.send(raw_message)
        
        except websockets.exceptions.ConnectionClosed:
            self.logger.debug(f"Connection closed while sending to {connection_id[:8]}")
        
        except Exception as e:
            self.logger.error(f"Output error for {connection_id[:8]}: {e}")
    
    async def on_new_connection(
        self,
        handler: Callable[[str], Awaitable[None]]
    ) -> None:
        """
        Register callback for new connections.
        
        Args:
            handler: Async function called with connection_id when client connects
        """
        self._connection_handlers.append(handler)
        self.logger.debug(f"Registered connection handler, total: {len(self._connection_handlers)}")
    
    # ============ Xiaozhi Protocol Methods ============
    
    def _decode_xiaozhi_message(self, raw_message, connection_id: str) -> Optional[Chunk]:
        """
        Decode Xiaozhi message to Chunk.
        
        Xiaozhi protocol:
        - JSON: {"type": "interrupt"} -> CONTROL_INTERRUPT
        - JSON: {"type": "hello", "data": {...}} -> CONTROL_HELLO
        - JSON: {"type": "stop"} -> CONTROL_STOP
        - Binary: PCM audio data -> AUDIO_RAW
        
        Args:
            raw_message: Raw message from WebSocket (bytes or str)
            connection_id: Connection ID for session tracking
            
        Returns:
            Decoded Chunk or None if invalid
        """
        # Try parse as JSON (control message)
        if isinstance(raw_message, str):
            try:
                msg = json.loads(raw_message)
                return self._decode_json_message(msg, connection_id)
            except json.JSONDecodeError:
                self.logger.warning(f"Invalid JSON from {connection_id[:8]}: {raw_message[:100]}")
                return None
        
        # Also try parsing bytes as JSON
        if isinstance(raw_message, bytes):
            try:
                msg = json.loads(raw_message.decode('utf-8'))
                return self._decode_json_message(msg, connection_id)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Not JSON, treat as audio
                pass
        
        # Treat as binary audio data (PCM, 16kHz, mono, 16-bit)
        if isinstance(raw_message, bytes) and len(raw_message) > 0:
            return AudioChunk(
                type=ChunkType.AUDIO_RAW,
                data=raw_message,
                sample_rate=16000,
                channels=1,
                session_id=connection_id
            )
        
        return None
    
    def _decode_json_message(self, msg: dict, connection_id: str) -> Optional[Chunk]:
        """Decode JSON message to control chunk"""
        msg_type = msg.get("type", "")
        
        if msg_type == "interrupt":
            return ControlChunk(
                type=ChunkType.CONTROL_INTERRUPT,
                command="interrupt",
                params=msg.get("data", {}),
                session_id=connection_id
            )
        elif msg_type == "hello":
            return ControlChunk(
                type=ChunkType.CONTROL_HELLO,
                command="hello",
                params=msg.get("data", {}),
                session_id=connection_id
            )
        elif msg_type == "stop":
            return ControlChunk(
                type=ChunkType.CONTROL_STOP,
                command="stop",
                params=msg.get("data", {}),
                session_id=connection_id
            )
        elif msg_type == "start":
            return ControlChunk(
                type=ChunkType.CONTROL_START,
                command="start",
                params=msg.get("data", {}),
                session_id=connection_id
            )
        else:
            self.logger.warning(f"Unknown message type: {msg_type}")
            return None
    
    def _encode_xiaozhi_message(self, chunk: Chunk) -> Optional[bytes]:
        """
        Encode Chunk to Xiaozhi message.
        
        Xiaozhi protocol:
        - AUDIO_ENCODED -> binary data
        - Events -> JSON: {"type": "event.xxx", "data": ..., "timestamp": ...}
        - Control -> JSON: {"type": "control.xxx", ...}
        
        Args:
            chunk: Chunk to encode
            
        Returns:
            Encoded message as bytes, or None if not sendable
        """
        # Audio: send as binary
        if chunk.type == ChunkType.AUDIO_ENCODED:
            return chunk.data if isinstance(chunk.data, bytes) else None
        
        # Signals: send as JSON
        elif chunk.is_signal():
            message_dict = {
                "type": chunk.type.value,
                "timestamp": chunk.timestamp,
            }
            
            # Add type-specific data
            if is_event_chunk(chunk):
                if hasattr(chunk, 'event_data') and chunk.event_data:
                    message_dict["data"] = chunk.event_data
                if hasattr(chunk, 'source_station') and chunk.source_station:
                    message_dict["source"] = chunk.source_station
            elif hasattr(chunk, 'params'):
                message_dict["data"] = chunk.params
            
            # Add generic data if present
            if chunk.data and not message_dict.get("data"):
                message_dict["data"] = chunk.data
            
            return json.dumps(message_dict).encode('utf-8')
        
        # Text chunks: send as JSON
        elif chunk.type in (ChunkType.TEXT, ChunkType.TEXT_DELTA):
            content = getattr(chunk, 'content', None) or getattr(chunk, 'delta', '')
            message_dict = {
                "type": chunk.type.value,
                "text": content,
                "timestamp": chunk.timestamp,
            }
            return json.dumps(message_dict).encode('utf-8')
        
        return None
