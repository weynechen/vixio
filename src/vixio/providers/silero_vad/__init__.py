"""
Silero VAD Provider

Provider naming convention:
- silero-vad-local: In-process inference (requires: pip install vixio[silero-vad-local])
- silero-vad-grpc: Connect to gRPC service (requires: pip install vixio[silero-vad-grpc])

Usage:
    # In-process mode (no external service needed)
    from vixio.providers.silero_vad import LocalSileroVADInProcessProvider
    vad = LocalSileroVADInProcessProvider()
    
    # gRPC mode (production)
    from vixio.providers.silero_vad import LocalSileroVADProvider
    vad = LocalSileroVADProvider(service_url="vad-service:50051")
"""

# gRPC provider (connects to external service, requires grpc)
# Note: Catch both ImportError (package not installed) and RuntimeError (grpc version mismatch)
try:
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
except (ImportError, RuntimeError):
    LocalSileroVADProvider = None

# In-process provider (requires: pip install vixio[silero-vad-local])
try:
    from vixio.providers.silero_vad.local_provider import LocalSileroVADInProcessProvider
except ImportError:
    LocalSileroVADInProcessProvider = None

__all__ = [
    "LocalSileroVADProvider",  # silero-vad-grpc
    "LocalSileroVADInProcessProvider",  # silero-vad-local
]
