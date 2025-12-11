"""
VADStation - Voice Activity Detection

Input: AUDIO_RAW (PCM audio)
Output: 
  - EVENT_VAD_START when voice starts
  - Merged AUDIO_RAW + EVENT_VAD_END when voice ends

Data Flow:
- VAD detects voice activity in audio stream
- Buffers audio during speech
- On VAD_END, outputs merged audio chunk + VAD_END event
- TurnDetector receives merged audio and decides if turn is complete

Note: This station expects PCM audio data. Transport layers are responsible
for format conversion (e.g., Opus -> PCM) before chunks enter the pipeline.
"""

from typing import AsyncIterator, List
from vixio.core.station import DetectorStation
from vixio.core.chunk import Chunk, ChunkType, EventChunk, AudioChunk
from vixio.core.middleware import with_middlewares
from vixio.providers.vad import VADProvider, VADEvent


@with_middlewares(
    # Note: DetectorStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - ErrorHandlerMiddleware (error handling)
)
class VADStation(DetectorStation):
    """
    VAD workstation: Detects voice activity and outputs buffered audio segments.
    
    Input: AUDIO_RAW (PCM format, streaming)
    Output: EVENT_VAD_START + (Merged AUDIO_RAW + EVENT_VAD_END) per speech segment
    
    Data Flow:
    - Receives streaming audio chunks
    - Detects voice activity using VAD provider
    - Buffers audio during speech
    - On VAD_END, outputs merged audio + VAD_END event
    - TurnDetector/SmartTurn downstream decides if turn is complete
    
    Completion Contract:
    - EMITS_COMPLETION = False (TurnDetector decides when turn ends)
    - VAD only emits VAD_START/END events and buffered audio
    """
    
    # DetectorStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    
    # VAD does not emit completion - TurnDetector handles that
    EMITS_COMPLETION = False
    AWAITS_COMPLETION = False
    
    def __init__(
        self, 
        vad_provider: VADProvider, 
        name: str = "vad"
    ):
        """
        Initialize VAD station.
        
        Args:
            vad_provider: VAD provider instance
            name: Station name
        """
        super().__init__(name=name)
        self.vad = vad_provider
        self._is_speaking = False
        self._audio_buffer: List[AudioChunk] = []
        
        self.logger.info("VAD initialized (buffer mode, outputs merged audio on VAD_END)")
    
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
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
        """
        # Reset VAD state on interrupt
        if self._is_speaking:
            await self.vad.detect(b'', VADEvent.END)
        self._is_speaking = False
        await self.vad.reset()
        
        # Clear audio buffer
        if self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks on interrupt")
            self._audio_buffer.clear()
        
        self.logger.debug("VAD state reset by interrupt")
    
    async def reset_state(self) -> None:
        """Reset VAD state for new turn."""
        await super().reset_state()
        self._is_speaking = False
        
        # Clear audio buffer
        if self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks on state reset")
            self._audio_buffer.clear()
        
        self.logger.debug("VAD state reset")
    
    async def cleanup(self) -> None:
        """
        Cleanup VAD resources.
        
        Releases VAD provider resources to free memory.
        """
        try:
            self._audio_buffer.clear()
            
            if self.vad and hasattr(self.vad, 'cleanup'):
                await self.vad.cleanup()
                self.logger.debug("VAD provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up VAD provider: {e}")
    
    def _merge_audio_buffer(self, session_id: str, turn_id: int) -> AudioChunk:
        """
        Merge all buffered audio into a single AudioChunk.
        
        Returns:
            Merged AudioChunk with all buffered audio data
        """
        if not self._audio_buffer:
            return None
        
        # Merge audio data
        merged_audio = b''.join(chunk.data for chunk in self._audio_buffer)
        sample_rate = self._audio_buffer[0].sample_rate if self._audio_buffer else 16000
        channels = self._audio_buffer[0].channels if self._audio_buffer else 1
        
        return AudioChunk(
            type=ChunkType.AUDIO_RAW,
            data=merged_audio,
            source=self.name,
            session_id=session_id,
            turn_id=turn_id,
            sample_rate=sample_rate,
            channels=channels
        )
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process audio chunk through VAD.
        
        Logic:
        - Detect voice activity in AUDIO_RAW chunks
        - Buffer audio during speech
        - On VAD_START: emit EVENT_VAD_START
        - On VAD_END: emit merged AUDIO_RAW + EVENT_VAD_END
        
        TurnDetector downstream will receive:
        - EVENT_VAD_START (can update state)
        - AUDIO_RAW (merged buffer from this speech segment)
        - EVENT_VAD_END (can decide if turn is complete)
        """
        # Only process audio data (PCM)
        if chunk.type != ChunkType.AUDIO_RAW:
            return
            yield  # Makes this an async generator
        
        # Detect voice activity
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        has_voice = await self.vad.detect(audio_data, VADEvent.CHUNK)
        
        # Buffer audio while speaking
        if self._is_speaking:
            self._audio_buffer.append(chunk)
        
        # State change: silence -> voice
        if has_voice and not self._is_speaking:
            await self.vad.detect(b'', VADEvent.START)
            self._is_speaking = True
            
            # Start buffering with this chunk
            self._audio_buffer.append(chunk)
            
            self.logger.info("Voice activity started")
            yield EventChunk(
                type=ChunkType.EVENT_VAD_START,
                event_data={"has_voice": True},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
        
        # State change: voice -> silence
        elif not has_voice and self._is_speaking:
            await self.vad.detect(b'', VADEvent.END)
            self._is_speaking = False
            
            # Output merged audio
            merged_chunk = self._merge_audio_buffer(chunk.session_id, chunk.turn_id)
            if merged_chunk:
                self.logger.info(f"Voice ended, outputting {len(merged_chunk.data)} bytes merged audio")
                yield merged_chunk
            
            # Clear buffer
            self._audio_buffer.clear()
            
            # Emit VAD_END event (TurnDetector uses this to decide turn completion)
            yield EventChunk(
                type=ChunkType.EVENT_VAD_END,
                event_data={"has_voice": False},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
