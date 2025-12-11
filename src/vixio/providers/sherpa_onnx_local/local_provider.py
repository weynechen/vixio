"""
Local Sherpa ONNX ASR Provider (In-Process Inference)

This provider runs ASR inference directly in the current process
without requiring a separate gRPC service.

Model is automatically downloaded from HuggingFace on first use:
- Default: csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09

Requires: pip install vixio[sherpa-onnx-asr-local]
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
import numpy as np

from vixio.providers.asr import ASRProvider
from vixio.providers.registry import register_provider

# Default HuggingFace repo for ASR model
DEFAULT_ASR_REPO_ID = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
DEFAULT_ASR_LOCAL_DIR = "sherpa-onnx-sense-voice"


@register_provider("sherpa-onnx-asr-local")
class LocalSherpaASRInProcessProvider(ASRProvider):
    """
    Local Sherpa ONNX ASR Provider (In-Process Inference).
    
    Runs ASR inference directly in the current process using sherpa-onnx.
    Suitable for development and single-instance deployments.
    
    Features:
    - No external service dependency
    - Automatic model download from HuggingFace
    - SenseVoice model support (multi-language)
    - Offline recognition (batch processing)
    - Low latency (no network overhead)
    
    Requirements:
        pip install vixio[sherpa-onnx-asr-local]
        # or manually: pip install sherpa-onnx huggingface_hub
    """
    
    @property
    def is_local(self) -> bool:
        """This is a local (in-process) service"""
        return True
    
    @property
    def is_stateful(self) -> bool:
        """ASR is stateful - maintains recognition state"""
        return True
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "asr"
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        repo_id: str = DEFAULT_ASR_REPO_ID,
        language: str = "auto",
        num_threads: int = 4,
        use_itn: bool = True
    ):
        """
        Initialize Local Sherpa ONNX ASR provider (in-process).
        
        Model is automatically downloaded from HuggingFace if not found locally.
        
        Args:
            model_path: Path to Sherpa ONNX model directory. If None, downloads
                        from HuggingFace to models/sherpa-onnx-sense-voice/
            repo_id: HuggingFace repository ID for auto-download
            language: Language code ("auto", "zh", "en", etc.)
            num_threads: Number of threads for inference
            use_itn: Whether to use Inverse Text Normalization
        """
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.model_path = model_path  # Can be None, will be resolved in initialize()
        self.repo_id = repo_id
        self.language = language
        self.num_threads = num_threads
        self.use_itn = use_itn
        
        # Recognizer (initialized lazily)
        self._recognizer = None
        self._inference_lock = asyncio.Lock()
        self._resolved_model_path: Optional[str] = None
        
        self.logger.info(
            f"Initialized Local Sherpa ONNX ASR (in-process) "
            f"(repo_id={repo_id}, language={language})"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "model_path": {
                "type": "string",
                "default": None,
                "description": "Path to Sherpa ONNX model directory. If None, auto-downloads from HuggingFace."
            },
            "repo_id": {
                "type": "string",
                "default": DEFAULT_ASR_REPO_ID,
                "description": "HuggingFace repository ID for auto-download"
            },
            "language": {
                "type": "string",
                "default": "auto",
                "description": "Language code (auto/zh/en/ja/ko/yue)"
            },
            "num_threads": {
                "type": "int",
                "default": 4,
                "description": "Number of threads for inference"
            },
            "use_itn": {
                "type": "bool",
                "default": True,
                "description": "Whether to use Inverse Text Normalization"
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider: load Sherpa ONNX model.
        
        Automatically downloads model from HuggingFace if not found locally.
        """
        try:
            import sherpa_onnx
        except ImportError as e:
            raise ImportError(
                "sherpa-onnx not installed. "
                "Install with: pip install vixio[sherpa-onnx-asr-local]"
            ) from e
        
        # Resolve model path
        model_dir = self._resolve_model_path()
        self._resolved_model_path = str(model_dir)
        
        model_file = model_dir / "model.int8.onnx"
        tokens_file = model_dir / "tokens.txt"
        
        if not model_file.exists():
            raise FileNotFoundError(f"Model file not found: {model_file}")
        if not tokens_file.exists():
            raise FileNotFoundError(f"Tokens file not found: {tokens_file}")
        
        # Create offline recognizer for SenseVoice
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_file),
            tokens=str(tokens_file),
            num_threads=self.num_threads,
            sample_rate=16000,
            feature_dim=80,
            use_itn=self.use_itn,
            debug=False,
        )
        
        self.logger.info(f"Sherpa ONNX ASR model loaded from {model_dir}")
    
    def _resolve_model_path(self) -> Path:
        """
        Resolve model path, downloading from HuggingFace if necessary.
        
        Returns:
            Path to the model directory.
        """
        # If model_path is explicitly specified and exists, use it
        if self.model_path:
            model_dir = Path(self.model_path)
            if not model_dir.is_absolute():
                # Try relative to cwd first
                import os
                cwd = Path(os.getcwd())
                for _ in range(5):
                    candidate = cwd / self.model_path
                    if candidate.exists():
                        return candidate
                    cwd = cwd.parent
                # If not found, check absolute path
                if model_dir.exists():
                    return model_dir
            else:
                if model_dir.exists():
                    return model_dir
            
            # Model path specified but not found - try to download
            self.logger.warning(
                f"Model directory not found at {self.model_path}, "
                f"will download from HuggingFace: {self.repo_id}"
            )
        
        # Download from HuggingFace
        from vixio.utils.model_downloader import ensure_model_downloaded
        
        downloaded_path = ensure_model_downloaded(
            repo_id=self.repo_id,
            local_dir=DEFAULT_ASR_LOCAL_DIR,
        )
        
        return Path(downloaded_path)
    
    async def transcribe_stream(self, audio_chunks: list[bytes]):
        """
        Transcribe audio chunks to text (streaming output).
        
        This is a pseudo-streaming implementation - the underlying
        OfflineRecognizer processes all audio at once, but output is
        wrapped as an async iterator for interface consistency.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            
        Yields:
            Single text result after processing completes
        """
        if self._recognizer is None:
            raise RuntimeError("ASR provider not initialized. Call initialize() first.")
        
        try:
            # Concatenate all audio chunks
            audio_data = b''.join(audio_chunks)
            
            if not audio_data:
                return
            
            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            
            # Run inference with lock
            async with self._inference_lock:
                # Create stream and decode
                stream = self._recognizer.create_stream()
                stream.accept_waveform(16000, audio_float)
                self._recognizer.decode_stream(stream)
                
                result = stream.result.text.strip()
            
            if result:
                self.logger.debug(f"Transcribed: {result}")
                # Yield single result (pseudo-streaming)
                yield result
        
        except Exception as e:
            self.logger.error(f"ASR transcription failed: {e}")
            # Yield nothing on error
    
    async def reset(self) -> None:
        """Reset ASR state (no-op for offline recognizer)"""
        self.logger.debug("ASR state reset (no-op for offline mode)")
    
    async def cleanup(self) -> None:
        """Cleanup provider resources"""
        self._recognizer = None
        self.logger.info("ASR provider cleaned up")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "model_path": self.model_path,
            "repo_id": self.repo_id,
            "resolved_model_path": self._resolved_model_path,
            "language": self.language,
            "num_threads": self.num_threads,
            "use_itn": self.use_itn,
        })
        return config

