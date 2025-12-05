"""
Kokoro TTS Provider

Supports multiple operation modes:
- kokoro-tts-local: In-process inference (requires: pip install vixio[kokoro-cn-tts-local])
- kokoro-tts-grpc: Connect to gRPC service (requires: pip install vixio[kokoro-cn-tts-grpc])
"""

# gRPC provider (connects to external service, requires grpc)
# Note: Catch both ImportError (package not installed) and RuntimeError (grpc version mismatch)
try:
    from .grpc_provider import LocalKokoroTTSProvider
except (ImportError, RuntimeError):
    LocalKokoroTTSProvider = None

# In-process provider (requires: pip install vixio[kokoro-cn-tts-local])
try:
    from .local_provider import LocalKokoroTTSInProcessProvider
except ImportError:
    LocalKokoroTTSInProcessProvider = None

__all__ = [
    'LocalKokoroTTSProvider',  # gRPC client
    'LocalKokoroTTSInProcessProvider',  # In-process inference
]

