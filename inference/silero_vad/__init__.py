"""
Silero VAD Microservice

gRPC-based Voice Activity Detection service using Silero VAD model.
"""

from .client import VADServiceClient

__all__ = [
    'VADServiceClient',
]

