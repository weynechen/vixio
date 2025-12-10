"""
Local Kokoro TTS Provider (In-Process Inference)

This provider runs TTS inference directly in the current process
without requiring a separate gRPC service.

Requires: pip install vixio[kokoro-cn-tts-local]
"""

import asyncio
from typing import Dict, Any, AsyncGenerator
import numpy as np

from vixio.providers.tts import TTSProvider
from vixio.providers.registry import register_provider


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
        
        # Model (initialized lazily)
        self._model = None
        self._device = None
        self._inference_lock = asyncio.Lock()
        
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
        Initialize provider: load Kokoro model.
        """
        try:
            from kokoro import KModel
            import torch
            
            # kokoro library calls logger.remove() on import, which removes all 
            # vixio's loguru handlers. We need to reconfigure the logger.
            from vixio.utils.logger_config import reconfigure_logger
            reconfigure_logger()
        except ImportError as e:
            raise ImportError(
                "kokoro or torch not installed. "
                "Install with: pip install vixio[kokoro-cn-tts-local]"
            ) from e
        
        # Determine device
        self._device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.logger.info(f"Using device: {self._device}")
        
        # Load model
        self._model = KModel(repo_id=self.repo_id).to(self._device).eval()
        
        self.logger.info(f"Kokoro TTS model loaded (device={self._device}, repo={self.repo_id})")
    
    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Synthesize text to speech.
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio bytes (PCM format, 16-bit, mono)
        """
        if self._model is None:
            raise RuntimeError("TTS provider not initialized. Call initialize() first.")
        
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise ImportError(
                "kokoro not installed. "
                "Install with: pip install vixio[kokoro-cn-tts-local]"
            ) from e
        
        self.logger.info(f"Synthesizing text: {text[:50]}...")
        
        try:
            async with self._inference_lock:
                # Run TTS inference in thread pool to avoid blocking event loop
                audio_data = await asyncio.get_event_loop().run_in_executor(
                    None,  # Use default executor (ThreadPoolExecutor)
                    self._synthesize_sync,
                    text
                )
            
            yield audio_data
            
            # Log sample count (audio_data is already bytes)
            sample_count = len(audio_data) // 2  # 16-bit samples
            self.logger.info(f"Synthesis completed: {sample_count} samples")
        
        except Exception as e:
            self.logger.error(f"TTS synthesis failed: {e}")
            raise
    
    def _synthesize_sync(self, text: str) -> bytes:
        """
        Synchronous TTS synthesis (runs in thread pool).
        
        This method contains the CPU-intensive TTS inference and is designed
        to be called via run_in_executor() to avoid blocking the event loop.
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio bytes (PCM format, 16-bit, mono)
        """
        from kokoro import KPipeline
        import numpy as np
        
        # Create pipeline
        lang_code = 'z' if self.lang == 'zh' else 'a'
        pipeline = KPipeline(
            lang_code=lang_code,
            repo_id=self.repo_id,
            model=self._model
        )
        
        # Speed adjustment for long text
        def speed_callable(len_ps):
            speed = self.speed * 0.8
            if len_ps <= 83:
                speed = self.speed
            elif len_ps < 183:
                speed = self.speed * (1 - (len_ps - 83) / 500)
            return speed * 1.1
        
        # Generate audio
        generator = pipeline(text, voice=self.voice, speed=speed_callable)
        result = next(generator)
        
        # Get audio data
        audio_data = result.audio
        if hasattr(audio_data, 'cpu'):
            audio_np = audio_data.cpu().numpy()
        else:
            audio_np = audio_data
        
        # Kokoro outputs at 24kHz, resample if needed
        if self.sample_rate != 24000:
            target_length = int(len(audio_np) * self.sample_rate / 24000)
            indices = np.linspace(0, len(audio_np) - 1, target_length)
            audio_np = np.interp(indices, np.arange(len(audio_np)), audio_np)
        
        # Convert to int16 PCM bytes
        audio_int16 = (audio_np * 32767).astype(np.int16)
        return audio_int16.tobytes()
    
    def cancel(self) -> None:
        """Cancel ongoing synthesis (no-op for in-process)"""
        self.logger.debug("TTS synthesis cancellation requested")
    
    async def cleanup(self) -> None:
        """Cleanup provider resources"""
        self._model = None
        self.logger.info("TTS provider cleaned up")
    
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

