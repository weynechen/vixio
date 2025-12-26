"""
Qwen Realtime ASR/TTS/Omni Providers

This package provides ASR, TTS, and Omni (end-to-end) providers using 
Alibaba Cloud's Qwen models via the DashScope SDK.

Providers:
- Qwen3AsrFlashRealtimeProvider: ASR with built-in VAD and context support
- Qwen3TtsFlashRealtimeProvider: TTS with streaming input and auto-segmentation
- QwenOmniRealtimeProvider: End-to-end voice conversation (VAD+ASR+LLM+TTS)

Requires: pip install vixio[dev-qwen]
"""

from vixio.providers.qwen.qwen3_asr_flash_realtime import Qwen3AsrFlashRealtimeProvider
from vixio.providers.qwen.qwen3_tts_flash_realtime import Qwen3TtsFlashRealtimeProvider
from vixio.providers.qwen.qwen_omni_realtime import QwenOmniRealtimeProvider

__all__ = [
    "Qwen3AsrFlashRealtimeProvider",
    "Qwen3TtsFlashRealtimeProvider",
    "QwenOmniRealtimeProvider",
]
