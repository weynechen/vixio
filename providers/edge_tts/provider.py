"""
Edge TTS provider implementation
"""

import asyncio
from typing import AsyncIterator, Dict, Any
from providers.tts import TTSProvider


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
        Synthesize text to audio (streaming).
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio bytes (MP3 encoded)
        """
        if not text or not text.strip():
            self.logger.warning("Empty text provided for TTS")
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
            
            # Stream audio
            async for chunk in communicate.stream():
                if self._cancelled:
                    self.logger.info("TTS synthesis cancelled")
                    break
                
                if chunk["type"] == "audio":
                    audio_data = chunk["data"]
                    if audio_data:
                        yield audio_data
        
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
