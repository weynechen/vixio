"""
Local Kokoro TTS Provider (In-Process Inference)

This provider runs TTS inference directly in the current process
without requiring a separate gRPC service.

Requires: pip install vixio[kokoro-cn-tts-local]
"""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, Any, AsyncGenerator
import numpy as np

from vixio.providers.tts import TTSProvider
from vixio.providers.registry import register_provider


# Global variables for worker process (initialized once per process)
_worker_model = None
_worker_device = None

# Global ProcessPoolExecutor shared by all TTS provider instances
_global_executor: ProcessPoolExecutor = None
_global_executor_lock = None  # Will be initialized on first use


def _get_global_executor() -> ProcessPoolExecutor:
    """Get or create the global ProcessPoolExecutor."""
    global _global_executor, _global_executor_lock
    
    # Lazy init lock (thread-safe for initial creation)
    if _global_executor_lock is None:
        import threading
        _global_executor_lock = threading.Lock()
    
    with _global_executor_lock:
        if _global_executor is None:
            import multiprocessing
            ctx = multiprocessing.get_context('spawn')
            _global_executor = ProcessPoolExecutor(max_workers=1, mp_context=ctx)
        return _global_executor


def _shutdown_global_executor():
    """Shutdown the global executor (call on application exit)."""
    global _global_executor
    if _global_executor is not None:
        _global_executor.shutdown(wait=True)
        _global_executor = None


def _init_worker(repo_id: str):
    """Initialize worker process with model."""
    global _worker_model, _worker_device
    
    from kokoro import KModel
    import torch
    
    _worker_device = 'cuda' if torch.cuda.is_available() else 'cpu'
    _worker_model = KModel(repo_id=repo_id).to(_worker_device).eval()


def _synthesize_worker(text: str, voice: str, speed: float, lang: str, sample_rate: int, repo_id: str) -> bytes:
    """
    TTS synthesis in worker process (bypasses GIL).
    
    Args:
        text: Text to synthesize
        voice: Voice ID
        speed: Speech speed
        lang: Language code
        sample_rate: Output sample rate
        repo_id: Model repo ID
        
    Returns:
        Audio bytes (PCM format, 16-bit, mono)
    """
    global _worker_model, _worker_device
    
    from kokoro import KPipeline
    import numpy as np
    
    # Initialize model if not already done (first call in this process)
    if _worker_model is None:
        _init_worker(repo_id)
        # Verify initialization succeeded
        if _worker_model is None:
            raise RuntimeError(f"Failed to initialize Kokoro model from {repo_id}")
    
    # Create pipeline
    lang_code = 'z' if lang == 'zh' else 'a'
    pipeline = KPipeline(
        lang_code=lang_code,
        repo_id=repo_id,
        model=_worker_model
    )
    
    # Speed adjustment for long text
    def speed_callable(len_ps):
        s = speed * 0.8
        if len_ps <= 83:
            s = speed
        elif len_ps < 183:
            s = speed * (1 - (len_ps - 83) / 500)
        return s * 1.1
    
    # Generate audio
    generator = pipeline(text, voice=voice, speed=speed_callable)
    result = next(generator)
    
    # Get audio data
    audio_data = result.audio
    if hasattr(audio_data, 'cpu'):
        audio_np = audio_data.cpu().numpy()
    else:
        audio_np = audio_data
    
    # Kokoro outputs at 24kHz, resample if needed
    if sample_rate != 24000:
        target_length = int(len(audio_np) * sample_rate / 24000)
        indices = np.linspace(0, len(audio_np) - 1, target_length)
        audio_np = np.interp(indices, np.arange(len(audio_np)), audio_np)
    
    # Convert to int16 PCM bytes
    audio_int16 = (audio_np * 32767).astype(np.int16)
    return audio_int16.tobytes()


