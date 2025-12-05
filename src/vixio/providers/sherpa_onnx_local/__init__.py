"""
Sherpa ONNX ASR Provider

Supports multiple operation modes:
- sherpa-onnx-asr-local: In-process inference (requires: pip install vixio[sherpa-onnx-asr-local])
- sherpa-onnx-asr-grpc: Connect to gRPC service (requires: pip install vixio[sherpa-onnx-asr-grpc])

Usage:
    from vixio.providers.sherpa_onnx_local import LocalSherpaASRProvider
    
    # gRPC mode (production)
    provider = LocalSherpaASRProvider(service_url="localhost:50052")
    await provider.initialize()
    text = await provider.transcribe(audio_chunks)
"""

# gRPC provider (connects to external service, requires grpc)
# Note: Catch both ImportError (package not installed) and RuntimeError (grpc version mismatch)
try:
    from vixio.providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
except (ImportError, RuntimeError):
    LocalSherpaASRProvider = None

# In-process provider (requires: pip install vixio[sherpa-onnx-asr-local])
try:
    from vixio.providers.sherpa_onnx_local.local_provider import LocalSherpaASRInProcessProvider
except ImportError:
    LocalSherpaASRInProcessProvider = None

__all__ = [
    "LocalSherpaASRProvider",  # gRPC client
    "LocalSherpaASRInProcessProvider",  # In-process inference
]
