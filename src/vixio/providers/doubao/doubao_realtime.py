"""
Doubao Realtime Dialog Provider

Volcengine (火山引擎) end-to-end realtime voice conversation via WebSocket.
Features:
- Integrated VAD + ASR + LLM + TTS in one service
- Server-side VAD for automatic turn detection
- Streaming audio input and output
- System prompt and speaking style customization

Reference: https://www.volcengine.com/docs/6561/1594356?lang=zh

Requires: websockets>=14.0
"""

import asyncio
import gzip
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import websockets
from loguru import logger as base_logger

from vixio.core.tools.types import ToolDefinition
from vixio.providers.realtime import BaseRealtimeProvider, RealtimeEvent, RealtimeEventType
from vixio.providers.registry import register_provider


# ==============================================================================
# Protocol Constants
# ==============================================================================

PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

# Message Types
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

# Message Type Flags
NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_SEQUENCE_1 = 0b0011
MSG_WITH_EVENT = 0b0100

# Serialization
NO_SERIALIZATION = 0b0000
JSON_SERIALIZATION = 0b0001

# Compression
NO_COMPRESSION = 0b0000
GZIP_COMPRESSION = 0b0001


# ==============================================================================
# Event Types
# ==============================================================================

class DialogEventType:
    """Doubao Dialog event types"""
    # Connection events
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FINISHED = 52
    
    # Session events
    START_SESSION = 100
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    USAGE_RESPONSE = 154  # Usage statistics
    
    # Task events
    TASK_REQUEST = 200
    
    # TTS events
    SAY_HELLO = 300
    TTS_SENTENCE_START = 350
    TTS_SENTENCE_END = 351
    TTS_ENDED = 359
    
    # ASR events
    ASR_INFO = 450  # User started speaking / interruption
    ASR_RESPONSE = 451
    ASR_ENDED = 459
    
    # Chat events
    CHAT_TTS_TEXT = 500
    CHAT_TEXT_QUERY = 501
    
    # LLM Response events
    CHAT_RESPONSE = 550  # LLM text response delta
    CHAT_ENDED = 559  # LLM response complete


# ==============================================================================
# Protocol Helpers
# ==============================================================================

def generate_header(
    version: int = PROTOCOL_VERSION,
    message_type: int = CLIENT_FULL_REQUEST,
    message_flags: int = MSG_WITH_EVENT,
    serialization: int = JSON_SERIALIZATION,
    compression: int = GZIP_COMPRESSION,
) -> bytearray:
    """Generate protocol header."""
    header = bytearray()
    header_size = DEFAULT_HEADER_SIZE
    header.append((version << 4) | header_size)
    header.append((message_type << 4) | message_flags)
    header.append((serialization << 4) | compression)
    header.append(0x00)  # reserved
    return header


