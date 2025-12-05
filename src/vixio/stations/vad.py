"""
VADStation - Voice Activity Detection

Input: AUDIO_RAW (PCM audio)
Output: AUDIO_RAW (passthrough) + EVENT_VAD_START/END

Note: This station expects PCM audio data. Transport layers are responsible
for format conversion (e.g., Opus -> PCM) before chunks enter the pipeline.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator
from vixio.core.station import DetectorStation
from vixio.core.chunk import Chunk, ChunkType, EventChunk, is_audio_chunk
from vixio.core.middleware import with_middlewares
from vixio.providers.vad import VADProvider, VADEvent


@with_middlewares(
    # Note: DetectorStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_INTERRUPT)
    # - ErrorHandlerMiddleware (error handling)
)
class VADStation(DetectorStation):
    """
    VAD workstation: Detects voice activity in PCM audio stream.
    
    Input: AUDIO_RAW (PCM format)
    Output: AUDIO_RAW (passthrough) + EVENT_VAD_START/END
    
    Note: Expects PCM audio data. Transport layers handle format conversion.
    Turn management is handled by TTS/TurnDetector stations (increment on completion/interrupt).
    """
    
    # DetectorStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    
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
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        # Set interrupt callback to reset VAD state
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_INTERRUPT received.
        """
        # Reset VAD state on interrupt
        if self._is_speaking:
            # Send END event if VAD was active
            await self.vad.detect(b'', VADEvent.END)
        self._is_speaking = False
        await self.vad.reset()
        self.logger.debug("VAD state reset by interrupt")
    
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
        Process chunk through VAD - CORE LOGIC ONLY.
        
        Middlewares handle: signal processing (CONTROL_INTERRUPT), error handling.
        
        Core logic:
        - Detect voice activity in AUDIO_RAW chunks
        - Emit EVENT_VAD_START/END on state change
        - Passthrough all chunks
        
        Note: SignalHandlerMiddleware handles CONTROL_INTERRUPT (resets VAD via _handle_interrupt)
        """
        # Handle signals (passthrough)
        if chunk.is_signal():
            yield chunk
            return
        
        # Only process audio data (PCM)
        if not is_audio_chunk(chunk) or chunk.type != ChunkType.AUDIO_RAW:
            yield chunk
            return
        
        # Detect voice activity
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        has_voice = await self.vad.detect(audio_data, VADEvent.CHUNK)
        
        # Emit VAD events on state change
        if has_voice and not self._is_speaking:
            # Voice activity started
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
            # Voice activity ended
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
