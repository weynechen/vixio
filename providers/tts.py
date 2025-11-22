"""
TTS provider interface
"""

from abc import abstractmethod
from typing import AsyncIterator, Dict, Any
from vixio.providers.base import BaseProvider


class TTSProvider(BaseProvider):
    """
    TTS (Text-to-Speech) provider interface.
    
    Implementations should synthesize text to audio.
    """
    
    @abstractmethod
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize text to audio (streaming).
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio bytes (encoded, e.g., PCM, Opus, MP3)
        """
        pass
    
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
