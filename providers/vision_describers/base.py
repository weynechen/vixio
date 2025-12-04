"""
Vision describer base class
"""

from abc import ABC, abstractmethod
from providers.vision import ImageContent


class VisionDescriber(ABC):
    """
    Vision describer base class.
    
    Converts images to text descriptions for single-modal strategy.
    
    Key design: describe() needs user's query to provide relevant descriptions.
    """
    
    @abstractmethod
    async def describe(
        self, 
        image: ImageContent, 
        query: str
    ) -> str:
        """
        Describe image based on user's query.
        
        Args:
            image: Image content
            query: User's question (guides description focus)
            
        Returns:
            Targeted image description
        """
        pass