def parse_response(res: bytes, logger=None) -> Dict[str, Any]:
    """
    Parse server response.
    
    IMPORTANT: This follows the exact same logic as the reference implementation.
    The event/seq are read from payload[:4] (same position), then start is incremented.
    """
    if isinstance(res, str) or len(res) < 4:
        return {}
    
    # Parse header
    protocol_version = res[0] >> 4
    header_size = res[0] & 0x0f
    message_type = res[1] >> 4
    message_type_specific_flags = res[1] & 0x0f
    serialization_method = res[2] >> 4
    message_compression = res[2] & 0x0f
    reserved = res[3]
    header_extensions = res[4:header_size * 4]
    
    payload = res[header_size * 4:]
    result = {
        '_header': {
            'message_type': message_type,
            'message_flags': message_type_specific_flags,
            'serialization': serialization_method,
            'compression': message_compression,
        }
    }
    payload_msg = None
    payload_size = 0
    start = 0
    
    if message_type == SERVER_FULL_RESPONSE or message_type == SERVER_ACK:
        result['message_type'] = 'SERVER_FULL_RESPONSE'
        if message_type == SERVER_ACK:
            result['message_type'] = 'SERVER_ACK'
        
        # IMPORTANT: Follow reference implementation exactly!
        # Both seq and event read from payload[:4], then start is incremented
        if message_type_specific_flags & NEG_SEQUENCE > 0:
            result['seq'] = int.from_bytes(payload[:4], "big", signed=False)
            start += 4
        if message_type_specific_flags & MSG_WITH_EVENT > 0:
            result['event'] = int.from_bytes(payload[:4], "big", signed=False)
            start += 4
        
        payload = payload[start:]
        
        # Parse session_id
        session_id_size = int.from_bytes(payload[:4], "big", signed=True)
        session_id = payload[4:session_id_size + 4]
        result['session_id'] = str(session_id, 'utf-8', errors='ignore') if session_id_size > 0 else ''
        payload = payload[4 + session_id_size:]
        
        # Parse payload
        payload_size = int.from_bytes(payload[:4], "big", signed=False)
        payload_msg = payload[4:]
        result['payload_size'] = payload_size
        
    elif message_type == SERVER_ERROR_RESPONSE:
        code = int.from_bytes(payload[:4], "big", signed=False)
        result['code'] = code
        result['message_type'] = 'SERVER_ERROR'
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:]
    
    if payload_msg is None:
        return result
    
    # Decompress if GZIP
    if message_compression == GZIP_COMPRESSION:
        try:
            payload_msg = gzip.decompress(payload_msg)
        except Exception as e:
            if logger:
                logger.debug(f"Gzip decompress failed: {e}")
    
    # For audio data (SERVER_ACK with NO_SERIALIZATION), return raw bytes
    if message_type == SERVER_ACK and serialization_method == NO_SERIALIZATION:
        result['payload_msg'] = payload_msg
        return result
    
    # Deserialize if needed
    if serialization_method == JSON_SERIALIZATION:
        try:
            payload_msg = json.loads(str(payload_msg, "utf-8"))
        except Exception:
            pass
    elif serialization_method != NO_SERIALIZATION:
        try:
            payload_msg = str(payload_msg, "utf-8")
        except Exception:
            pass
    
    result['payload_msg'] = payload_msg
    return result


# ==============================================================================
# Realtime Provider
# ==============================================================================


