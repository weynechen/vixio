"""
Xiaozhi preset configurations for quick start.

Available presets:
- qwen: Pipeline mode (VAD + ASR + LLM + TTS)
- qwen-realtime: End-to-end realtime mode

Each preset is a complete YAML configuration file that can be used
directly with `vixio run` command or copied as a starting point for
customization.
"""

from pathlib import Path

PRESET_DIR = Path(__file__).parent

AVAILABLE_PRESETS = ["qwen", "qwen-realtime"]


def get_preset_path(preset_name: str) -> Path:
    """
    Get preset config file path.
    
    Args:
        preset_name: Name of the preset (qwen or qwen-realtime)
        
    Returns:
        Path to the preset YAML file
        
    Raises:
        ValueError: If preset_name is not valid
        FileNotFoundError: If preset file doesn't exist
    """
    if preset_name not in AVAILABLE_PRESETS:
        raise ValueError(
            f"Unknown preset: {preset_name}. "
            f"Available presets: {', '.join(AVAILABLE_PRESETS)}"
        )
    
    preset_path = PRESET_DIR / f"{preset_name}.yaml"
    if not preset_path.exists():
        raise FileNotFoundError(f"Preset file not found: {preset_path}")
    
    return preset_path


def list_presets() -> list[str]:
    """
    List all available presets.
    
    Returns:
        List of preset names
    """
    return AVAILABLE_PRESETS.copy()
