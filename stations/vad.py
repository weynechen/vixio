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
from providers.vad import VADProvider


class VADStation(Station):
    """
    VAD workstation: Detects voice activity in PCM audio stream.
    
    Input: AUDIO_RAW (PCM format)
    Output: AUDIO_RAW (passthrough) + EVENT_VAD_START/END
    
    Note: Expects PCM audio data. Transport layers handle format conversion.
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
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through VAD.
        
        For audio chunks:
        - Detect voice activity
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
                self._is_speaking = False
                self.vad.reset()
                self.logger.debug("VAD state reset by interrupt")
            # Always passthrough signals
            yield chunk
            return
        
        # Only process audio data (PCM)
        if not is_audio_chunk(chunk) or chunk.type != ChunkType.AUDIO_RAW:
            yield chunk
            return
        
        # Detect voice in PCM audio
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        has_voice = self.vad.detect(audio_data)
        
        # Emit VAD events on state change
        if has_voice and not self._is_speaking:
            # Voice activity started
            self.logger.info("Voice activity started")
            yield EventChunk(
                type=ChunkType.EVENT_VAD_START,
                event_data={"has_voice": True},
                source_station=self.name,
                session_id=chunk.session_id
            )
            self._is_speaking = True
        
        elif not has_voice and self._is_speaking:
            # Voice activity ended
            self.logger.info("Voice activity ended")
            yield EventChunk(
                type=ChunkType.EVENT_VAD_END,
                event_data={"has_voice": False},
                source_station=self.name,
                session_id=chunk.session_id
            )
            self._is_speaking = False
        
        # Always passthrough audio
        yield chunk
