"""
Xiaozhi protocol implementation

Defines message format and encoding/decoding for Xiaozhi devices
"""

import json
from typing import Union, Dict, Any, Optional, List
from vixio.core.protocol import ProtocolBase
from vixio.core.chunk import Chunk, ChunkType, AudioChunk, TextChunk, ControlChunk
from loguru import logger

class XiaozhiMessageType:
    """Xiaozhi message types"""
    HELLO = "hello"
    AUDIO = "audio"
    TEXT = "text"
    TTS = "tts"
    STT = "stt"  # Speech-to-text recognition result
    LLM = "llm"  # LLM response text
    CONTROL = "control"
    STATE = "state"
    ERROR = "error"
    MCP = "mcp"  # MCP JSON-RPC messages (function tools)
    IMAGE = "image"  # Image/video frame from device (vision support)


class XiaozhiControlAction:
    """Xiaozhi control actions"""
    INTERRUPT = "interrupt"
    START = "start"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"


class XiaozhiProtocol(ProtocolBase):
    """
    Xiaozhi protocol handler.
    
    Message format:
    - Text messages: JSON strings with 'type' field
    - Audio messages: Raw bytes (Opus-encoded audio)
    """
    
    def __init__(
        self,
        version: int = 1,
        audio_format: str = "opus",
        sample_rate: int = 16000,
        channels: int = 1,
        frame_duration: int = 60,
    ):
        """
        Initialize Xiaozhi protocol.
        
        Args:
            version: Protocol version
            audio_format: Audio format (opus)
            sample_rate: Audio sample rate
            channels: Number of audio channels
            frame_duration: Audio frame duration in ms
        """
        self.version = version
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_duration = frame_duration
        self.logger = logger.bind(component="XiaozhiProtocol")
        
        # Audio frame buffering (per-session)
        # Keeps incomplete tail frames for next chunk
        self._audio_buffers: Dict[str, bytes] = {}
    # ============ ProtocolBase interface implementation ============
    
    def decode_message(self, data: Union[bytes, str]) -> Dict[str, Any]:
        """
        Decode raw data to message dictionary (ProtocolBase interface).
        
        Delegates to parse_message().
        """
        return self.parse_message(data)
    
    def is_audio_message(self, data: Union[bytes, str]) -> bool:
        """
        Check if message is audio data.
        
        Args:
            data: Raw message data
            
        Returns:
            True if audio message
        """
        return isinstance(data, bytes)
    
    def parse_message(self, data: Union[bytes, str]) -> Dict[str, Any]:
        """
        Parse incoming message.
        
        Args:
            data: Raw message data
            
        Returns:
            Parsed message dictionary
        """
        # Binary data -> audio message
        if isinstance(data, bytes):
            return {
                "type": XiaozhiMessageType.AUDIO,
                "audio_data": data,
                "format": self.audio_format,
            }
        
        # Text data -> JSON message
        elif isinstance(data, str):
            try:
                msg = json.loads(data)
                # Ensure type field exists
                if "type" not in msg:
                    msg["type"] = XiaozhiMessageType.TEXT
                return msg
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON message: {e}")
        
        else:
            raise ValueError(f"Unsupported data type: {type(data)}")
    
    def encode_message(self, message: Dict[str, Any]) -> Union[bytes, str]:
        """
        Encode message for sending (ProtocolBase interface).
        
        Args:
            message: Message dictionary
            
        Returns:
            Encoded message (bytes for audio, str for JSON)
        """
        msg_type = message.get("type", XiaozhiMessageType.TEXT)
        
        # Audio message -> binary
        if msg_type == XiaozhiMessageType.AUDIO:
            return message.get("audio_data", b"")
        
        # Other messages -> JSON
        else:
            self.logger.info(f"Encoding message: {message}")
            return json.dumps(message, ensure_ascii=False)
    
    def message_to_chunk(
        self, 
        message: Dict[str, Any], 
        session_id: str, 
        turn_id: int
    ) -> Optional[Chunk]:
        """
        Convert Xiaozhi message to Chunk (ProtocolBase interface).
        
        Used by InputStation to convert incoming messages.
        """
        msg_type = message.get("type")
        
        # Audio message → AudioChunk
        if msg_type == XiaozhiMessageType.AUDIO:
            # Note: Opus decoding is handled by InputStation (needs codec state)
            return AudioChunk(
                type=ChunkType.AUDIO_DELTA,
                data=message.get("audio_data", b""),  # Opus encoded data
                sample_rate=self.sample_rate,
                channels=self.channels,
                session_id=session_id,
                turn_id=turn_id,
                source="Client"
            )
        
        # Control message → ControlChunk
        elif msg_type == XiaozhiMessageType.CONTROL:
            action = message.get("action", "")
            if action == XiaozhiControlAction.INTERRUPT:
                return ControlChunk(
                    type=ChunkType.CONTROL_STATE_RESET,
                    command="interrupt",
                    params={},
                    session_id=session_id,
                    turn_id=turn_id
                )
            elif action == XiaozhiControlAction.STOP:
                return ControlChunk(
                    type=ChunkType.CONTROL_STOP,
                    command="stop",
                    params={},
                    session_id=session_id,
                    turn_id=turn_id
                )
        
        # Text message → TextChunk
        elif msg_type == XiaozhiMessageType.TEXT:
            content = message.get("content", message.get("text", ""))
            if content:
                return TextChunk(
                    type=ChunkType.TEXT,
                    data=content,
                    session_id=session_id,
                    turn_id=turn_id,
                    source="Client"
                )
        
        # HELLO message → ControlChunk
        elif msg_type == XiaozhiMessageType.HELLO:
            return ControlChunk(
                type=ChunkType.CONTROL_HANDSHAKE,
                command="hello",
                params=message,
                session_id=session_id,
                turn_id=turn_id
            )
        
        # Abort message → ControlChunk
        elif msg_type == "abort":
            return ControlChunk(
                type=ChunkType.CONTROL_STATE_RESET,
                command="abort",
                params={"reason": "client_abort"},
                session_id=session_id,
                turn_id=turn_id
            )
        
        # MCP message → None (handled directly by Transport, not Pipeline)
        elif msg_type == XiaozhiMessageType.MCP:
            # Return None to signal Transport to handle this specially
            # MCP messages bypass Pipeline and go directly to DeviceToolClient
            return None
        
        return None
    
    def chunk_to_message(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        """
        Convert Chunk to Xiaozhi message (ProtocolBase interface).
        
        Default conversion method. Returns None for most chunks,
        letting OutputStation call specific business methods instead.
        """
        # Most chunks need special handling, return None
        # OutputStation will call corresponding business methods based on chunk.type and chunk.source
        return None
    
    def prepare_audio_data(
        self, 
        pcm_data: bytes, 
        sample_rate: int, 
        channels: int = 1,
        session_id: Optional[str] = None
    ) -> List[bytes]:
        """
        Prepare audio data for Xiaozhi transport with frame buffering.
        
        Xiaozhi requirements:
        - Sample rate: 16kHz
        - Frame duration: 60ms (configurable)
        - Frame size: sample_rate * frame_duration_ms / 1000 * 2 * channels bytes
        
        Buffering strategy:
        1. Prepend residual bytes from previous call (if any)
        2. Split into complete frames only
        3. Keep incomplete tail for next call
        4. Use flush_audio_buffer() to get final padded frame
        
        Args:
            pcm_data: Raw PCM audio data (16-bit signed little-endian)
            sample_rate: Input sample rate in Hz
            channels: Number of audio channels
            session_id: Session ID for stateful buffering (required)
            
        Returns:
            List of COMPLETE PCM frames ready for Opus encoding
        """
        if not pcm_data:
            return []
        
        if not session_id:
            self.logger.warning("session_id not provided, buffering disabled")
            session_id = "default"
        
        # 1. Resample to target sample rate if needed
        if sample_rate != self.sample_rate:
            from vixio.utils.audio.convert import resample_pcm
            self.logger.debug(
                f"Resampling audio from {sample_rate}Hz to {self.sample_rate}Hz "
                f"for Xiaozhi protocol"
            )
            try:
                pcm_data = resample_pcm(
                    pcm_data,
                    from_rate=sample_rate,
                    to_rate=self.sample_rate,
                    channels=channels,
                    sample_width=2
                )
            except Exception as e:
                self.logger.error(f"Failed to resample audio: {e}, using original data")
                # Continue with original data on error
        
        # 2. Prepend residual from previous chunk (if any)
        residual = self._audio_buffers.get(session_id, b'')
        if residual:
            self.logger.debug(
                f"Prepending {len(residual)} residual bytes from previous chunk "
                f"(session={session_id[:8]})"
            )
            pcm_data = residual + pcm_data
        
        # 3. Calculate frame size
        # frame_duration (ms) * sample_rate (Hz) / 1000 = samples per frame
        # samples * 2 bytes (16-bit) * channels = bytes per frame
        samples_per_frame = int(self.sample_rate * self.frame_duration / 1000)
        frame_size = samples_per_frame * 2 * channels
        
        # 4. Split into COMPLETE frames only
        frames = []
        offset = 0
        while offset + frame_size <= len(pcm_data):
            frame = pcm_data[offset:offset + frame_size]
            frames.append(frame)
            offset += frame_size
        
        # 5. Keep incomplete tail for next chunk
        tail = pcm_data[offset:]
        if tail:
            self._audio_buffers[session_id] = tail
            self.logger.debug(
                f"Buffered {len(tail)} bytes for next chunk "
                f"(session={session_id[:8]}, {len(tail)}/{frame_size} bytes)"
            )
        else:
            # No tail, clear buffer
            self._audio_buffers[session_id] = b''
        
        self.logger.debug(
            f"Split {len(pcm_data)} bytes into {len(frames)} complete frames "
            f"(session={session_id[:8]}, {self.frame_duration}ms @ {self.sample_rate}Hz)"
        )
        
        return frames
    
    def flush_audio_buffer(self, session_id: str) -> List[bytes]:
        """
        Flush remaining audio buffer (called on TTS_STOP).
        
        Returns any incomplete tail frame padded to complete frame size.
        
        Args:
            session_id: Session ID to flush buffer for
            
        Returns:
            List containing 0 or 1 padded frame
        """
        tail = self._audio_buffers.pop(session_id, b'')
        
        if not tail:
            self.logger.debug(f"No residual audio to flush (session={session_id[:8]})")
            return []
        
        # Calculate frame size
        samples_per_frame = int(self.sample_rate * self.frame_duration / 1000)
        frame_size = samples_per_frame * 2 * self.channels
        
        # Pad tail to complete frame
        padding_size = frame_size - len(tail)
        padded_frame = tail + (b'\x00' * padding_size)
        
        self.logger.info(
            f"Flushed audio buffer: padded {len(tail)} bytes with {padding_size} zero bytes "
            f"to complete {frame_size}-byte frame (session={session_id[:8]})"
        )
        
        return [padded_frame]
    
    def create_hello_message(self, session_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Create HELLO handshake message.
        
        Args:
            session_id: Optional session ID
            **kwargs: Additional parameters
            
        Returns:
            HELLO message dictionary
        """
        message = {
            "type": XiaozhiMessageType.HELLO,
            "version": self.version,
            "transport": "websocket",
            "audio_params": {
                "format": self.audio_format,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "frame_duration": self.frame_duration,
            },
        }
        
        if session_id:
            message["session_id"] = session_id
        
        message.update(kwargs)
        return message
    
    def create_tts_message(
        self,
        session_id: str,
        state: str,
        text: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create TTS state message.
        
        Args:
            session_id: Session ID
            state: TTS state (start, stop, sentence_start, sentence_end)
            text: Optional text content
            **kwargs: Additional parameters
            
        Returns:
            TTS message dictionary
        """
        message = {
            "type": XiaozhiMessageType.TTS,
            "session_id": session_id,
            "state": state,
        }
        
        if text is not None:
            message["text"] = text
        
        message.update(kwargs)
        return message
    
    def create_state_message(
        self,
        state: str,
        session_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create STATE message.
        
        Args:
            state: State description
            session_id: Optional session ID
            **kwargs: Additional parameters
            
        Returns:
            STATE message dictionary
        """
        message = {
            "type": XiaozhiMessageType.STATE,
            "state": state,
        }
        
        if session_id:
            message["session_id"] = session_id
        
        message.update(kwargs)
        return message
    
    def create_error_message(
        self,
        error: str,
        session_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create ERROR message.
        
        Args:
            error: Error description
            session_id: Optional session ID
            **kwargs: Additional parameters
            
        Returns:
            ERROR message dictionary
        """
        message = {
            "type": XiaozhiMessageType.ERROR,
            "error": error,
        }
        
        if session_id:
            message["session_id"] = session_id
        
        message.update(kwargs)
        return message
    
    def create_stt_message(
        self,
        text: str,
        session_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create STT (speech-to-text) message.
        
        Args:
            text: Recognized text from ASR
            session_id: Optional session ID
            **kwargs: Additional parameters
            
        Returns:
            STT message dictionary
        """
        message = {
            "type": XiaozhiMessageType.STT,
            "text": text,
        }
        
        if session_id:
            message["session_id"] = session_id
        
        message.update(kwargs)
        return message
    
    def create_llm_message(
        self,
        text: str,
        session_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create LLM message.
        
        Args:
            text: LLM response text
            session_id: Optional session ID
            **kwargs: Additional parameters
            
        Returns:
            LLM message dictionary
        """
        message = {
            "type": XiaozhiMessageType.LLM,
            "text": text,
        }
        
        if session_id:
            message["session_id"] = session_id
        
        message.update(kwargs)
        return message
    
    def create_mcp_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create MCP message for sending to device.
        
        MCP messages wrap JSON-RPC payloads for device tool interactions.
        
        Args:
            payload: JSON-RPC 2.0 payload (method, id, params, etc.)
            
        Returns:
            MCP message dictionary ready for encoding
        """
        return {
            "type": XiaozhiMessageType.MCP,
            "payload": payload
        }
    
    def is_mcp_message(self, message: Dict[str, Any]) -> bool:
        """
        Check if message is an MCP message.
        
        Args:
            message: Parsed message dictionary
            
        Returns:
            True if message type is MCP
        """
        return message.get("type") == XiaozhiMessageType.MCP
    
    def get_mcp_payload(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract MCP payload from message.
        
        Args:
            message: Parsed MCP message
            
        Returns:
            JSON-RPC payload or None if not MCP message
        """
        if self.is_mcp_message(message):
            return message.get("payload")
        return None
    
    def is_image_message(self, message: Dict[str, Any]) -> bool:
        """
        Check if message is an image/video frame message.
        
        Args:
            message: Parsed message dictionary
            
        Returns:
            True if message type is IMAGE
        """
        return message.get("type") == XiaozhiMessageType.IMAGE
    
    def get_image_data(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract image data from message.
        
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
            message: Parsed image message
            
        Returns:
            Image data dict or None if not image message
        """
        if not self.is_image_message(message):
            return None
        
        import base64
        import time
        
        # Decode base64 image data
        data_str = message.get("data", "")
        try:
            image_bytes = base64.b64decode(data_str) if data_str else b""
        except Exception:
            image_bytes = b""
        
        return {
            "data": image_bytes,
            "trigger": message.get("trigger", "heartbeat"),
            "timestamp": message.get("timestamp", time.time()),
            "width": message.get("width", 0),
            "height": message.get("height", 0),
            "format": message.get("format", "jpeg")
        }
    
    # ============ Business interface layer implementation ============
    
    def handshake(self, session_id: str, **params) -> Dict[str, Any]:
        """Create HELLO handshake message (business interface)."""
        return self.create_hello_message(session_id=session_id, **params)
    
    def send_stt(self, session_id: str, text: str, **params) -> Optional[Dict[str, Any]]:
        """Create STT message (business interface)."""
        if params.get("is_delta", False):
            return None
        return self.create_stt_message(text=text, session_id=session_id, **params)
    
    def send_llm(self, session_id: str, text: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create LLM message (business interface).
        
        Note: For Xiaozhi devices, we map LLM text to TTS 'sentence_start' events
        to ensure they are displayed as subtitles on the device screen.
        The native 'llm' message type might not be supported for subtitle display in all firmware versions.
        """
        if params.get("is_delta", False):
            return None
            
        # Map to TTS sentence_start event for subtitle display
        return self.create_tts_message(
            session_id=session_id,
            state="sentence_start",
            text=text,
            **params
        )
    
    def send_tts_audio(self, session_id: str, audio_data: bytes, **params) -> Dict[str, Any]:
        """Create TTS audio message (business interface)."""
        return {
            "type": XiaozhiMessageType.AUDIO,
            "audio_data": audio_data,
        }
    
    def send_tts_event(
        self, 
        session_id: str, 
        event: str,
        text: Optional[str] = None,
        **params
    ) -> Dict[str, Any]:
        """Create TTS event message (business interface)."""
        return self.create_tts_message(
            session_id=session_id,
            state=event,
            text=text,
            **params
        )

    def send_bot_thinking_event(self, session_id: str, **params) -> Dict[str, Any]:
        """Create bot thinking event message (business interface)."""
        return self.create_tts_message(
            session_id=session_id,
            state="start",
            **params
        )
    
    def send_error(self, session_id: str, error: str, **params) -> Dict[str, Any]:
        """Create error message (business interface)."""
        return self.create_error_message(error=error, session_id=session_id, **params)
    
    def abort(self, session_id: str, reason: Optional[str] = None, **params) -> Dict[str, Any]:
        """Create abort message (business interface)."""
        return {
            "type": "abort",
            "reason": reason,
            "session_id": session_id,
            **params
        }
    
    def start_listen(self, session_id: str, **params) -> Dict[str, Any]:
        """Create 'start listening' state message (business interface)."""
        return self.create_state_message(state="listening", session_id=session_id, **params)
    
    def stop_listen(self, session_id: str, **params) -> Dict[str, Any]:
        """Create 'stop listening' state message (business interface)."""
        return self.create_state_message(state="idle", session_id=session_id, **params)
    
    def start_speaker(self, session_id: str, **params) -> Dict[str, Any]:
        """Create 'start speaker' state message (business interface)."""
        return self.create_state_message(state="speaking", session_id=session_id, **params)
    
    def stop_speaker(self, session_id: str, **params) -> Dict[str, Any]:
        """Create 'stop speaker' state message (business interface)."""
        return self.create_state_message(state="idle", session_id=session_id, **params)

