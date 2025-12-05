"""
Configuration management for Vixio framework

Usage:
    from vixio.config import load_config, get_config, VixioConfig
    
    # Load from file
    config = load_config("config.yaml")
    
    # Get global config (auto-loads from .env and config.yaml)
    config = get_config()
    
    # Access settings
    print(config.vad.threshold)
    print(config.tts.voice)
"""

from pathlib import Path

from vixio.config.schema import (
    VixioConfig,
    VADConfig,
    ASRConfig,
    TTSConfig,
    AgentConfig,
    ProviderConfig,
)
from vixio.config.loader import (
    load_config,
    get_config,
    set_config,
    reload_config,
    VixioSettings,
)


def get_default_config_path() -> str:
    """
    Get the default providers.yaml configuration file path.
    
    Search order:
    1. Current working directory: ./config/providers.yaml
    2. Project root (if in development): {project}/config/providers.yaml
    3. Package bundled config (fallback): vixio/config/providers.yaml
    
    This allows users to customize the config by placing it in their project,
    while providing a default config for quick start.
    
    Returns:
        str: Absolute path to the default providers.yaml
    """
    import os
    
    # 1. Check current working directory
    cwd_config = Path(os.getcwd()) / "config" / "providers.yaml"
    if cwd_config.exists():
        return str(cwd_config)
    
    # 2. Check if we're in a development setup (look for pyproject.toml)
    # Walk up from cwd to find project root
    current = Path(os.getcwd())
    for _ in range(5):  # Max 5 levels up
        pyproject = current / "pyproject.toml"
        if pyproject.exists():
            project_config = current / "config" / "providers.yaml"
            if project_config.exists():
                return str(project_config)
        current = current.parent
    
    # 3. Fallback to package bundled config
    package_config = Path(__file__).parent / "providers.yaml"
    if package_config.exists():
        return str(package_config)
    
    # No config found - return default path (will error at load time)
    return str(Path(os.getcwd()) / "config" / "providers.yaml")


__all__ = [
    # Schema
    "VixioConfig",
    "VADConfig",
    "ASRConfig",
    "TTSConfig",
    "AgentConfig",
    "ProviderConfig",
    # Loader
    "load_config",
    "get_config",
    "set_config",
    "reload_config",
    "VixioSettings",
    # Utilities
    "get_default_config_path",
]
