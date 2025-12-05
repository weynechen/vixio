"""
Composite vision describer

Combines multiple describers into one.
"""

from typing import List
from loguru import logger
from vixio.providers.vision import ImageContent, VisionDescriber


class CompositeDescriber(VisionDescriber):
    """
    Composite describer that combines multiple describers.
    
    Runs all describers and concatenates their results.
    Useful for combining VLM + YOLO + OCR, etc.
    """
    
    def __init__(
        self, 
        describers: List[VisionDescriber],
        separator: str = " | "
    ):
        """
        Initialize composite describer.
        
        Args:
            describers: List of describers to combine
            separator: Separator between descriptions
        """
        self.describers = describers
        self.separator = separator
        self.logger = logger.bind(component="CompositeDescriber")
    
    async def describe(
        self, 
        image: ImageContent, 
        query: str
    ) -> str:
        """
        Describe image using all describers.
        
        Args:
            image: Image content
            query: User's question
            
        Returns:
            Combined description from all describers
        """
        results = []
        
        for describer in self.describers:
            try:
                desc = await describer.describe(image, query)
                if desc:
                    results.append(desc)
            except Exception as e:
                self.logger.warning(f"Describer {describer.__class__.__name__} failed: {e}")
                continue
        
        if not results:
            return "[No description available]"
        
        return self.separator.join(results)

