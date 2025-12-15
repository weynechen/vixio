"""
Qwen Realtime ASR/TTS/Omni Providers

This package provides ASR, TTS, and Omni (end-to-end) providers using 
Alibaba Cloud's Qwen models via the DashScope SDK.

Requires: pip install vixio[dev-qwen]
"""

from vixio.providers.qwen.qwen3_asr_flash_realtime import QwenASRRealtimeProvider
from vixio.providers.qwen.qwen3_tts_flash_realtime import QwenTTSRealtimeProvider
from vixio.providers.qwen.qwen_omni_realtime import QwenOmniRealtimeProvider

__all__ = [
    "QwenASRRealtimeProvider",
    "QwenTTSRealtimeProvider",
    "QwenOmniRealtimeProvider",
]
