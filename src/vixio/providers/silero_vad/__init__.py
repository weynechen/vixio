"""
Silero VAD Provider

Supports multiple operation modes:
- silero-vad-local: In-process inference (requires: pip install vixio[silero-vad-local])
- silero-vad-grpc: Connect to gRPC service (requires: pip install vixio[silero-vad-grpc])

Usage:
    from vixio.providers.silero_vad import SileroVADProvider
    
    # Auto mode (development)
    vad = SileroVADProvider()
    
    # gRPC mode (production)
    vad = SileroVADProvider(mode="grpc", service_url="vad-service:50051")
"""

from vixio.providers.silero_vad.provider import SileroVADProvider

# gRPC provider (connects to external service, requires grpc)
try:
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
except ImportError:
    LocalSileroVADProvider = None

# In-process provider (requires: pip install vixio[silero-vad-local])
try:
    from vixio.providers.silero_vad.local_provider import LocalSileroVADInProcessProvider
except ImportError:
    LocalSileroVADInProcessProvider = None

__all__ = [
    "SileroVADProvider",
    "LocalSileroVADProvider",  # gRPC client
    "LocalSileroVADInProcessProvider",  # In-process inference
]
