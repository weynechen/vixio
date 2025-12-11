"""
Qwen Realtime ASR/TTS Providers

This package provides ASR and TTS providers using Alibaba Cloud's Qwen models
via the DashScope SDK.

Requires: pip install vixio[dev-qwen]
"""

from vixio.providers.qwen.qwen3_asr_flash_realtime import QwenASRRealtimeProvider
from vixio.providers.qwen.qwen3_tts_flash_realtime import QwenTTSRealtimeProvider

__all__ = [
    "QwenASRRealtimeProvider",
    "QwenTTSRealtimeProvider",
]
