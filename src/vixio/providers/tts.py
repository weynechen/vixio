"""
TTS provider interface

Enhanced features:
- Streaming text input: Some providers support streaming text chunks
- Auto-segmentation: Some providers handle sentence splitting automatically
"""

from abc import abstractmethod
from collections.abc import AsyncIterator
from typing import Dict, Any
from vixio.providers.base import BaseProvider


class TTSProvider(BaseProvider):
    """
    TTS (Text-to-Speech) provider interface.
    
    Implementations should synthesize text to audio.
    
    Operating Modes:
    - Batch mode: synthesize() - Process complete sentences
    - Streaming mode: synthesize_stream() - Process streaming text input
    """
    
    @property
    def sample_rate(self) -> int:
        """
        Output audio sample rate in Hz.
        
        Subclasses can set this via self.sample_rate = value in __init__.
        Default: 16000 Hz
        
        Returns:
            Sample rate in Hz
        """
        return getattr(self, '_sample_rate', 16000)
    
    @sample_rate.setter
    def sample_rate(self, value: int) -> None:
        """Set the output audio sample rate."""
        self._sample_rate = value
    
    @abstractmethod
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize complete text to audio (batch mode).
        
        This is the standard mode where complete sentences are provided.
        All TTS providers must implement this method.
        
        Args:
            text: Complete text to synthesize
            
        Yields:
            Audio bytes (encoded, e.g., PCM, Opus, MP3)
        """
        pass
    
    async def synthesize_stream(
        self, 
        text_stream: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """
        Synthesize streaming text to audio (streaming mode).
        
        This method is OPTIONAL. Providers that support streaming text input
        and intelligent segmentation (like qwen3-tts-flash-realtime in 
        server_commit mode) should override this method.
        
        Default implementation: buffer all text and call synthesize() in batch.
        
        Args:
            text_stream: Streaming text chunks (e.g., from LLM output)
            
        Yields:
            Audio bytes as synthesis progresses
        """
        # Default implementation: buffer and batch synthesize
        buffer = []
        async for text in text_stream:
            buffer.append(text)
        
        if buffer:
            full_text = ''.join(buffer)
            async for audio in self.synthesize(full_text):
                yield audio
    
    @property
    def supports_streaming_input(self) -> bool:
        """
        Whether provider supports streaming text input.
        
        If True, the provider can accept streaming text chunks via
        synthesize_stream() and handle segmentation automatically.
        This eliminates the need for external sentence aggregation.
        
        Returns:
            True if provider supports streaming input, False otherwise
        """
        return False
    
    @abstractmethod
    def cancel(self) -> None:
        """Cancel ongoing synthesis"""
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
