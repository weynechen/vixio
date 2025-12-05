"""
VAD provider interface
"""

from abc import abstractmethod
from typing import Dict, Any
from enum import Enum
from vixio.providers.base import BaseProvider


class VADEvent(Enum):
    """VAD event types for state management"""
    START = "start"  # Begin voice activity detection cycle
    CHUNK = "chunk"  # Continue detection (default)
    END = "end"      # End voice activity detection cycle


class VADProvider(BaseProvider):
    """
    VAD (Voice Activity Detection) provider interface.
    
    Implementations should detect voice activity in audio chunks.
    """
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "vad"
    
    @property
    def is_stateful(self) -> bool:
        """VAD is stateful - requires sequential processing within VAD cycles"""
        return True
    
    @abstractmethod
    async def detect(self, audio_data: bytes, event: VADEvent = VADEvent.CHUNK) -> bool:
        """
        Detect voice activity in audio data.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            event: VAD event type (START/CHUNK/END) for lock management
            
        Returns:
            True if voice detected, False otherwise
            
        Notes:
            - START: Acquire lock, begin new VAD cycle
            - CHUNK: Continue detection (lock already held)
            - END: Release lock, end VAD cycle
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
            "category": self.category,
            "is_stateful": self.is_stateful,
        }
