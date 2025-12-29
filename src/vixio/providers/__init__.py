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
from vixio.providers.sentence_aggregator import SentenceAggregatorProvider

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
    Load Qwen3AsrFlashRealtimeProvider class (qwen3-asr-flash-realtime).
    
    Real-time ASR using Alibaba Cloud's Qwen3-ASR-Flash model.
    Features: Built-in VAD, context enhancement support
    Requires: pip install vixio[dev-qwen]
    
    Returns:
        Qwen3AsrFlashRealtimeProvider class
    """
    try:
        from vixio.providers.qwen.qwen3_asr_flash_realtime import Qwen3AsrFlashRealtimeProvider
        return Qwen3AsrFlashRealtimeProvider
    except ImportError as e:
        raise ImportError(
            "Qwen3AsrFlashRealtimeProvider requires dashscope package. "
            "Install with: pip install vixio[dev-qwen]"
        ) from e


def get_qwen_tts_realtime_provider():
    """
    Load Qwen3TtsFlashRealtimeProvider class (qwen3-tts-flash-realtime).
    
    Real-time TTS using Alibaba Cloud's Qwen3-TTS-Flash model.
    Features: Streaming input, auto-segmentation, multiple audio formats
    Requires: pip install vixio[dev-qwen]
    
    Returns:
        Qwen3TtsFlashRealtimeProvider class
    """
    try:
        from vixio.providers.qwen.qwen3_tts_flash_realtime import Qwen3TtsFlashRealtimeProvider
        return Qwen3TtsFlashRealtimeProvider
    except ImportError as e:
        raise ImportError(
            "Qwen3TtsFlashRealtimeProvider requires dashscope package. "
            "Install with: pip install vixio[dev-qwen]"
        ) from e


def get_qwen_omni_realtime_provider():
    """
    Load QwenOmniRealtimeProvider class (qwen-omni-realtime).
    
    End-to-end realtime voice conversation using Alibaba Cloud's 
    Qwen-Omni-Realtime model. Integrates VAD + ASR + LLM + TTS.
    Requires: pip install vixio[dev-qwen]
    
    Returns:
        QwenOmniRealtimeProvider class
    """
    try:
        from vixio.providers.qwen.qwen_omni_realtime import QwenOmniRealtimeProvider
        return QwenOmniRealtimeProvider
    except ImportError as e:
        raise ImportError(
            "QwenOmniRealtimeProvider requires dashscope package. "
            "Install with: pip install vixio[dev-qwen]"
        ) from e


def get_doubao_tts_bidirectional_provider():
    """
    Load DoubaoTTSBidirectionalProvider class (doubao-tts-bidirectional).
    
    Bidirectional TTS using Volcengine's WebSocket protocol.
    Features: Streaming text input, multiple audio formats
    Requires: pip install vixio[doubao]
    
    Returns:
        DoubaoTTSBidirectionalProvider class
    """
    try:
        from vixio.providers.doubao.doubao_tts_bidirectional import DoubaoTTSBidirectionalProvider
        return DoubaoTTSBidirectionalProvider
    except ImportError as e:
        raise ImportError(
            "DoubaoTTSBidirectionalProvider requires websockets package. "
            "Install with: pip install vixio[doubao]"
        ) from e


def get_doubao_realtime_provider():
    """
    Load DoubaoRealtimeProvider class (doubao-realtime).
    
    End-to-end realtime voice conversation using Volcengine's dialog API.
    Features: Integrated VAD + ASR + LLM + TTS
    Requires: pip install vixio[doubao]
    
    Returns:
        DoubaoRealtimeProvider class
    """
    try:
        from vixio.providers.doubao.doubao_realtime import DoubaoRealtimeProvider
        return DoubaoRealtimeProvider
    except ImportError as e:
        raise ImportError(
            "DoubaoRealtimeProvider requires websockets package. "
            "Install with: pip install vixio[doubao]"
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
    "SentenceAggregatorProvider",
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
    "get_qwen_omni_realtime_provider",
    # Lazy loaders - Doubao providers
    "get_doubao_tts_bidirectional_provider",
    "get_doubao_realtime_provider",
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
# Note: Catch ImportError (package not installed), RuntimeError (grpc version mismatch),
#       and any protobuf version errors
try:
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
    __all__.append("LocalSileroVADProvider")
except Exception:
    # Silently skip if gRPC or protobuf dependencies are not available
    pass

try:
    from vixio.providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
    __all__.append("LocalSherpaASRProvider")
except Exception:
    # Silently skip if gRPC or protobuf dependencies are not available
    pass

try:
    from vixio.providers.kokoro_cn_tts_local.grpc_provider import LocalKokoroTTSProvider
    __all__.append("LocalKokoroTTSProvider")
except Exception:
    # Silently skip if gRPC or protobuf dependencies are not available
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
    from vixio.providers.qwen.qwen3_asr_flash_realtime import Qwen3AsrFlashRealtimeProvider
    __all__.append("Qwen3AsrFlashRealtimeProvider")
except ImportError:
    pass

try:
    from vixio.providers.qwen.qwen3_tts_flash_realtime import Qwen3TtsFlashRealtimeProvider
    __all__.append("Qwen3TtsFlashRealtimeProvider")
except ImportError:
    pass

try:
    from vixio.providers.qwen.qwen_omni_realtime import QwenOmniRealtimeProvider
    __all__.append("QwenOmniRealtimeProvider")
except ImportError:
    pass

# Sentence aggregator providers (always available)
try:
    from vixio.providers.sentence_aggregator import SimpleSentenceAggregatorProviderCN
    __all__.append("SimpleSentenceAggregatorProviderCN")
except ImportError:
    pass

# Doubao providers (requires websockets)
try:
    from vixio.providers.doubao.doubao_tts_bidirectional import DoubaoTTSBidirectionalProvider
    __all__.append("DoubaoTTSBidirectionalProvider")
except ImportError:
    pass

try:
    from vixio.providers.doubao.doubao_realtime import DoubaoRealtimeProvider
    __all__.append("DoubaoRealtimeProvider")
except ImportError:
    pass
