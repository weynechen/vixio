"""
Provider interfaces and implementations

This package contains both:
1. Provider interfaces (base.py, vad.py, asr.py, tts.py, agent.py, vision.py)
2. Provider implementations (silero_vad, sherpa_onnx_local, edge_tts)
"""

# Provider interfaces
from vixio.providers.base import BaseProvider
from vixio.providers.vad import VADProvider
from vixio.providers.asr import ASRProvider
from vixio.providers.tts import TTSProvider

# Provider implementations
from vixio.providers.silero_vad.provider import SileroVADProvider
from vixio.providers.sherpa_onnx_local.provider import SherpaOnnxLocalProvider
from vixio.providers.edge_tts.provider import EdgeTTSProvider

__all__ = [
    # Interfaces
    "BaseProvider",
    "VADProvider",
    "ASRProvider",
    "TTSProvider",
    # Implementations
    "SileroVADProvider",
    "SherpaOnnxLocalProvider",
    "EdgeTTSProvider",
]

