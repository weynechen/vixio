"""
Sherpa-ONNX local ASR provider implementation
"""

from providers.sherpa_onnx_local.provider import SherpaOnnxLocalProvider
from providers.sherpa_onnx_local.shared_provider import SharedModelSherpaOnnxProvider

__all__ = ["SherpaOnnxLocalProvider", "SharedModelSherpaOnnxProvider"]

