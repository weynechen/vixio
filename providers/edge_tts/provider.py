"""
Edge TTS provider implementation
"""

import asyncio
from typing import AsyncIterator, Dict, Any
from providers.tts import TTSProvider
from utils.audio import mp3_to_pcm, MP3_AVAILABLE


class EdgeTTSProvider(TTSProvider):
    """
    Edge TTS provider implementation.
    
    Uses Microsoft Edge TTS for speech synthesis.
    """
    
    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        name: str = "EdgeTTS"
    ):
        """
        Initialize Edge TTS provider.
        
        Args:
            voice: Voice name (e.g., "zh-CN-XiaoxiaoNeural")
            rate: Speech rate (e.g., "+0%", "+20%", "-10%")
            volume: Speech volume (e.g., "+0%", "+20%", "-10%")
            pitch: Speech pitch (e.g., "+0Hz", "+5Hz", "-5Hz")
            name: Provider name
        """
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
        
        # Cancellation flag
        self._cancelled = False
    
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize text to audio.
        
        Important: Returns PCM audio data (16-bit signed, little-endian, 16kHz, mono).
        EdgeTTS returns MP3, which is converted to PCM internally.
        
        Args:
            text: Text to synthesize
            
        Yields:
            PCM audio bytes
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
            
            # Accumulate MP3 chunks (Edge TTS streams MP3 fragments, not complete frames)
            mp3_chunks = []
            async for chunk in communicate.stream():
                if self._cancelled:
                    self.logger.info("TTS synthesis cancelled")
                    break
                
                if chunk["type"] == "audio":
                    audio_data = chunk["data"]
                    if audio_data:
                        mp3_chunks.append(audio_data)
            
            # Convert accumulated MP3 to PCM
            if mp3_chunks and not self._cancelled:
                mp3_data = b''.join(mp3_chunks)
                self.logger.debug(f"Converting {len(mp3_data)} bytes of MP3 to PCM")
                
                # Convert MP3 to PCM
                pcm_data = mp3_to_pcm(mp3_data, sample_rate=16000, channels=1)
                
                # Split PCM into chunks for streaming (60ms frames = 960 samples = 1920 bytes)
                chunk_size = 1920  # 60ms at 16kHz mono
                for i in range(0, len(pcm_data), chunk_size):
                    if self._cancelled:
                        break
                    yield pcm_data[i:i + chunk_size]
                
                self.logger.debug(f"Converted to {len(pcm_data)} bytes of PCM")
        
        except Exception as e:
            self.logger.error(f"Error in TTS synthesis: {e}", exc_info=True)
    
    def cancel(self) -> None:
        """Cancel ongoing synthesis"""
        self._cancelled = True
        self.logger.debug("TTS synthesis cancelled")
    
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
