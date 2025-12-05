"""
Local Kokoro TTS Provider (gRPC Client)

This provider acts as a gRPC client connecting to the Kokoro TTS service.
Suitable for all deployment modes (dev/docker/k8s).
"""

import uuid
from typing import Dict, Any, AsyncGenerator
import numpy as np
from vixio.providers.tts import TTSProvider
from vixio.providers.registry import register_provider
from vixio.providers.kokoro_cn_tts_local.client import TTSServiceClient


@register_provider("kokoro-tts-grpc")
class LocalKokoroTTSProvider(TTSProvider):
    """
    Local Kokoro TTS Provider (gRPC Client).
    
    Connects to Kokoro TTS gRPC service for text-to-speech synthesis.
    Supports streaming output for low latency.
    
    Deployment modes:
    - Dev: localhost:50053 (1 replica)
    - Docker: kokoro-tts-service:50053 (1 replica)
    - K8s: kokoro-tts-service:50053 (2-10 replicas, load-balanced)
    """
    
    @property
    def is_local(self) -> bool:
        """This is a local (self-hosted) service"""
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
        service_url: str,
        voice: str = "zf_001",
        speed: float = 1.0,
        lang: str = "zh",
        sample_rate: int = 16000
    ):
        """
        Initialize Local Kokoro TTS provider.
        
        Args:
            service_url: gRPC service URL
                - Dev: "localhost:50053"
                - Docker: "kokoro-tts-service:50053"
                - K8s: "kokoro-tts-service:50053"
            voice: Voice ID (zf_001, zf_002, zm_001, zm_002)
            speed: Speech speed multiplier
            lang: Language code
            sample_rate: Audio sample rate
        """
        # Use registered name from decorator
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.service_url = service_url
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.sample_rate = sample_rate
        
        # gRPC client
        self._client: TTSServiceClient = None
        self.session_id: str = None
        
        self.logger.info(
            f"Initialized Local Kokoro TTS (gRPC) "
            f"targeting {service_url}"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "service_url": {
                "type": "string",
                "required": True,
                "description": "Kokoro TTS gRPC service URL",
                "examples": {
                    "dev": "localhost:50053",
                    "docker": "kokoro-tts-service:50053",
                    "k8s": "kokoro-tts-service:50053"
                }
            },
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
                "description": "Audio sample rate"
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider: connect to gRPC service and create session.
        
        Called once when provider is created.
        """
        # Create gRPC client
        self._client = TTSServiceClient(self.service_url)
        await self._client.connect()
        
        # Create session on server
        self.session_id = str(uuid.uuid4())
        success = await self._client.create_session(
            session_id=self.session_id,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
            sample_rate=self.sample_rate
        )
        
        if not success:
            raise RuntimeError(f"Failed to create TTS session {self.session_id}")
        
        self.logger.info(
            f"TTS session created: {self.session_id} "
            f"(voice={self.voice}, speed={self.speed})"
        )
    
    async def synthesize(
        self,
        text: str
    ) -> AsyncGenerator[bytes, None]:
        """
        Synthesize text to speech (streaming).
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio bytes (PCM format, 16-bit, mono, 16kHz)
        """
        if not self._client or not self.session_id:
            raise RuntimeError("TTS provider not initialized. Call initialize() first.")
        
        try:
            async for sample_rate, audio_data, is_final in self._client.synthesize(
                session_id=self.session_id,
                text=text,
                join_sentences=True
            ):
                if is_final:
                    break
                
                # Resample from 24kHz to 16kHz if needed (for system compatibility)
                if sample_rate == 24000:
                    # Simple linear resampling: 24kHz -> 16kHz (3:2 ratio)
                    # Take every 3rd sample out of 2 samples (downsample)
                    target_length = int(len(audio_data) * 16000 / 24000)
                    indices = np.linspace(0, len(audio_data) - 1, target_length)
                    audio_resampled = np.interp(indices, np.arange(len(audio_data)), audio_data)
                else:
                    audio_resampled = audio_data
                
                # Convert float32 audio to int16 PCM bytes
                # audio_data is np.ndarray with shape (N,) and dtype float32 in range [-1, 1]
                audio_int16 = (audio_resampled * 32767).astype(np.int16)
                audio_bytes = audio_int16.tobytes()
                
                yield audio_bytes
        
        except Exception as e:
            self.logger.error(f"TTS synthesis failed: {e}")
            raise
    
    async def synthesize_raw(
        self,
        text: str
    ) -> AsyncGenerator[tuple[int, np.ndarray], None]:
        """
        Synthesize text to speech (streaming, raw audio format).
        
        This is a helper method that returns raw float32 audio with sample rate.
        For the standard interface, use synthesize() which returns PCM bytes.
        
        Args:
            text: Text to synthesize
            
        Yields:
            Tuple of (sample_rate, audio_chunk as float32 numpy array)
        """
        if not self._client or not self.session_id:
            raise RuntimeError("TTS provider not initialized. Call initialize() first.")
        
        try:
            async for sample_rate, audio_data, is_final in self._client.synthesize(
                session_id=self.session_id,
                text=text,
                join_sentences=True
            ):
                if is_final:
                    break
                
                yield (sample_rate, audio_data)
        
        except Exception as e:
            self.logger.error(f"TTS stream synthesis failed: {e}")
            raise
    
    def cancel(self) -> None:
        """
        Cancel ongoing synthesis.
        
        For gRPC-based TTS, cancellation is handled by the client closing
        the stream or the caller stopping iteration. This is a no-op
        as the gRPC stream is automatically cleaned up.
        """
        self.logger.debug("TTS synthesis cancellation requested (gRPC stream will be closed)")
    
    async def cleanup(self) -> None:
        """
        Cleanup provider resources.
        
        Destroys server-side session and closes gRPC connection.
        """
        if self._client and self.session_id:
            try:
                # Destroy server-side session
                await self._client.destroy_session(self.session_id)
                self.logger.info(f"TTS session destroyed: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Error destroying TTS session: {e}")
            
            try:
                # Close gRPC connection
                await self._client.close()
            except Exception as e:
                self.logger.error(f"Error closing TTS client: {e}")
            
            self._client = None
            self.session_id = None
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "service_url": self.service_url,
            "voice": self.voice,
            "speed": self.speed,
            "lang": self.lang,
            "sample_rate": self.sample_rate,
            "session_id": self.session_id,
        })
        return config

