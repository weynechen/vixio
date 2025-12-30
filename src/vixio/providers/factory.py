"""
Provider factory for creating providers from configuration
"""

from typing import Dict, Any, Optional
from loguru import logger
from vixio.providers.base import BaseProvider
from vixio.providers.registry import ProviderRegistry


class ProviderFactory:
    """
    Provider factory for config-driven provider creation.
    
    Creates provider instances based on provider name,
    without needing to know if it's local or remote.
    """
    
    @staticmethod
    def create(provider_name: str, config: Dict[str, Any]) -> BaseProvider:
        """
        Create provider instance from configuration.
        
        Args:
            provider_name: Provider name (e.g., "silero-vad")
            config: Configuration dictionary
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: Provider not registered
            TypeError: Invalid configuration
            
        Example:
            provider = ProviderFactory.create("silero-vad-grpc", {
                "service_url": "localhost:50051",
                "threshold": 0.5
            })
        """
        provider_class = ProviderRegistry.get(provider_name)
        
        if not provider_class:
            available = list(ProviderRegistry.list_providers().keys())
            raise ValueError(
                f"Provider '{provider_name}' not found. "
                f"Available providers: {available}"
            )
        
        # Validate config against schema
        schema = provider_class.get_config_schema()
        _validate_config(config, schema, provider_name)
        
        # Create instance
        try:
            provider = provider_class(**config)
        except TypeError as e:
            raise TypeError(f"Invalid config for provider '{provider_name}': {e}")
        
        logger.info(
            f"Created provider: {provider_name} "
            f"(local={provider.is_local}, category={provider.category}, "
            f"stateful={provider.is_stateful})"
        )
        
        return provider
    
    @staticmethod
    def create_from_config_file(config_path: str, env: Optional[str] = None) -> Dict[str, BaseProvider]:
        """
        Create all providers from YAML config file.
        
        Supports two config formats:
        1. Single-environment: { providers: {...} }
        2. Multi-environment: { dev: { providers: {...} }, prod: {...} }
        
        Args:
            config_path: Path to YAML config file
            env: Environment name (dev/docker/k8s). If None, auto-detect format.
            
        Returns:
            Dictionary of {category: provider}
            
        Example:
            providers = ProviderFactory.create_from_config_file(
                "config/providers.yaml",
                env="dev"
            )
            vad_provider = providers['vad']
        """
        import yaml
        import os
        from pathlib import Path
        
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path, encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # Determine config format and extract providers config
        if "providers" in config:
            # Single-environment config (e.g., preset files)
            env_config = config
        elif env is not None:
            # Multi-environment config with specified env
            if env not in config:
                raise ValueError(f"Environment '{env}' not found in config. Available: {list(config.keys())}")
            env_config = config[env]
        else:
            # Multi-environment config without specified env - use first available
            available_envs = [k for k in config.keys() if k != "version"]
            if not available_envs:
                raise ValueError("No environment found in config")
            env = available_envs[0]
            env_config = config[env]
        
        if 'providers' not in env_config:
            raise ValueError(f"No 'providers' section in config")
        
        providers = {}
        for category, provider_config in env_config['providers'].items():
            provider_name = provider_config['provider']
            provider_params = provider_config.get('config', {})
            
            # Expand environment variables
            provider_params = _expand_env_vars(provider_params)
            
            try:
                provider = ProviderFactory.create(provider_name, provider_params)
                providers[category] = provider
                logger.info(f"Loaded {category} provider: {provider_name}")
            except Exception as e:
                logger.error(f"Failed to create {category} provider '{provider_name}': {e}")
                raise
        
        return providers


def _validate_config(config: Dict, schema: Dict, provider_name: str) -> None:
    """
    Validate configuration against schema.
    
    Args:
        config: Configuration dictionary
        schema: Schema dictionary
        provider_name: Provider name for error messages
        
    Raises:
        ValueError: Missing required config
        TypeError: Wrong type for config value
    """
    for key, spec in schema.items():
        # Check required fields
        if spec.get('required', False) and key not in config:
            raise ValueError(
                f"Provider '{provider_name}' missing required config: {key}"
            )
        
        # Type checking (basic)
        if key in config:
            value = config[key]
            expected_type = spec.get('type')
            
            if expected_type == 'string' and not isinstance(value, str):
                raise TypeError(
                    f"Provider '{provider_name}' config '{key}' must be string, got {type(value)}"
                )
            elif expected_type == 'float' and not isinstance(value, (int, float)):
                raise TypeError(
                    f"Provider '{provider_name}' config '{key}' must be number, got {type(value)}"
                )
            elif expected_type == 'int' and not isinstance(value, int):
                raise TypeError(
                    f"Provider '{provider_name}' config '{key}' must be integer, got {type(value)}"
                )
            elif expected_type == 'bool' and not isinstance(value, bool):
                raise TypeError(
                    f"Provider '{provider_name}' config '{key}' must be boolean, got {type(value)}"
                )


def _expand_env_vars(config: Dict) -> Dict:
    """
    Expand environment variables in config values.
    
    Supports:
    - ${VAR_NAME} - use environment variable (error if not set)
    - ${VAR_NAME:default} - use default if not set
    - ${VAR_NAME:} - use empty string if not set (no warning)
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Config with expanded environment variables
    """
    import os
    import re
    
    expanded = {}
    # Pattern matches ${VAR} or ${VAR:default}
    env_var_pattern = re.compile(r'\$\{([^:}]+)(?::([^}]*))?\}')
    
    for key, value in config.items():
        if isinstance(value, str):
            # Replace ${VAR} or ${VAR:default} with environment variable
            def replace_env(match):
                var_name = match.group(1)
                default_value = match.group(2)  # None if no default specified
                
                env_value = os.getenv(var_name)
                
                if env_value is not None:
                    # Environment variable is set
                    return env_value
                elif default_value is not None:
                    # Use default value (can be empty string)
                    if default_value:
                        logger.debug(f"Environment variable {var_name} not set, using default: {default_value}")
                    return default_value
                else:
                    # No default, variable must be set
                    logger.error(f"Required environment variable {var_name} not set!")
                    raise ValueError(f"Required environment variable {var_name} not set")
            
            expanded[key] = env_var_pattern.sub(replace_env, value)
        elif isinstance(value, dict):
            expanded[key] = _expand_env_vars(value)
        else:
            expanded[key] = value
    
    return expanded