@register_provider("kokoro-tts-local")
class LocalKokoroTTSInProcessProvider(TTSProvider):
    """
    Local Kokoro TTS Provider (In-Process Inference).
    
    Runs TTS inference directly in the current process using Kokoro.
    Suitable for development and single-instance deployments.
    
    Features:
    - No external service dependency
    - GPU acceleration when available (CUDA)
    - Chinese language support (Kokoro v1.1 zh)
    - Multiple voice options
    
    Requirements:
        pip install vixio[kokoro-cn-tts-local]
        # or manually: pip install kokoro misaki[zh] torch
    """
    
    @property
    def is_local(self) -> bool:
        """This is a local (in-process) service"""
        return True
    
    @property
    def is_stateful(self) -> bool:
        """TTS is stateless - each request is independent"""
        return False
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "tts"
    
    def __init__(
        self,
        voice: str = "zf_001",
        speed: float = 1.0,
        lang: str = "zh",
        sample_rate: int = 16000,
        repo_id: str = "hexgrad/Kokoro-82M-v1.1-zh"
    ):
        """
        Initialize Local Kokoro TTS provider (in-process).
        
        Args:
            voice: Voice ID (zf_001, zf_002, zm_001, zm_002)
            speed: Speech speed multiplier
            lang: Language code
            sample_rate: Output audio sample rate
            repo_id: Hugging Face repo ID for model
        """
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.sample_rate = sample_rate
        self.repo_id = repo_id
        
        # Flag to track initialization (executor is global/shared)
        self._initialized = False
        
        self.logger.info(
            f"Initialized Local Kokoro TTS (in-process) "
            f"(voice={voice}, speed={speed}, lang={lang})"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "voice": {
                "type": "string",
                "default": "zf_001",
                "description": "Voice ID (zf_001, zf_002, zm_001, zm_002)"
            },
            "speed": {
                "type": "float",
                "default": 1.0,
                "description": "Speech speed multiplier"
            },
            "lang": {
                "type": "string",
                "default": "zh",
                "description": "Language code"
            },
            "sample_rate": {
                "type": "int",
                "default": 16000,
                "description": "Output audio sample rate"
            },
            "repo_id": {
                "type": "string",
                "default": "hexgrad/Kokoro-82M-v1.1-zh",
                "description": "Hugging Face repo ID for model"
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider: get global ProcessPoolExecutor for TTS inference.
        
        Model is loaded lazily in worker process on first synthesis call.
        Using ProcessPoolExecutor to bypass Python GIL.
        Executor is shared globally across all sessions.
        """
        try:
            import torch
            
            # kokoro library calls logger.remove() on import, which removes all 
            # vixio's loguru handlers. We need to reconfigure the logger.
            from vixio.utils.logger_config import reconfigure_logger
            reconfigure_logger()
        except ImportError as e:
            raise ImportError(
                "torch not installed. "
                "Install with: pip install vixio[kokoro-cn-tts-local]"
            ) from e
        
        # Determine device (for logging only, actual model runs in worker process)
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.logger.info(f"TTS will use device: {device}")
        
        # Get global shared executor (created once, reused by all sessions)
        _get_global_executor()
        self._initialized = True
        
        self.logger.info(f"Kokoro TTS ready (using global ProcessPoolExecutor, repo={self.repo_id})")
    
    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Synthesize text to speech using ProcessPoolExecutor.
        
        TTS inference runs in separate process to bypass GIL,
        preventing audio playback stuttering.
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio bytes (PCM format, 16-bit, mono)
        """
        if not self._initialized:
            raise RuntimeError("TTS provider not initialized. Call initialize() first.")
        
        self.logger.info(f"Synthesizing text: {text[:50]}...")
        
        try:
            loop = asyncio.get_event_loop()
            executor = _get_global_executor()
            
            # Run TTS in separate process (bypasses GIL completely)
            audio_data = await loop.run_in_executor(
                executor,
                _synthesize_worker,
                text,
                self.voice,
                self.speed,
                self.lang,
                self.sample_rate,
                self.repo_id
            )
            
            yield audio_data
            
            # Log sample count
            sample_count = len(audio_data) // 2  # 16-bit samples
            self.logger.info(f"Synthesis completed: {sample_count} samples")
        
        except Exception as e:
            self.logger.error(f"TTS synthesis failed: {e}")
            raise
    
    def cancel(self) -> None:
        """Cancel ongoing synthesis (no-op for process pool)"""
        self.logger.debug("TTS synthesis cancellation requested")
    
    async def cleanup(self) -> None:
        """Cleanup provider instance (global executor is NOT shutdown here)."""
        # Note: Global executor is shared across all sessions, don't shutdown here
        # Call _shutdown_global_executor() on application exit if needed
        self._initialized = False
        self.logger.info("TTS provider instance cleaned up")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "voice": self.voice,
            "speed": self.speed,
            "lang": self.lang,
            "sample_rate": self.sample_rate,
            "repo_id": self.repo_id,
        })
        return config

