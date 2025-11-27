"""
Kokoro TTS Provider

gRPC-based provider for Kokoro v1.1 Chinese TTS.
"""

from .grpc_provider import LocalKokoroTTSProvider

__all__ = [
    'LocalKokoroTTSProvider',
]

