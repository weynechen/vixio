"""
Base provider class
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from loguru import logger


class BaseProvider(ABC):
    """
    Base provider class for all service providers.
    
    All providers must inherit from this class and implement required properties/methods.
    
    Fixed properties (defined by subclass):
    - is_local: Whether this is a self-hosted service (True) or cloud API (False)
    - is_stateful: Whether this requires sequential processing within cycles
    - category: Provider category (vad/asr/agent/tts)
    """
    
    def __init__(self, name: Optional[str] = None):
        """
        Initialize provider.
        
        Args:
            name: Provider name for logging
        """
        self.name = name or self.__class__.__name__
        self.logger = logger.bind(component=self.name)
    
    @property
    @abstractmethod
    def is_local(self) -> bool:
        """
        Whether this is a local (self-hosted) service.
        
        Returns:
            True: Local service (gRPC), we manage deployment
            False: Remote service (HTTP API), cloud provider manages
        """
        pass
    
    @property
    @abstractmethod
    def is_stateful(self) -> bool:
        """
        Whether this provider requires sequential processing.
        
        Returns:
            True: Stateful (e.g., VAD requires START→END lock)
            False: Stateless (e.g., ASR can process chunks independently)
        """
        pass
    
    @property
    @abstractmethod
    def category(self) -> str:
        """
        Provider category.
        
        Returns:
            One of: "vad", "asr", "agent", "tts"
        """
        pass
    
    @classmethod
    @abstractmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """
        Return configuration schema for this provider.
        
        Used for validation and documentation generation.
        This is a class method because schema is class-level information.
        
        Returns:
            Dictionary describing required/optional config parameters
            
        Example:
            {
                "service_url": {
                    "type": "string",
                    "required": True,
                    "description": "gRPC service URL"
                },
                "threshold": {
                    "type": "float",
                    "default": 0.5,
                    "description": "Detection threshold"
                }
            }
        """
        pass
    
    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the provider.
        
        Called once when provider is created.
        Use this to connect to services, create sessions, etc.
        """
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """
        Cleanup provider resources.
        
        Called when provider is no longer needed.
        Use this to disconnect, destroy sessions, etc.
        """
        pass
    
    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"
    
    def __repr__(self) -> str:
        return self.__str__()
