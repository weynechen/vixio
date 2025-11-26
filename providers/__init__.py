"""
Provider interfaces and implementations

This package contains both:
1. Provider interfaces (base.py, vad.py, asr.py, tts.py, agent.py, vision.py)
2. Provider implementations (silero_vad, sherpa_onnx_local, edge_tts, openai_agent)
"""

# Provider interfaces
from providers.base import BaseProvider
from providers.vad import VADProvider
from providers.asr import ASRProvider
from providers.tts import TTSProvider
from providers.agent import AgentProvider, Tool

# Provider implementations
from providers.silero_vad.provider import SileroVADProvider
from providers.sherpa_onnx_local.provider import SherpaOnnxLocalProvider
from providers.sherpa_onnx_local.shared_provider import SharedModelSherpaOnnxProvider
from providers.edge_tts.provider import EdgeTTSProvider
from providers.openai_agent.provider import OpenAIAgentProvider

__all__ = [
    # Interfaces
    "BaseProvider",
    "VADProvider",
    "ASRProvider",
    "TTSProvider",
    "AgentProvider",
    "Tool",
    # Implementations
    "SileroVADProvider",
    "SherpaOnnxLocalProvider",
    "SharedModelSherpaOnnxProvider",  # Shared ASR for memory optimization
    "EdgeTTSProvider",
    "OpenAIAgentProvider",
]

