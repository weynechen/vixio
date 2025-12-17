"""
Protocol base - Protocol abstraction layer

Defines two types of interfaces:
1. Message encoding/decoding (low-level)
2. Business methods (high-level)
"""

from abc import ABC, abstractmethod
from typing import Union, Dict, Any, Optional
from vixio.core.chunk import Chunk


class ProtocolBase(ABC):
    """
    Protocol base - defines message encoding/decoding and business interfaces.
    
    Provides two layers of interfaces:
    1. Message encoding/decoding layer: decode_message / encode_message
    2. Business interface layer: handshake / send_stt / send_tts etc.
    
    Business interfaces return message dictionaries, which are encoded and sent by OutputStation.
    """
    
    # ============ Message encoding/decoding layer (must implement) ============
    
    @abstractmethod
    def decode_message(self, data: Union[bytes, str]) -> Dict[str, Any]:
        """
        Decode raw data to message dictionary.
        
        Args:
            data: Raw data (received from WebSocket/TCP etc.)
            
        Returns:
            Message dictionary (includes type field etc.)
        """
        pass
    
    @abstractmethod
    def encode_message(self, message: Dict[str, Any]) -> Union[bytes, str]:
        """
        Encode message dictionary to raw data.
        
        Args:
            message: Message dictionary
            
        Returns:
            Raw data (to be sent via WebSocket/TCP etc.)
        """
        pass
    
    @abstractmethod
    def message_to_chunk(
        self, 
        message: Dict[str, Any], 
        session_id: str, 
        turn_id: int
    ) -> Optional[Chunk]:
        """
        Convert protocol message to Chunk (used by InputStation).
        
        Args:
            message: Decoded message dictionary
            session_id: Session ID
            turn_id: Current turn ID
            
        Returns:
            Chunk instance, or None if message does not need conversion
        """
        pass
    
    @abstractmethod
    def chunk_to_message(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        """
        Convert Chunk to protocol message (used by OutputStation, optional).
        
        This is the default conversion method. For Chunks that cannot be
        automatically converted, OutputStation will call business interface methods.
        
        Args:
            chunk: Chunk instance
            
        Returns:
            Message dictionary, or None if special handling is needed
        """
        pass
    
    @abstractmethod
    def prepare_audio_data(
        self, 
        pcm_data: bytes, 
        sample_rate: int, 
        channels: int = 1
    ) -> list[bytes]:
        """
        Prepare audio data for transport-specific sending.
        
        Protocol-specific processing includes:
        - Resample to target sample rate (if needed)
        - Split into frames according to transport requirements
        - Return list of PCM frame chunks (NOT encoded yet, encoding done by OutputStation)
        
        Different protocols have different requirements:
        - Xiaozhi: 16kHz, 60ms frames (1920 bytes)
        - WebRTC: 48kHz, 20ms frames
        
        Args:
            pcm_data: Raw PCM audio data
            sample_rate: Input sample rate in Hz
            channels: Number of audio channels (default: 1)
            
        Returns:
            List of PCM frames ready for encoding by codec
        """
        pass
    
    # ============ Business interface layer (optional implementation) ============
    # These methods are called by OutputStation based on Chunk type
    # Returns message dictionary (not encoded data)
    
    def handshake(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create handshake message (e.g., HELLO).
        
        Called when connection is established or Pipeline is ready.
        
        Args:
            session_id: Session ID
            **params: Protocol-specific parameters
            
        Returns:
            Handshake message dictionary, or None if protocol doesn't need handshake
        """
        return None
    
    def send_text(
        self, 
        session_id: str, 
        text: str, 
        source: str = "",
        **params
    ) -> Optional[Dict[str, Any]]:
        """
        Create TEXT message with source information.
        
        DAG decoupling: OutputStation passes type + source to Protocol.
        Protocol decides message format based on source.
        
        Default implementation routes based on source:
        - "asr" or similar -> send_stt()
        - "agent" or similar -> send_llm()
        - others -> generic text message
        
        Subclasses can override for custom routing.
        
        Args:
            session_id: Session ID
            text: Text content
            source: Source station name (e.g., "asr", "agent", "TextAggregator")
            **params: Protocol-specific parameters
            
        Returns:
            Text message dictionary
        """
        source_lower = source.lower() if source else ""
        
        if "asr" in source_lower:
            return self.send_stt(session_id, text, **params)
        elif "agent" in source_lower:
            return self.send_llm(session_id, text, **params)
        else:
            # Default: try send_llm as fallback
            return self.send_llm(session_id, text, **params)
    
    def send_text_delta(
        self, 
        session_id: str, 
        text: str, 
        source: str = "",
        **params
    ) -> Optional[Dict[str, Any]]:
        """
        Create TEXT_DELTA (streaming text) message with source information.
        
        DAG decoupling: OutputStation passes type + source to Protocol.
        Protocol decides message format based on source.
        
        Default implementation routes based on source:
        - "asr" -> send_stt() for streaming ASR
        - "agent" -> send_llm() for streaming LLM
        - others -> generic text delta message
        
        Subclasses can override for custom routing.
        
        Args:
            session_id: Session ID
            text: Text delta content
            source: Source station name (e.g., "asr", "agent")
            **params: Protocol-specific parameters
            
        Returns:
            Text delta message dictionary
        """
        source_lower = source.lower() if source else ""
        
        if "asr" in source_lower:
            return self.send_stt(session_id, text, is_delta=True, **params)
        elif "agent" in source_lower:
            return self.send_llm(session_id, text, is_delta=True, **params)
        else:
            # Default: try send_llm as fallback
            return self.send_llm(session_id, text, is_delta=True, **params)
    
    def send_stt(self, session_id: str, text: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create STT (speech recognition) message.
        
        Called when ASR completes recognition.
        
        Args:
            session_id: Session ID
            text: Recognized text
            **params: Protocol-specific parameters (e.g., is_final, confidence, is_delta)
            
        Returns:
            STT message dictionary, or None if protocol doesn't support
        """
        return None
    
    def send_llm(self, session_id: str, text: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create LLM (Agent) response message.
        
        Called when Agent generates response.
        
        Args:
            session_id: Session ID
            text: Agent response text
            **params: Protocol-specific parameters (e.g., is_delta)
            
        Returns:
            LLM message dictionary, or None if protocol doesn't support
        """
        return None
    
    def send_tts_audio(
        self, 
        session_id: str, 
        audio_data: bytes,
        **params
    ) -> Optional[Dict[str, Any]]:
        """
        Create TTS audio message.
        
        Called when TTS generates audio.
        Note: audio_data is already in encoded format (e.g., Opus).
        
        Args:
            session_id: Session ID
            audio_data: Encoded audio data
            **params: Protocol-specific parameters
            
        Returns:
            Audio message dictionary
        """
        return None
    
    def send_tts_event(
        self, 
        session_id: str, 
        event: str,  # "start", "sentence_start", "sentence_end", "stop"
        text: Optional[str] = None,
        **params
    ) -> Optional[Dict[str, Any]]:
        """
        Create TTS event message (for UI synchronization).
        
        Args:
            session_id: Session ID
            event: Event type
            text: Optional text content (for sentence_start)
            **params: Protocol-specific parameters
            
        Returns:
            TTS event message dictionary
        """
        return None
    
    def send_bot_thinking_event(
            self, 
            session_id: str, 
            **params
        ) -> Optional[Dict[str, Any]]:
            """
            Create bot thinking event message (for UI synchronization).
            
            Args:
                session_id: Session ID
                event: Event type
                **params: Protocol-specific parameters
                
            Returns:
                Bot thinking event message dictionary
            """
            return None

    def send_error(self, session_id: str, error: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create error message.
        
        Args:
            session_id: Session ID
            error: Error description
            **params: Protocol-specific parameters (e.g., error_code)
            
        Returns:
            Error message dictionary
        """
        return None
    
    def abort(self, session_id: str, reason: str = None, **params) -> Optional[Dict[str, Any]]:
        """
        Create abort message (interrupt current operation).
        
        Args:
            session_id: Session ID
            reason: Abort reason
            **params: Protocol-specific parameters
            
        Returns:
            Abort message dictionary
        """
        return None
    
    def start_listen(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create "start listening" state message.
        
        Tell client to activate microphone.
        
        Returns:
            State message dictionary, or None if protocol doesn't support
        """
        return None
    
    def stop_listen(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create "stop listening" state message.
        
        Tell client to deactivate microphone.
        
        Returns:
            State message dictionary, or None if protocol doesn't support
        """
        return None
    
    def start_speaker(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create "start speaker" state message.
        
        Tell client to prepare for audio playback.
        
        Returns:
            State message dictionary, or None if protocol doesn't support
        """
        return None
    
    def stop_speaker(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create "stop speaker" state message.
        
        Tell client to stop audio playback.
        
        Returns:
            State message dictionary, or None if protocol doesn't support
        """
        return None
    
    def get_tool_list(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """
        Create "get tool list" message.
        
        Tell client to get the list of tools.
        
        Returns:
            Tool list message dictionary, or None if protocol doesn't support
        """
        return None

    # ============ Capability query ============
    
    def supports_method(self, method_name: str) -> bool:
        """
        Check if protocol supports a business method.
        
        Args:
            method_name: Method name (e.g., "send_stt", "handshake")
            
        Returns:
            True if method is implemented (not default None return)
        """
        method = getattr(self, method_name, None)
        if method is None:
            return False
        
        # Simple check: call and see if it returns None (not perfect but good enough)
        # In practice, subclasses should explicitly declare supported methods
        return callable(method)

