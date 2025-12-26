"""
Edge TTS provider implementation
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Dict, Any
from vixio.providers.tts import TTSProvider
from vixio.providers.registry import register_provider
from vixio.utils.audio import mp3_to_pcm, MP3_AVAILABLE


@register_provider("edge-tts-remote")
class EdgeTTSProvider(TTSProvider):
    """
    Edge TTS provider implementation.
    
    Uses Microsoft Edge TTS for speech synthesis.
    """
    
    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service"""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """TTS is stateless - each request is independent"""
        return False
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "tts"
    
    @property
    def sample_rate(self) -> int:
        """Output audio sample rate (fixed at 16kHz for Edge TTS)."""
        return 16000
    
    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz"
    ):
        """
        Initialize Edge TTS provider.
        
        Args:
            voice: Voice name (e.g., "zh-CN-XiaoxiaoNeural")
            rate: Speech rate (e.g., "+0%", "+20%", "-10%")
            volume: Speech volume (e.g., "+0%", "+20%", "-10%")
            pitch: Speech pitch (e.g., "+0Hz", "+5Hz", "-5Hz")
        """
        # Use registered name from decorator
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        
        # Import edge_tts
        try:
            import edge_tts
            self.edge_tts = edge_tts
            self.logger.info(f"Initialized Edge TTS with voice={voice}")
        except Exception as e:
            self.logger.error(f"Failed to import edge_tts: {e}")
            raise
        
        # Cancellation flag and current task
        self._cancelled = False
        self._current_task = None
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "voice": {
                "type": "string",
                "default": "zh-CN-XiaoxiaoNeural",
                "description": "Voice name (e.g., zh-CN-XiaoxiaoNeural)"
            },
            "rate": {
                "type": "string",
                "default": "+0%",
                "description": "Speech rate (e.g., +0%, +20%, -10%)"
            },
            "volume": {
                "type": "string",
                "default": "+0%",
                "description": "Speech volume (e.g., +0%, +20%, -10%)"
            },
            "pitch": {
                "type": "string",
                "default": "+0Hz",
                "description": "Speech pitch (e.g., +0Hz, +5Hz, -5Hz)"
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize Edge TTS provider.
        
        Edge TTS doesn't require initialization, but we implement this
        to satisfy the BaseProvider interface.
        """
        self.logger.debug("Edge TTS provider initialized (no-op)")
    
    async def cleanup(self) -> None:
        """
        Cleanup Edge TTS provider resources.
        
        Cancel any ongoing synthesis and cleanup resources.
        """
        self.cancel()
        self.logger.debug("Edge TTS provider cleaned up")
    
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize text to audio.
        
        Important: Returns complete PCM audio data for entire sentence (16-bit signed, little-endian, 16kHz, mono).
        EdgeTTS returns MP3, which is converted to PCM internally.
        
        Unlike xiaozhi-server which returns chunked data, this returns the entire sentence audio at once.
        Transport layer is responsible for flow control when sending to client.
        
        Args:
            text: Text to synthesize
            
        Yields:
            Complete PCM audio bytes for the sentence
        """
        if not text or not text.strip():
            self.logger.warning("Empty text provided for TTS")
            return
        
        if not MP3_AVAILABLE:
            self.logger.error("MP3 conversion not available, cannot use Edge TTS")
            return
        
        self._cancelled = False
        
        try:
            self.logger.info(f"TTS synthesizing: '{text[:50]}...'")
            
            # Create communicator
            communicate = self.edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch
            )
            
            # Wrap stream generation in a task for immediate cancellation
            async def generate_mp3_stream():
                """Generate MP3 chunks from Edge TTS stream"""
                mp3_chunks = []
                async for chunk in communicate.stream():
                    if self._cancelled:
                        self.logger.info("TTS synthesis cancelled during streaming")
                        break
                    
                    if chunk["type"] == "audio":
                        audio_data = chunk["data"]
                        if audio_data:
                            mp3_chunks.append(audio_data)
                
                return mp3_chunks
            
            # Create task for stream generation (can be cancelled immediately)
            self._current_task = asyncio.create_task(generate_mp3_stream())
            
            try:
                # Wait for stream generation to complete (or be cancelled)
                mp3_chunks = await self._current_task
            except asyncio.CancelledError:
                self.logger.info("TTS synthesis task cancelled")
                return
            finally:
                self._current_task = None
            
            # Convert accumulated MP3 to PCM and return complete sentence audio
            if mp3_chunks and not self._cancelled:
                mp3_data = b''.join(mp3_chunks)
                self.logger.debug(f"Converting {len(mp3_data)} bytes of MP3 to PCM")
                
                # Convert MP3 to PCM
                pcm_data = mp3_to_pcm(mp3_data, sample_rate=16000, channels=1)
                
                # Yield complete sentence audio (not chunked)
                # Transport layer will handle flow control and chunking for transmission
                if pcm_data:
                    self.logger.debug(f"Yielding complete sentence audio: {len(pcm_data)} bytes of PCM")
                    yield pcm_data
        
        except asyncio.CancelledError:
            self.logger.info("TTS synthesis cancelled")
            raise
        except Exception as e:
            self.logger.error(f"Error in TTS synthesis: {e}", exc_info=True)
    
    def cancel(self) -> None:
        """
        Cancel ongoing synthesis immediately.
        
        This will:
        1. Set cancellation flag
        2. Cancel current task (if any) to interrupt network IO immediately
        """
        self._cancelled = True
        
        # Cancel current task to interrupt immediately (don't wait for next network chunk)
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            self.logger.info("TTS synthesis task cancelled immediately")
        else:
            self.logger.debug("TTS synthesis cancellation flag set")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "voice": self.voice,
            "rate": self.rate,
            "volume": self.volume,
            "pitch": self.pitch,
        })
        return config
