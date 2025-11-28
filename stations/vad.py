"""
VADStation - Voice Activity Detection

Input: AUDIO_RAW (PCM audio)
Output: AUDIO_RAW (passthrough) + EVENT_VAD_START/END

Note: This station expects PCM audio data. Transport layers are responsible
for format conversion (e.g., Opus -> PCM) before chunks enter the pipeline.
"""

from typing import AsyncIterator
from core.station import Station
from core.chunk import Chunk, ChunkType, AudioChunk, EventChunk, is_audio_chunk
from providers.vad import VADProvider, VADEvent


class VADStation(Station):
    """
    VAD workstation: Detects voice activity in PCM audio stream.
    
    Input: AUDIO_RAW (PCM format)
    Output: AUDIO_RAW (passthrough) + EVENT_VAD_START/END
    
    Note: Expects PCM audio data. Transport layers handle format conversion.
    Turn management is handled by TTS/TurnDetector stations (increment on completion/interrupt).
    """
    
    def __init__(self, vad_provider: VADProvider, name: str = "VAD"):
        """
        Initialize VAD station.
        
        Args:
            vad_provider: VAD provider instance
            name: Station name
        """
        super().__init__(name=name)
        self.vad = vad_provider
        self._is_speaking = False
    
    async def cleanup(self) -> None:
        """
        Cleanup VAD resources.
        
        Releases VAD provider resources to free memory.
        """
        try:
            if self.vad and hasattr(self.vad, 'cleanup'):
                await self.vad.cleanup()
                self.logger.debug("VAD provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up VAD provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through VAD.
        
        For audio chunks:
        - Detect voice activity
        - Send VAD events (START/CHUNK/END) to provider
        - Emit VAD events on state change
        - Passthrough audio chunk
        
        For signal chunks:
        - Handle CONTROL_INTERRUPT to reset state
        - Passthrough signal
        """
        # Handle signals
        if chunk.is_signal():
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                # Reset VAD state on interrupt
                if self._is_speaking:
                    # Send END event if VAD was active
                    await self.vad.detect(b'', VADEvent.END)
                self._is_speaking = False
                await self.vad.reset()
                self.logger.debug("VAD state reset by interrupt")
            # Always passthrough signals
            yield chunk
            return
        
        # Only process audio data (PCM)
        if not is_audio_chunk(chunk) or chunk.type != ChunkType.AUDIO_RAW:
            yield chunk
            return
        
        # Prepare audio data
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        
        # Detect voice activity with appropriate event
        has_voice = await self.vad.detect(audio_data, VADEvent.CHUNK)
        
        # Emit VAD events on state change
        if has_voice and not self._is_speaking:
            # Voice activity started: send START event
            await self.vad.detect(b'', VADEvent.START)
            self._is_speaking = True
            
            self.logger.info("Voice activity started")
            yield EventChunk(
                type=ChunkType.EVENT_VAD_START,
                event_data={"has_voice": True},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
        
        elif not has_voice and self._is_speaking:
            # Voice activity ended: send END event
            await self.vad.detect(b'', VADEvent.END)
            self._is_speaking = False
            
            self.logger.info("Voice activity ended")
            yield EventChunk(
                type=ChunkType.EVENT_VAD_END,
                event_data={"has_voice": False},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
        
        # Always passthrough audio
        yield chunk
