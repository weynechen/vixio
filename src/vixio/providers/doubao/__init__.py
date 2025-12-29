"""
Doubao (豆包) providers for Volcengine speech services.

Providers:
- DoubaoTTSBidirectionalProvider: Bidirectional TTS via WebSocket
"""

# TTS Provider
try:
    from vixio.providers.doubao.doubao_tts_bidirectional import DoubaoTTSBidirectionalProvider
    __all__ = ["DoubaoTTSBidirectionalProvider"]
except ImportError:
    __all__ = []