@register_provider("doubao-realtime")
class DoubaoRealtimeProvider(BaseRealtimeProvider):
    """
    Doubao Realtime Dialog Provider.
    
    End-to-end voice conversation using Volcengine's realtime dialogue API.
    Integrates VAD + ASR + LLM + TTS in one service.
    
    Features:
    - Server-side VAD for automatic turn detection
    - Streaming audio input and output
    - System prompt and speaking style customization
    - Say-hello support for initial greeting
    
    """
    
    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service."""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """Realtime model is stateful - maintains conversation state."""
        return True
    
    @property
    def category(self) -> str:
        """Provider category."""
        return "realtime"
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema."""
        return {
            "app_id": {
                "type": "string",
                "required": True,
                "description": "Volcengine App ID (X-Api-App-ID)",
            },
            "access_token": {
                "type": "string",
                "required": True,
                "description": "Volcengine Access Key (X-Api-Access-Key)",
            },
            "app_key": {
                "type": "string",
                "default": "PlgvMymc7f3tQnJ6",
                "description": "Volcengine App Key (X-Api-App-Key)",
            },
            "resource_id": {
                "type": "string",
                "default": "volc.speech.dialog",
                "description": "Resource ID (usually fixed as volc.speech.dialog)",
            },
            "endpoint": {
                "type": "string",
                "default": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
                "description": "WebSocket endpoint URL",
            },
            "speaker": {
                "type": "string",
                "default": "zh_female_tianmei",
                "description": "TTS speaker/voice type",
            },
            "bot_name": {
                "type": "string",
                "default": "小智",
                "description": "Bot name for dialog",
            },
            "system_role": {
                "type": "string",
                "default": "你是一个友好的AI助手。",
                "description": "System role/prompt",
            },
            "speaking_style": {
                "type": "string",
                "default": "简洁明了，语速适中。",
                "description": "Speaking style description",
            },
            "input_sample_rate": {
                "type": "int",
                "default": 16000,
                "description": "Input audio sample rate (Hz)",
            },
            "output_sample_rate": {
                "type": "int",
                "default": 24000,
                "description": "Output audio sample rate (Hz)",
            },
            "output_format": {
                "type": "string",
                "default": "pcm",
                "description": "Output audio format (pcm, pcm_s16le)",
            },
            "end_smooth_window_ms": {
                "type": "int",
                "default": 1500,
                "description": "ASR end smoothing window (ms)",
            },
            "recv_timeout": {
                "type": "int",
                "default": 30,
                "description": "Receive timeout in seconds (10-120)",
            },
        }
    
    def __init__(
        self,
        app_id: str,
        access_token: str,
        app_key: str="PlgvMymc7f3tQnJ6",
        resource_id: str = "volc.speech.dialog",
        endpoint: str = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
        speaker: str = "zh_female_tianmei",
        bot_name: str = "小智",
        system_role: str = "你是一个友好的AI助手。",
        speaking_style: str = "简洁明了，语速适中。",
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        output_format: str = "pcm",
        end_smooth_window_ms: int = 1500,
        recv_timeout: int = 30,
    ):
        """Initialize Doubao Realtime provider."""
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.app_id = app_id
        self.access_key = access_token
        self.app_key = app_key
        self.resource_id = resource_id
        self.endpoint = endpoint
        self.speaker = speaker
        self.bot_name = bot_name
        self.system_role = system_role
        self.speaking_style = speaking_style
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.output_format = output_format
        self.end_smooth_window_ms = end_smooth_window_ms
        self.recv_timeout = min(max(recv_timeout, 10), 120)  # Clamp to 10-120
        
        # Connection state
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._is_connected = False
        self._session_id: str = ""
        self._logid: str = ""
        
        # Response queue for stream_response pattern
        self._response_queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._response_active = False
        self._receive_task: Optional[asyncio.Task] = None
        
        # State tracking
        self._is_user_speaking = False
        self._session_finished = asyncio.Event()
        
        self.logger = base_logger.bind(component=self.name)
        self.logger.info(
            f"Initialized Doubao Realtime provider "
            f"(speaker={speaker}, bot_name={bot_name})"
        )
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._is_connected
    
    def _convert_float32_to_int16(self, float32_data: bytes) -> bytes:
        """
        Convert float32 PCM audio to int16 PCM.
        
        Doubao E2E API returns float32 PCM with range [-1.0, 1.0].
        We need to convert to int16 PCM with range [-32768, 32767].
        
        Args:
            float32_data: Float32 PCM audio bytes (little-endian)
            
        Returns:
            Int16 PCM audio bytes (little-endian)
        """
        if not float32_data:
            return b''
        
        try:
            # Convert bytes to float32 numpy array
            float_array = np.frombuffer(float32_data, dtype=np.float32)
            
            # Clip to [-1.0, 1.0] range (safety check)
            float_array = np.clip(float_array, -1.0, 1.0)
            
            # Scale to int16 range and convert
            int16_array = (float_array * 32767).astype(np.int16)
            
            return int16_array.tobytes()
        except Exception as e:
            self.logger.error(f"Failed to convert float32 to int16: {e}")
            # Return original data on error (may still produce noise but at least won't crash)
            return float32_data
    
    async def initialize(self) -> None:
        """Initialize the provider."""
        await self.connect()
    
    async def connect(self) -> None:
        """Establish WebSocket connection and start session."""
        if self._is_connected:
            return
        
        self._session_id = str(uuid.uuid4())
        connect_id = str(uuid.uuid4())
        
        headers = {
            "X-Api-App-ID": self.app_id,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-App-Key": self.app_key,
            "X-Api-Connect-Id": connect_id,
        }
        
        self.logger.info(f"Connecting to {self.endpoint}")
        
        try:
            self._websocket = await websockets.connect(
                self.endpoint,
                additional_headers=headers,
                ping_interval=None,
                max_size=10 * 1024 * 1024,
            )
            
            # Get logid from response
            if hasattr(self._websocket, "response") and self._websocket.response:
                self._logid = self._websocket.response.headers.get("X-Tt-Logid", "")
                self.logger.info(f"Connected, logid: {self._logid}")
            
            # Start Connection
            await self._send_start_connection()
            response = await self._receive_response()
            if response.get('event') != DialogEventType.CONNECTION_STARTED:
                raise RuntimeError(f"StartConnection failed: {response}")
            self.logger.debug("Connection started")
            
            # Start Session
            await self._send_start_session()
            response = await self._receive_response()
            if response.get('event') != DialogEventType.SESSION_STARTED:
                raise RuntimeError(f"StartSession failed: {response}")
            self.logger.debug("Session started")
            
            self._is_connected = True
            self._session_finished.clear()
            
            # Start background receive task
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            self._emit_event(RealtimeEvent(RealtimeEventType.SESSION_CREATED))
            self.logger.info("Doubao Realtime connection established")
            
        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            self._websocket = None
            raise
    
    async def _send_start_connection(self) -> None:
        """Send StartConnection request."""
        request = bytearray(generate_header())
        request.extend(int(DialogEventType.START_CONNECTION).to_bytes(4, 'big'))
        payload = gzip.compress(b"{}")
        request.extend(len(payload).to_bytes(4, 'big'))
        request.extend(payload)
        await self._websocket.send(request)
    
    async def _send_start_session(self) -> None:
        """Send StartSession request."""
        session_params = {
            "asr": {
                "extra": {
                    "end_smooth_window_ms": self.end_smooth_window_ms,
                },
            },
            "tts": {
                "speaker": self.speaker,
                "audio_config": {
                    "channel": 1,
                    "format": self.output_format,
                    "sample_rate": self.output_sample_rate,
                },
            },
            "dialog": {
                "bot_name": self.bot_name,
                "system_role": self.system_role,
                "speaking_style": self.speaking_style,
                "extra": {
                    "strict_audit": False,
                    "recv_timeout": self.recv_timeout,
                    "input_mod": "audio",
                },
            },
        }
        
        payload = gzip.compress(json.dumps(session_params).encode())
        session_id_bytes = self._session_id.encode()
        
        request = bytearray(generate_header())
        request.extend(int(DialogEventType.START_SESSION).to_bytes(4, 'big'))
        request.extend(len(session_id_bytes).to_bytes(4, 'big'))
        request.extend(session_id_bytes)
        request.extend(len(payload).to_bytes(4, 'big'))
        request.extend(payload)
        await self._websocket.send(request)
    
    async def _receive_response(self) -> Dict[str, Any]:
        """Receive and parse a response."""
        data = await self._websocket.recv()
        return parse_response(data, self.logger)
    
    async def _receive_loop(self) -> None:
        """Background task to receive and process events."""
        try:
            while self._is_connected:
                try:
                    data = await asyncio.wait_for(
                        self._websocket.recv(),
                        timeout=self.recv_timeout + 5
                    )
                    response = parse_response(data, self.logger)
                    self._handle_response(response)
                    
                    # Check for session end
                    event = response.get('event')
                    if event in [DialogEventType.SESSION_FINISHED, DialogEventType.SESSION_FAILED]:
                        self.logger.info(f"Session ended with event {event}")
                        self._session_finished.set()
                        break
                        
                except asyncio.TimeoutError:
                    self.logger.warning("Receive timeout in background task")
                    continue
                except websockets.ConnectionClosed:
                    self.logger.info("WebSocket connection closed")
                    break
                    
        except asyncio.CancelledError:
            self.logger.debug("Receive task cancelled")
        except Exception as e:
            self.logger.error(f"Error in receive loop: {e}")
            self._emit_event(RealtimeEvent(
                type=RealtimeEventType.ERROR,
                text=str(e)
            ))
        finally:
            self._is_connected = False
            self._response_active = False
    
    def _handle_response(self, response: Dict[str, Any]) -> None:
        """Handle a parsed response and emit events."""
        if not response:
            return
        
        message_type = response.get('message_type')
        event = response.get('event')
        payload = response.get('payload_msg')
        header = response.get('_header', {})
        
        # Log all responses for debugging
        if message_type == 'SERVER_ACK':
            payload_size = len(payload) if isinstance(payload, bytes) else 0
            # Log first bytes to verify PCM audio data
            first_bytes = ""
            if isinstance(payload, bytes) and len(payload) >= 16:
                first_bytes = payload[:16].hex()
            self.logger.debug(
                f"SERVER_ACK: event={event}, audio_size={payload_size}, "
                f"compression={header.get('compression')}, serial={header.get('serialization')}, "
                f"first_16_bytes={first_bytes}"
            )
        else:
            self.logger.debug(f"Response: type={message_type}, event={event}, payload={str(payload)[:100]}")
        
        # Audio data
        if message_type == 'SERVER_ACK' and isinstance(payload, bytes):
            if len(payload) > 0:
                # Doubao E2E API returns float32 PCM, convert to int16
                pcm_int16 = self._convert_float32_to_int16(payload)
                self._emit_event(RealtimeEvent(
                    type=RealtimeEventType.AUDIO_DELTA,
                    data=pcm_int16
                ))
            return
        
        # Event-based responses
        if event == DialogEventType.ASR_INFO:
            # User started speaking (interruption)
            self.logger.info("ASR_INFO (450): User speaking detected, clearing audio queue")
            self._is_user_speaking = True
            self._emit_event(RealtimeEvent(RealtimeEventType.SPEECH_START))
        
        elif event == DialogEventType.ASR_RESPONSE:
            # ASR transcript (451)
            self.logger.debug(f"ASR_RESPONSE (451): payload={payload}")
            if isinstance(payload, dict):
                transcript = payload.get('text', '') or payload.get('transcript', '')
                if transcript:
                    self._emit_event(RealtimeEvent(
                        type=RealtimeEventType.TRANSCRIPT_COMPLETE,
                        text=transcript
                    ))
        
        elif event == DialogEventType.ASR_ENDED:
            # User stopped speaking (459)
            self.logger.info("ASR_ENDED (459): User stopped speaking")
            self._is_user_speaking = False
            self._emit_event(RealtimeEvent(RealtimeEventType.SPEECH_STOP))
        
        elif event == DialogEventType.TTS_SENTENCE_START:
            # Bot started responding (350)
            self.logger.info(f"TTS_SENTENCE_START (350): payload={payload}")
            self._emit_event(RealtimeEvent(RealtimeEventType.RESPONSE_START))
        
        elif event == DialogEventType.TTS_SENTENCE_END:
            # TTS sentence ended (351)
            self.logger.debug(f"TTS_SENTENCE_END (351): payload={payload}")
        
        elif event == DialogEventType.TTS_ENDED:
            # Bot finished responding (359)
            self.logger.info("TTS_ENDED (359): Bot finished speaking")
            self._emit_event(RealtimeEvent(RealtimeEventType.RESPONSE_DONE))
        
        elif event == DialogEventType.CHAT_RESPONSE:
            # LLM text response delta (550)
            if isinstance(payload, dict):
                content = payload.get('content', '')
                if content:
                    self.logger.debug(f"CHAT_RESPONSE (550): '{content}'")
                    self._emit_event(RealtimeEvent(
                        type=RealtimeEventType.TEXT_DELTA,
                        text=content
                    ))
        
        elif event == DialogEventType.CHAT_ENDED:
            # LLM response complete (559)
            self.logger.debug("CHAT_ENDED (559): LLM response complete")
        
        elif event == DialogEventType.USAGE_RESPONSE:
            # Usage statistics (154)
            self.logger.debug(f"USAGE_RESPONSE (154): {payload}")
        
        elif event == DialogEventType.SESSION_STARTED:
            self.logger.info(f"SESSION_STARTED (150): payload={payload}")
        
        elif event == DialogEventType.SESSION_FINISHED:
            self.logger.info(f"SESSION_FINISHED (152): payload={payload}")
        
        elif message_type == 'SERVER_ERROR':
            error_msg = str(payload) if payload else "Unknown error"
            # AudioASRIdleTimeoutError is a normal timeout, not a critical error
            if "AudioASRIdleTimeoutError" in error_msg or "52000009" in error_msg:
                self.logger.info(f"ASR idle timeout (user didn't speak): {error_msg}")
            else:
                self.logger.error(f"Server error: {error_msg}")
                self._emit_event(RealtimeEvent(
                    type=RealtimeEventType.ERROR,
                    text=error_msg
                ))
        
        else:
            # Log unknown events for debugging
            if event and event not in [50, 52, 150, 152, 153]:  # Skip connection events
                self.logger.warning(f"Unknown event {event}: payload={str(payload)[:200]}")
    
    def _emit_event(self, event: RealtimeEvent) -> None:
        """Emit event to handler and queue."""
        # Legacy callback
        if self._event_handler:
            self._event_handler(event)
        
        # Queue for stream_response
        if self._response_active:
            try:
                self._response_queue.put_nowait(event)
            except asyncio.QueueFull:
                self.logger.warning("Response queue full, dropping event")
    
    async def disconnect(self) -> None:
        """Close connection."""
        if not self._websocket:
            return
        
        try:
            # Send FinishSession
            await self._send_finish_session()
            
            # Wait for session to finish
            try:
                await asyncio.wait_for(self._session_finished.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Timeout waiting for session finish")
            
            # Send FinishConnection
            await self._send_finish_connection()
            
            await self._websocket.close()
        except Exception as e:
            self.logger.warning(f"Error during disconnect: {e}")
        
        # Cancel receive task
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        self._websocket = None
        self._is_connected = False
        self.logger.info("Doubao Realtime disconnected")
    
    async def _send_finish_session(self) -> None:
        """Send FinishSession request."""
        session_id_bytes = self._session_id.encode()
        payload = gzip.compress(b"{}")
        
        request = bytearray(generate_header())
        request.extend(int(DialogEventType.FINISH_SESSION).to_bytes(4, 'big'))
        request.extend(len(session_id_bytes).to_bytes(4, 'big'))
        request.extend(session_id_bytes)
        request.extend(len(payload).to_bytes(4, 'big'))
        request.extend(payload)
        await self._websocket.send(request)
    
    async def _send_finish_connection(self) -> None:
        """Send FinishConnection request."""
        payload = gzip.compress(b"{}")
        
        request = bytearray(generate_header())
        request.extend(int(DialogEventType.FINISH_CONNECTION).to_bytes(4, 'big'))
        request.extend(len(payload).to_bytes(4, 'big'))
        request.extend(payload)
        await self._websocket.send(request)
    
    async def cleanup(self) -> None:
        """Cleanup provider resources."""
        await self.disconnect()
    
    async def update_session(
        self,
        instructions: Optional[str] = None,
        tools: Optional[List[ToolDefinition]] = None,
        voice: Optional[str] = None
    ) -> None:
        """
        Update session configuration.
        
        Note: Doubao dialog doesn't support dynamic session updates.
        Configuration must be set before connecting.
        """
        if instructions:
            self.system_role = instructions
        if voice:
            self.speaker = voice
        
        if self._is_connected:
            self.logger.warning(
                "Session already connected. Changes will apply on next connection."
            )
        
        self._emit_event(RealtimeEvent(RealtimeEventType.SESSION_UPDATED))
    
    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio chunk to the model."""
        if not self._is_connected or not self._websocket:
            self.logger.warning("Cannot send audio: not connected")
            return
        
        # Compress audio
        compressed = gzip.compress(audio_data)
        session_id_bytes = self._session_id.encode()
        
        request = bytearray(generate_header(
            message_type=CLIENT_AUDIO_ONLY_REQUEST,
            serialization=NO_SERIALIZATION,
        ))
        request.extend(int(DialogEventType.TASK_REQUEST).to_bytes(4, 'big'))
        request.extend(len(session_id_bytes).to_bytes(4, 'big'))
        request.extend(session_id_bytes)
        request.extend(len(compressed).to_bytes(4, 'big'))
        request.extend(compressed)
        
        await self._websocket.send(request)
    
    async def say_hello(self, content: str = "你好，有什么可以帮助你的？") -> None:
        """
        Send a greeting message (TTS only, no LLM).
        
        This triggers the bot to speak the provided content directly.
        
        Args:
            content: Greeting text to speak
        """
        if not self._is_connected or not self._websocket:
            self.logger.warning("Cannot say hello: not connected")
            return
        
        payload = {"content": content}
        compressed = gzip.compress(json.dumps(payload).encode())
        session_id_bytes = self._session_id.encode()
        
        request = bytearray(generate_header())
        request.extend(int(DialogEventType.SAY_HELLO).to_bytes(4, 'big'))
        request.extend(len(session_id_bytes).to_bytes(4, 'big'))
        request.extend(session_id_bytes)
        request.extend(len(compressed).to_bytes(4, 'big'))
        request.extend(compressed)
        
        await self._websocket.send(request)
        self.logger.debug(f"Sent say_hello: {content[:50]}...")
    
    async def send_text_query(self, content: str) -> None:
        """
        Send a text query (for text-based input mode).
        
        This sends text directly to the LLM without ASR.
        
        Args:
            content: Text query to send
        """
        if not self._is_connected or not self._websocket:
            self.logger.warning("Cannot send text query: not connected")
            return
        
        payload = {"content": content}
        compressed = gzip.compress(json.dumps(payload).encode())
        session_id_bytes = self._session_id.encode()
        
        request = bytearray(generate_header())
        request.extend(int(DialogEventType.CHAT_TEXT_QUERY).to_bytes(4, 'big'))
        request.extend(len(session_id_bytes).to_bytes(4, 'big'))
        request.extend(session_id_bytes)
        request.extend(len(compressed).to_bytes(4, 'big'))
        request.extend(compressed)
        
        await self._websocket.send(request)
        self.logger.debug(f"Sent text query: {content[:50]}...")
    
    async def send_tool_output(self, tool_call_id: str, output: str) -> None:
        """
        Send tool execution result back to the model.
        
        Note: Tool calling may not be fully supported in Doubao dialog.
        """
        self.logger.warning("send_tool_output not implemented for Doubao Realtime")
    
    async def interrupt(self) -> None:
        """
        Interrupt current generation.
        
        In VAD mode, user speech automatically interrupts.
        For manual interrupt, we can send silent audio or use ASR_INFO simulation.
        """
        # Clear response queue
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.logger.debug("Interrupt: cleared response queue")
    
    async def stream_response(self) -> AsyncIterator[RealtimeEvent]:
        """
        Stream response events as async iterator.
        
        Yields events until RESPONSE_DONE or ERROR is received.
        
        Example:
            await provider.send_audio(audio_chunk)
            async for event in provider.stream_response():
                if event.type == RealtimeEventType.AUDIO_DELTA:
                    yield AudioChunk(data=event.data)
                elif event.type == RealtimeEventType.RESPONSE_DONE:
                    break
        
        Yields:
            RealtimeEvent: Events from the provider
        """
        self._response_active = True
        self.logger.debug("Stream response started")
        
        timeout = self.recv_timeout + 10
        start_time = asyncio.get_event_loop().time()
        
        try:
            while True:
                # Check timeout
                if asyncio.get_event_loop().time() - start_time > timeout:
                    self.logger.warning("Stream response timeout")
                    break
                
                try:
                    event = await asyncio.wait_for(
                        self._response_queue.get(),
                        timeout=0.1
                    )
                    
                    yield event
                    
                    # Stop on terminal events
                    if event.type in [RealtimeEventType.RESPONSE_DONE, RealtimeEventType.ERROR]:
                        break
                    
                    # Reset timeout on activity
                    start_time = asyncio.get_event_loop().time()
                    
                except asyncio.TimeoutError:
                    continue
                    
        finally:
            self._response_active = False
            self.logger.debug("Stream response ended")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration."""
        return {
            "provider": self.__class__.__name__,
            "name": self.name,
            "speaker": self.speaker,
            "bot_name": self.bot_name,
            "is_connected": self._is_connected,
        }
