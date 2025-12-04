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

# Provider implementations (Remote APIs - no heavy dependencies)
from providers.edge_tts.provider import EdgeTTSProvider
from providers.openai_agent.provider import OpenAIAgentProvider

# Provider implementations (Local gRPC Clients - auto-registered)
# These are auto-registered via @register_provider decorator
from providers.silero_vad.grpc_provider import LocalSileroVADProvider
from providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
from providers.kokoro.grpc_provider import LocalKokoroTTSProvider

# Vision describers (auto-registered via @register_provider decorator)
from providers.vision_describers.openai_compatible import OpenAICompatibleVLM

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
    # Remote Provider Implementations
    "EdgeTTSProvider",
    "OpenAIAgentProvider",
    # Local gRPC Provider Implementations
    "LocalSileroVADProvider",
    "LocalSherpaASRProvider",
    "LocalKokoroTTSProvider",
    # Vision Describers
    "OpenAICompatibleVLM",
]

