"""
Base sentence aggregator provider interface
"""

from abc import abstractmethod
from typing import List, Dict, Any
from vixio.providers.base import BaseProvider


class SentenceAggregatorProvider(BaseProvider):
    """
    Base provider for sentence aggregation.
    
    Implementations should aggregate streaming text deltas into complete sentences.
    Different providers can use different strategies:
    - Rule-based (punctuation, conjunctions, etc.)
    - Semantic-based (jieba, POS tagging, etc.)
    - Model-based (LLM, transformer, etc.)
    """
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "sentence_aggregator"
    
    @property
    def is_local(self) -> bool:
        """Sentence aggregation is always local (no API calls)"""
        return True
    
    @property
    def is_stateful(self) -> bool:
        """Sentence aggregation is stateful (maintains buffer)"""
        return True
    
    @abstractmethod
    def add_chunk(self, chunk: str) -> List[str]:
        """
        Add text chunk and extract complete sentences.
        
        Args:
            chunk: New text chunk (delta) from streaming
            
        Returns:
            List of complete sentences (may be empty if no complete sentence yet)
        """
        pass
    
    @abstractmethod
    def flush(self) -> str:
        """
        Flush remaining buffer as final sentence.
        
        Called when stream completes to get any remaining text.
        
        Returns:
            Remaining text as final sentence (empty string if buffer is empty)
        """
        pass
    
    @abstractmethod
    def reset(self) -> None:
        """
        Reset internal state for new conversation.
        
        Called when starting a new conversation or on state reset signal.
        """
        pass
    
    async def initialize(self) -> None:
        """
        Initialize the provider.
        
        Default implementation does nothing.
        Override if you need to load resources (models, dictionaries, etc.)
        """
        self.logger.info(f"Initialized {self.name}")
    
    async def cleanup(self) -> None:
        """
        Cleanup provider resources.
        
        Default implementation does nothing.
        Override if you need to release resources.
        """
        self.logger.info(f"Cleaned up {self.name}")
