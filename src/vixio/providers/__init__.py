"""
Provider interfaces and implementations

This package contains:
1. Provider interfaces (base.py, vad.py, asr.py, tts.py, agent.py, vision.py)
2. Provider implementations with lazy loading for optional dependencies
3. Provider registry and factory for plugin system

Usage:
    # Interfaces are always available
    from vixio.providers import VADProvider, TTSProvider, AgentProvider

    # Implementations require optional dependencies
    # Use lazy loaders or try/except for optional providers:
    
    # Method 1: Lazy loader functions
    EdgeTTSProvider = get_edge_tts_provider()
    
    # Method 2: Direct import (if optional deps installed)
    from vixio.providers import EdgeTTSProvider  # may raise ImportError
"""

from typing import TYPE_CHECKING

# ============================================================
# Provider Interfaces (always available)
# ============================================================
from vixio.providers.base import BaseProvider
from vixio.providers.vad import VADProvider, VADEvent
from vixio.providers.asr import ASRProvider
from vixio.providers.tts import TTSProvider
from vixio.providers.agent import AgentProvider, Tool
from vixio.providers.vision import VisionDescriber

# Provider registry and factory (always available)
from vixio.providers.registry import ProviderRegistry, register_provider
from vixio.providers.factory import ProviderFactory


# ============================================================
# Lazy Loader Functions (for optional dependencies)
# ============================================================

def get_edge_tts_provider():
    """
    Load EdgeTTSProvider class.
    
    Requires: pip install vixio[edge-tts]
    
    Returns:
        EdgeTTSProvider class
        
    Raises:
        ImportError: If edge-tts package is not installed
    """
    try:
        from vixio.providers.edge_tts.provider import EdgeTTSProvider
        return EdgeTTSProvider
    except ImportError as e:
        raise ImportError(
            "EdgeTTSProvider requires edge-tts package. "
            "Install with: pip install vixio[edge-tts]"
        ) from e


def get_openai_agent_provider():
    """
    Load OpenAIAgentProvider class.
    
    Requires: pip install vixio[openai-agent]
    
    Returns:
        OpenAIAgentProvider class
        
    Raises:
        ImportError: If openai-agents package is not installed
    """
    try:
        from vixio.providers.openai_agent.provider import OpenAIAgentProvider
        return OpenAIAgentProvider
    except ImportError as e:
        raise ImportError(
            "OpenAIAgentProvider requires openai-agents package. "
            "Install with: pip install vixio[openai-agent]"
        ) from e


def get_silero_vad_provider():
    """
    Load SileroVADProvider class.
    
    This provider supports dual-mode operation:
    - local: In-process inference (requires: pip install vixio[silero-vad-local])
    - grpc: Connect to gRPC service (no extra deps, but requires running service)
    - auto: Try gRPC first, fallback to local
    
    Returns:
        SileroVADProvider class
    """
    from vixio.providers.silero_vad.provider import SileroVADProvider
    return SileroVADProvider


def get_sherpa_asr_provider():
    """
    Load SherpaASRProvider class.
    
    This provider supports dual-mode operation:
    - local: In-process inference (requires: pip install vixio[sherpa-asr-local])
    - grpc: Connect to gRPC service (no extra deps, but requires running service)
    - auto: Try gRPC first, fallback to local
    
    Returns:
        SherpaASRProvider class
    """
    from vixio.providers.sherpa_onnx_local.provider import SherpaASRProvider
    return SherpaASRProvider


def get_kokoro_tts_provider():
    """
    Load KokoroTTSProvider class.
    
    This provider supports dual-mode operation:
    - local: In-process inference (requires: pip install vixio[kokoro-tts-local])
    - grpc: Connect to gRPC service (no extra deps, but requires running service)
    - auto: Try gRPC first, fallback to local
    
    Returns:
        KokoroTTSProvider class
    """
    from vixio.providers.kokoro_cn_tts_local.provider import KokoroTTSProvider
    return KokoroTTSProvider


def get_vision_describer():
    """
    Load OpenAICompatibleVLM class.
    
    Requires: pip install vixio[openai-agent]
    
    Returns:
        OpenAICompatibleVLM class
    """
    try:
        from vixio.providers.vision_describers.openai_compatible import OpenAICompatibleVLM
        return OpenAICompatibleVLM
    except ImportError as e:
        raise ImportError(
            "OpenAICompatibleVLM requires openai package. "
            "Install with: pip install vixio[openai-agent]"
        ) from e


# ============================================================
# Export list
# ============================================================

__all__ = [
    # Interfaces (always available)
    "BaseProvider",
    "VADProvider",
    "VADEvent",
    "ASRProvider",
    "TTSProvider",
    "AgentProvider",
    "Tool",
    "VisionDescriber",
    # Registry and Factory
    "ProviderRegistry",
    "register_provider",
    "ProviderFactory",
    # Lazy loaders
    "get_edge_tts_provider",
    "get_openai_agent_provider",
    "get_silero_vad_provider",
    "get_sherpa_asr_provider",
    "get_kokoro_tts_provider",
    "get_vision_describer",
]


# ============================================================
# Optional: Direct exports for convenience (if deps installed)
# These are wrapped in try/except so importing vixio.providers
# doesn't fail if optional deps are missing
# ============================================================

# Edge TTS (requires edge-tts)
try:
    from vixio.providers.edge_tts.provider import EdgeTTSProvider
    __all__.append("EdgeTTSProvider")
except ImportError:
    pass

# OpenAI Agent (requires openai-agents)
try:
    from vixio.providers.openai_agent.provider import OpenAIAgentProvider
    __all__.append("OpenAIAgentProvider")
except ImportError:
    pass

# Vision describers (requires openai)
try:
    from vixio.providers.vision_describers.openai_compatible import OpenAICompatibleVLM
    __all__.append("OpenAICompatibleVLM")
except ImportError:
    pass

# Local inference providers (gRPC clients - always available)
# These are renamed to follow the dual-mode pattern
try:
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
    __all__.append("LocalSileroVADProvider")
except ImportError:
    pass

try:
    from vixio.providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
    __all__.append("LocalSherpaASRProvider")
except ImportError:
    pass

try:
    from vixio.providers.kokoro_cn_tts_local.grpc_provider import LocalKokoroTTSProvider
    __all__.append("LocalKokoroTTSProvider")
except ImportError:
    pass

# In-process local inference providers (requires heavy dependencies)
# These run inference directly in the current process without gRPC
try:
    from vixio.providers.silero_vad.local_provider import LocalSileroVADInProcessProvider
    __all__.append("LocalSileroVADInProcessProvider")
except ImportError:
    pass

try:
    from vixio.providers.sherpa_onnx_local.local_provider import LocalSherpaASRInProcessProvider
    __all__.append("LocalSherpaASRInProcessProvider")
except ImportError:
    pass

try:
    from vixio.providers.kokoro_cn_tts_local.local_provider import LocalKokoroTTSInProcessProvider
    __all__.append("LocalKokoroTTSInProcessProvider")
except ImportError:
    pass
