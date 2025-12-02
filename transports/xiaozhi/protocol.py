"""
Xiaozhi protocol implementation

Defines message format and encoding/decoding for Xiaozhi devices
"""

import json
from typing import Union, Dict, Any, Optional
from core.protocol import ProtocolBase
from core.chunk import Chunk, ChunkType, AudioChunk, TextChunk, ControlChunk
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
        self.logger = logger.bind(name="XiaozhiProtocol")
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
                type=ChunkType.AUDIO_RAW,
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
                    type=ChunkType.CONTROL_INTERRUPT,
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
                type=ChunkType.CONTROL_HELLO,
                command="hello",
                params=message,
                session_id=session_id,
                turn_id=turn_id
            )
        
        # Abort message → ControlChunk
        elif msg_type == "abort":
            return ControlChunk(
                type=ChunkType.CONTROL_INTERRUPT,
                command="abort",
                params={"reason": "client_abort"},
                session_id=session_id,
                turn_id=turn_id
            )
        
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
    
    def create_hello_message(self, session_id: str = None, **kwargs) -> Dict[str, Any]:
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
        text: str = None,
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
        session_id: str = None,
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
        session_id: str = None,
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
        session_id: str = None,
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
        session_id: str = None,
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
    
    # ============ Business interface layer implementation ============
    
    def handshake(self, session_id: str, **params) -> Dict[str, Any]:
        """Create HELLO handshake message (business interface)."""
        return self.create_hello_message(session_id=session_id, **params)
    
    def send_stt(self, session_id: str, text: str, **params) -> Dict[str, Any]:
        """Create STT message (business interface)."""
        return self.create_stt_message(text=text, session_id=session_id, **params)
    
    def send_llm(self, session_id: str, text: str, **params) -> Dict[str, Any]:
        """Create LLM message (business interface)."""
        return self.create_llm_message(text=text, session_id=session_id, **params)
    
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
    
    def send_error(self, session_id: str, error: str, **params) -> Dict[str, Any]:
        """Create error message (business interface)."""
        return self.create_error_message(error=error, session_id=session_id, **params)
    
    def abort(self, session_id: str, reason: str = None, **params) -> Dict[str, Any]:
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

