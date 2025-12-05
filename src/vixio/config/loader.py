"""
Configuration Loader for Vixio

Loads configuration from multiple sources with priority:
1. Code configuration (highest)
2. Environment variables (.env + VIXIO_XXX)
3. YAML config file
4. Default values (lowest)
"""

from pathlib import Path
from typing import Optional, Union
from pydantic_settings import BaseSettings
from ruamel.yaml import YAML
from loguru import logger

from vixio.config.schema import VixioConfig


class VixioSettings(BaseSettings):
    """
    Environment-based settings with VIXIO_ prefix.
    
    Reads from:
    1. Environment variables (VIXIO_XXX)
    2. .env file in current directory
    
    Example:
        VIXIO_LOG_LEVEL=DEBUG
        VIXIO_VAD_SERVICE_URL=localhost:50051
    """
    
    # Config file path
    config_file: Optional[str] = "config.yaml"
    
    # Log settings
    log_level: str = "INFO"
    log_file: Optional[str] = None
    
    # Service endpoints
    vad_service_url: str = "localhost:50051"
    asr_service_url: str = "localhost:50052"
    tts_service_url: str = "localhost:50053"
    
    # Default providers
    default_vad: str = "silero-vad"
    default_asr: str = "sherpa-asr"
    default_tts: str = "edge-tts"
    default_agent: str = "openai-agent"
    
    # Provider modes
    vad_mode: str = "auto"
    asr_mode: str = "auto"
    tts_mode: str = "grpc"
    
    class Config:
        env_prefix = "VIXIO_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


def load_config(
    config_path: Optional[Union[str, Path]] = None,
    env_settings: Optional[VixioSettings] = None,
) -> VixioConfig:
    """
    Load Vixio configuration.
    
    Configuration sources (priority high to low):
    1. Environment variables (.env + VIXIO_XXX)
    2. YAML config file (config.yaml)
    3. Default values
    
    Args:
        config_path: Path to YAML config file. If None, uses VIXIO_CONFIG_FILE
                     env var or defaults to "config.yaml"
        env_settings: Pre-loaded environment settings
        
    Returns:
        VixioConfig instance
    """
    # Step 1: Load environment variables (.env file auto-loaded)
    if env_settings is None:
        env_settings = VixioSettings()
    
    # Step 2: Determine config file path
    if config_path is None:
        config_path = env_settings.config_file
    
    # Step 3: Initialize with defaults
    config_data = {}
    
    # Step 4: Load YAML config file (if exists)
    if config_path:
        yaml = YAML()
        path = Path(config_path)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.load(f)
                    if yaml_config:
                        config_data = yaml_config
                        logger.debug(f"Loaded config from {path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {path}: {e}")
    
    # Step 5: Create config object
    config = VixioConfig(**config_data)
    
    # Step 6: Apply environment variable overrides
    config.log_level = env_settings.log_level
    if env_settings.log_file:
        config.log_file = env_settings.log_file
    
    # Override VAD settings
    config.vad.service_url = env_settings.vad_service_url
    config.vad.mode = env_settings.vad_mode
    
    # Override ASR settings
    config.asr.service_url = env_settings.asr_service_url
    config.asr.mode = env_settings.asr_mode
    
    # Override TTS settings (if using gRPC TTS)
    # config.tts is for edge-tts by default, no service_url needed
    
    return config


# ============================================================
# Global config management
# ============================================================

_global_config: Optional[VixioConfig] = None


def get_config() -> VixioConfig:
    """
    Get global config (lazy load).
    
    Auto-discovers config from:
    1. .env file in current directory
    2. config.yaml in current directory (or VIXIO_CONFIG_FILE)
    """
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config


def set_config(config: VixioConfig) -> None:
    """Set global config manually"""
    global _global_config
    _global_config = config


def reload_config() -> VixioConfig:
    """Force reload config from files"""
    global _global_config
    _global_config = load_config()
    return _global_config
