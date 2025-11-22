"""
VAD provider interface
"""

from abc import abstractmethod
from typing import Dict, Any
from vixio.providers.base import BaseProvider


class VADProvider(BaseProvider):
    """
    VAD (Voice Activity Detection) provider interface.
    
    Implementations should detect voice activity in audio chunks.
    """
    
    @abstractmethod
    def detect(self, audio_data: bytes) -> bool:
        """
        Detect voice activity in audio data.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            
        Returns:
            True if voice detected, False otherwise
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
