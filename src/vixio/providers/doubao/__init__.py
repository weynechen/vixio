"""
Doubao (豆包) providers for Volcengine speech services.

Providers:
- DoubaoTTSBidirectionalProvider: Bidirectional TTS via WebSocket
- DoubaoRealtimeProvider: End-to-end realtime voice conversation
"""

__all__ = []

# TTS Provider
try:
    from vixio.providers.doubao.doubao_tts_bidirectional import DoubaoTTSBidirectionalProvider
    __all__.append("DoubaoTTSBidirectionalProvider")
except ImportError:
    pass

# Realtime Provider
try:
    from vixio.providers.doubao.doubao_realtime import DoubaoRealtimeProvider
    __all__.append("DoubaoRealtimeProvider")
except ImportError:
    pass
