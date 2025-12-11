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


def get_silero_vad_local_provider():
    """
    Load LocalSileroVADInProcessProvider class (silero-vad-local).
    
    In-process inference, runs VAD directly in the current process.
    Requires: pip install vixio[silero-vad-local]
    
    Returns:
        LocalSileroVADInProcessProvider class
    """
    from vixio.providers.silero_vad.local_provider import LocalSileroVADInProcessProvider
    return LocalSileroVADInProcessProvider


def get_silero_vad_grpc_provider():
    """
    Load LocalSileroVADProvider class (silero-vad-grpc).
    
    Connects to external gRPC VAD service.
    Requires: running VAD gRPC service
    
    Returns:
        LocalSileroVADProvider class
    """
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
    return LocalSileroVADProvider


def get_sherpa_asr_local_provider():
    """
    Load LocalSherpaASRInProcessProvider class (sherpa-onnx-asr-local).
    
    In-process inference, runs ASR directly in the current process.
    Requires: pip install vixio[sherpa-onnx-asr-local]
    
    Returns:
        LocalSherpaASRInProcessProvider class
    """
    from vixio.providers.sherpa_onnx_local.local_provider import LocalSherpaASRInProcessProvider
    return LocalSherpaASRInProcessProvider


def get_sherpa_asr_grpc_provider():
    """
    Load LocalSherpaASRProvider class (sherpa-onnx-asr-grpc).
    
    Connects to external gRPC ASR service.
    Requires: running ASR gRPC service
    
    Returns:
        LocalSherpaASRProvider class
    """
    from vixio.providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
    return LocalSherpaASRProvider


def get_kokoro_tts_local_provider():
    """
    Load LocalKokoroTTSInProcessProvider class (kokoro-tts-local).
    
    In-process inference, runs TTS directly in the current process.
    Requires: pip install vixio[kokoro-cn-tts-local]
    
    Returns:
        LocalKokoroTTSInProcessProvider class
    """
    from vixio.providers.kokoro_cn_tts_local.local_provider import LocalKokoroTTSInProcessProvider
    return LocalKokoroTTSInProcessProvider


def get_kokoro_tts_grpc_provider():
    """
    Load LocalKokoroTTSProvider class (kokoro-tts-grpc).
    
    Connects to external gRPC TTS service.
    Requires: running TTS gRPC service
    
    Returns:
        LocalKokoroTTSProvider class
    """
    from vixio.providers.kokoro_cn_tts_local.grpc_provider import LocalKokoroTTSProvider
    return LocalKokoroTTSProvider


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


def get_qwen_asr_realtime_provider():
    """
    Load QwenASRRealtimeProvider class (qwen-asr-realtime).
    
    Real-time ASR using Alibaba Cloud's Qwen3-ASR-Flash model.
    Requires: pip install vixio[dev-qwen]
    
    Returns:
        QwenASRRealtimeProvider class
    """
    try:
        from vixio.providers.qwen.qwen3_asr_flash_realtime import QwenASRRealtimeProvider
        return QwenASRRealtimeProvider
    except ImportError as e:
        raise ImportError(
            "QwenASRRealtimeProvider requires dashscope package. "
            "Install with: pip install vixio[dev-qwen]"
        ) from e


def get_qwen_tts_realtime_provider():
    """
    Load QwenTTSRealtimeProvider class (qwen-tts-realtime).
    
    Real-time TTS using Alibaba Cloud's Qwen3-TTS-Flash model.
    Requires: pip install vixio[dev-qwen]
    
    Returns:
        QwenTTSRealtimeProvider class
    """
    try:
        from vixio.providers.qwen.qwen3_tts_flash_realtime import QwenTTSRealtimeProvider
        return QwenTTSRealtimeProvider
    except ImportError as e:
        raise ImportError(
            "QwenTTSRealtimeProvider requires dashscope package. "
            "Install with: pip install vixio[dev-qwen]"
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
    # Lazy loaders - Remote providers
    "get_edge_tts_provider",
    "get_openai_agent_provider",
    "get_vision_describer",
    # Lazy loaders - Local (in-process) providers
    "get_silero_vad_local_provider",
    "get_sherpa_asr_local_provider",
    "get_kokoro_tts_local_provider",
    # Lazy loaders - gRPC providers
    "get_silero_vad_grpc_provider",
    "get_sherpa_asr_grpc_provider",
    "get_kokoro_tts_grpc_provider",
    # Lazy loaders - Qwen providers
    "get_qwen_asr_realtime_provider",
    "get_qwen_tts_realtime_provider",
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
# Note: Catch both ImportError (package not installed) and RuntimeError (grpc version mismatch)
try:
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
    __all__.append("LocalSileroVADProvider")
except (ImportError, RuntimeError):
    pass

try:
    from vixio.providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
    __all__.append("LocalSherpaASRProvider")
except (ImportError, RuntimeError):
    pass

try:
    from vixio.providers.kokoro_cn_tts_local.grpc_provider import LocalKokoroTTSProvider
    __all__.append("LocalKokoroTTSProvider")
except (ImportError, RuntimeError):
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

# Qwen Realtime providers (requires dashscope)
try:
    from vixio.providers.qwen.qwen3_asr_flash_realtime import QwenASRRealtimeProvider
    __all__.append("QwenASRRealtimeProvider")
except ImportError:
    pass

try:
    from vixio.providers.qwen.qwen3_tts_flash_realtime import QwenTTSRealtimeProvider
    __all__.append("QwenTTSRealtimeProvider")
except ImportError:
    pass
