"""
Provider registry for plugin system
"""

from typing import Dict, Type, Optional
from loguru import logger
from vixio.providers.base import BaseProvider


class ProviderRegistry:
    """
    Provider registry for plugin system.
    
    Supports:
    - Built-in provider auto-registration
    - Developer custom provider registration
    - Provider lookup by name
    """
    
    _providers: Dict[str, Type[BaseProvider]] = {}
    
    @classmethod
    def register(cls, name: str, provider_class: Type[BaseProvider]):
        """
        Register a provider.
        
        Args:
            name: Provider unique identifier (e.g., "silero-vad")
            provider_class: Provider class
            
        Raises:
            ValueError: If provider name already registered
            TypeError: If provider_class doesn't inherit BaseProvider
        """
        if name in cls._providers:
            raise ValueError(f"Provider '{name}' already registered")
        
        if not issubclass(provider_class, BaseProvider):
            raise TypeError(f"{provider_class} must inherit from BaseProvider")
        
        cls._providers[name] = provider_class
        logger.info(f"Registered provider: {name} ({provider_class.__name__})")
    
    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseProvider]]:
        """
        Get provider class by name.
        
        Args:
            name: Provider name
            
        Returns:
            Provider class or None if not found
        """
        return cls._providers.get(name)
    
    @classmethod
    def list_providers(cls, category: Optional[str] = None) -> Dict[str, Type[BaseProvider]]:
        """
        List all registered providers.
        
        Args:
            category: Filter by category (vad/asr/agent/tts), None for all
            
        Returns:
            Dictionary of {name: provider_class}
        """
        if category:
            # Filter by category - need to instantiate temporarily to check
            return {
                name: prov_cls
                for name, prov_cls in cls._providers.items()
                # This is a bit hacky but avoids full instantiation
                if hasattr(prov_cls, 'category') and 
                   (prov_cls.category.fget(None) == category if hasattr(prov_cls.category, 'fget') else False)
            }
        return cls._providers.copy()
    
    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if provider is registered"""
        return name in cls._providers


def register_provider(name: str):
    """
    Decorator for registering providers.
    
    Naming convention:
        - `-local`: In-process inference (e.g., silero-vad-local)
        - `-grpc`: Connect to gRPC service (e.g., silero-vad-grpc)
        - `-remote`: Connect to remote cloud API (e.g., edge-tts-remote)
    
    Usage:
        @register_provider("silero-vad-grpc")
        class LocalSileroVADProvider(VADProvider):
            ...
    
    The registered name will also be used as the provider's instance name.
    
    Args:
        name: Provider unique identifier (used in config files and as instance name)
    """
    def decorator(provider_class: Type[BaseProvider]):
        ProviderRegistry.register(name, provider_class)
        # Store registered name on class for auto-naming
        provider_class._registered_name = name
        return provider_class
    return decorator

