"""
Kokoro TTS Microservice

gRPC-based Text-to-Speech service using Kokoro v1.1 Chinese model.
"""

from .client import TTSServiceClient

__all__ = [
    'TTSServiceClient',
]

