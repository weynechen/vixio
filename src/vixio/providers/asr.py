"""
ASR provider interface

Streaming ASR Provider - all implementations must provide streaming output,
even if the underlying engine is batch-based (pseudo-streaming).

Enhanced features:
- Context enhancement: Improve recognition with domain-specific text
- Built-in VAD: Some providers have integrated voice activity detection
"""

from abc import abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from vixio.providers.base import BaseProvider


@dataclass
class ASRStreamResult:
    """
    Structured result from streaming ASR with built-in VAD.
    
    This allows ASR providers to return both text and VAD events
    through the same streaming interface, enabling proper latency
    monitoring without provider-specific handling.
    
    Attributes:
        text: Transcribed text (if available)
        event: VAD event type ("speech_started", "speech_stopped", None)
        timestamp: Event timestamp (seconds since epoch)
    """
    text: Optional[str] = None
    event: Optional[str] = None  # "speech_started", "speech_stopped"
    timestamp: Optional[float] = None


class ASRProvider(BaseProvider):
    """
    ASR (Automatic Speech Recognition) provider interface.
    
    All implementations must provide streaming output via transcribe_stream().
    Even batch-based engines should wrap their output as pseudo-streaming.
    
    Streaming Contract:
    - transcribe_stream() yields TEXT_DELTA chunks as recognition progresses
    - For batch engines: yield single result after processing
    - For streaming engines: yield intermediate + final results
    
    Enhanced Features:
    - Context support: Pass domain-specific text to improve accuracy
    - VAD support: Some providers have built-in voice activity detection
    """
    
    @abstractmethod
    def transcribe_stream(
        self, 
        audio_chunks: List[bytes],
        context: Optional[str] = None
    ) -> AsyncIterator[str]:
        """
        Transcribe audio chunks to text (streaming output).
        
        All implementations must provide this method. For batch-based engines,
        wrap the single result as a one-item async iterator.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            context: Optional text context to improve recognition accuracy.
                     Examples:
                     - Domain terms: "Bulge Bracket, Boutique, Middle Market"
                     - Previous conversation: "discussing investment banking..."
                     - Custom vocabulary: "product names, person names"
                     Only used if supports_context is True.
            
        Yields:
            Text segments as recognition progresses
            - Batch engines: yield single final result
            - Streaming engines: yield intermediate results + final
        """
        pass
    
    @property
    def supports_vad(self) -> bool:
        """
        Whether provider has built-in VAD (Voice Activity Detection) support.
        
        If True, the provider can handle turn detection internally,
        eliminating the need for external VAD services.
        
        Returns:
            True if provider has built-in VAD, False otherwise
        """
        return False
    
    @property
    def supports_context(self) -> bool:
        """
        Whether provider supports text context enhancement.
        
        If True, the provider can use context parameter in transcribe_stream()
        to improve recognition accuracy for domain-specific terms.
        
        Returns:
            True if provider supports context, False otherwise
        """
        return False
    
    @property
    def supports_streaming_input(self) -> bool:
        """
        Whether provider supports continuous audio streaming input.
        
        If True, the provider can be used with StreamingASRStation,
        receiving audio via append_audio_continuous() instead of
        batch transcribe_stream().
        
        Returns:
            True if provider supports streaming input, False otherwise
        """
        return False
    
    async def append_audio_continuous(
        self,
        audio_data: bytes
    ) -> AsyncIterator[ASRStreamResult]:
        """
        Append audio to continuous streaming ASR session.
        
        This method is used by StreamingASRStation for providers with
        built-in VAD. It allows continuous audio input and returns
        structured results including both text and VAD events.
        
        Args:
            audio_data: Audio bytes (PCM, 16kHz, mono, 16-bit)
            
        Yields:
            ASRStreamResult with text and/or VAD events
            - text: Transcribed text as it becomes available
            - event: "speech_started" or "speech_stopped" for VAD events
            - timestamp: Event timestamp for latency monitoring
            
        Raises:
            NotImplementedError: If provider doesn't support streaming input
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support streaming input. "
            "Override append_audio_continuous() to enable."
        )
        yield  # Make this an async generator
    
    def is_speech_ended(self) -> bool:
        """
        Check if ASR has detected end of speech.
        
        Used by StreamingASRStation to determine when to emit completion.
        
        Returns:
            True if speech has ended, False otherwise
        """
        return False
    
    async def stop_streaming(self) -> None:
        """
        Stop streaming ASR session.
        
        Called by StreamingASRStation on interrupt or cleanup.
        """
        pass
    
    @abstractmethod
    async def reset(self) -> None:
        """Reset internal state"""
        pass
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get provider configuration.
        
        Returns:
            Configuration dictionary
        """
        return {
            "provider": self.__class__.__name__,
            "name": self.name,
        }
