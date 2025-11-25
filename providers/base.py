"""
Base provider class
"""

from abc import ABC
from loguru import logger


class BaseProvider(ABC):
    """
    Base provider class for all service providers.
    
    Provides common functionality like logging.
    """
    
    def __init__(self, name: str = None):
        """
        Initialize provider.
        
        Args:
            name: Provider name for logging
        """
        self.name = name or self.__class__.__name__
        self.logger = logger.bind(provider=self.name)
    
    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"
    
    def __repr__(self) -> str:
        return self.__str__()
