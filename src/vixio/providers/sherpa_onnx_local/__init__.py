"""
Sherpa ONNX ASR Provider

Provider naming convention:
- sherpa-onnx-asr-local: In-process inference (requires: pip install vixio[sherpa-onnx-asr-local])
- sherpa-onnx-asr-grpc: Connect to gRPC service (requires: pip install vixio[sherpa-onnx-asr-grpc])

Usage:
    # In-process mode (no external service needed)
    from vixio.providers.sherpa_onnx_local import LocalSherpaASRInProcessProvider
    provider = LocalSherpaASRInProcessProvider(model_path="models/sherpa-onnx")
    
    # gRPC mode (production)
    from vixio.providers.sherpa_onnx_local import LocalSherpaASRProvider
    provider = LocalSherpaASRProvider(service_url="localhost:50052")
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
    "LocalSherpaASRProvider",  # sherpa-onnx-asr-grpc
    "LocalSherpaASRInProcessProvider",  # sherpa-onnx-asr-local
]
