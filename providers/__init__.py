"""
Provider interfaces and implementations

This package contains both:
1. Provider interfaces (base.py, vad.py, asr.py, tts.py, agent.py, vision.py)
2. Provider implementations (silero_vad, sherpa_onnx_local, edge_tts, openai_agent)
3. Provider registry and factory for plugin system
"""

# Provider interfaces
from providers.base import BaseProvider
from providers.vad import VADProvider, VADEvent
from providers.asr import ASRProvider
from providers.tts import TTSProvider
from providers.agent import AgentProvider, Tool

# Provider registry and factory
from providers.registry import ProviderRegistry, register_provider
from providers.factory import ProviderFactory

# Provider implementations (legacy - direct instantiation)
from providers.silero_vad.provider import SileroVADProvider
from providers.sherpa_onnx_local.provider import SherpaOnnxLocalProvider
from providers.sherpa_onnx_local.shared_provider import SharedModelSherpaOnnxProvider
from providers.edge_tts.provider import EdgeTTSProvider
from providers.openai_agent.provider import OpenAIAgentProvider

# Provider implementations (plugin system - registry-based)
# These are auto-registered via @register_provider decorator
from providers.silero_vad.grpc_provider import LocalSileroVADProvider
from providers.kokoro.grpc_provider import LocalKokoroTTSProvider

__all__ = [
    # Interfaces
    "BaseProvider",
    "VADProvider",
    "VADEvent",
    "ASRProvider",
    "TTSProvider",
    "AgentProvider",
    "Tool",
    # Registry and Factory
    "ProviderRegistry",
    "register_provider",
    "ProviderFactory",
    # Implementations (legacy)
    "SileroVADProvider",
    "SherpaOnnxLocalProvider",
    "SharedModelSherpaOnnxProvider",
    "EdgeTTSProvider",
    "OpenAIAgentProvider",
    # Implementations (plugin system - gRPC)
    "LocalSileroVADProvider",
    "LocalKokoroTTSProvider",
]

