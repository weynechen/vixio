"""
Kokoro TTS Provider

Provider naming convention:
- kokoro-tts-local: In-process inference (requires: pip install vixio[kokoro-cn-tts-local])
- kokoro-tts-grpc: Connect to gRPC service (requires: pip install vixio[kokoro-cn-tts-grpc])

Usage:
    # In-process mode (no external service needed)
    from vixio.providers.kokoro_cn_tts_local import LocalKokoroTTSInProcessProvider
    provider = LocalKokoroTTSInProcessProvider(voice="zf_001")
    
    # gRPC mode (production)
    from vixio.providers.kokoro_cn_tts_local import LocalKokoroTTSProvider
    provider = LocalKokoroTTSProvider(service_url="localhost:50053")
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
    'LocalKokoroTTSProvider',  # kokoro-tts-grpc
    'LocalKokoroTTSInProcessProvider',  # kokoro-tts-local
]

