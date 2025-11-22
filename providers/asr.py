"""
ASR provider interface
"""

from abc import abstractmethod
from typing import List, Dict, Any
from providers.base import BaseProvider


class ASRProvider(BaseProvider):
    """
    ASR (Automatic Speech Recognition) provider interface.
    
    Implementations should transcribe audio to text.
    """
    
    @abstractmethod
    async def transcribe(self, audio_chunks: List[bytes]) -> str:
        """
        Transcribe audio chunks to text.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            
        Returns:
            Transcribed text
        """
        pass
    
    @abstractmethod
    def reset(self) -> None:
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
