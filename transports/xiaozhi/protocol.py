"""
Xiaozhi protocol implementation

Defines message format and encoding/decoding for Xiaozhi devices
"""

import json
from typing import Union, Dict, Any


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


class XiaozhiProtocol:
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
        Encode message for sending.
        
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
            return json.dumps(message, ensure_ascii=False)
    
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

