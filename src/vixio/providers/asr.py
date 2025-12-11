"""
ASR provider interface

Streaming ASR Provider - all implementations must provide streaming output,
even if the underlying engine is batch-based (pseudo-streaming).
"""

from abc import abstractmethod
from typing import List, Dict, Any, AsyncIterator
from vixio.providers.base import BaseProvider


class ASRProvider(BaseProvider):
    """
    ASR (Automatic Speech Recognition) provider interface.
    
    All implementations must provide streaming output via transcribe_stream().
    Even batch-based engines should wrap their output as pseudo-streaming.
    
    Streaming Contract:
    - transcribe_stream() yields TEXT_DELTA chunks as recognition progresses
    - For batch engines: yield single result after processing
    - For streaming engines: yield intermediate + final results
    """
    
    @abstractmethod
    def transcribe_stream(self, audio_chunks: List[bytes]) -> AsyncIterator[str]:
        """
        Transcribe audio chunks to text (streaming output).
        
        All implementations must provide this method. For batch-based engines,
        wrap the single result as a one-item async iterator.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            
        Yields:
            Text segments as recognition progresses
            - Batch engines: yield single final result
            - Streaming engines: yield intermediate results + final
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
