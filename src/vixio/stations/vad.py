"""
VADStation - Voice Activity Detection

Input: AUDIO_DELTA (streaming PCM audio fragments)
Output: 
  - AUDIO_DELTA (passthrough with VAD state)
  - AUDIO (merged audio when voice ends)
  - EVENT_VAD_START when voice starts
  - EVENT_VAD_END when voice ends

Data Flow:
- Receives continuous AUDIO_DELTA from Transport
- Detects voice activity using VAD provider
- Passthrough AUDIO_DELTA (with VAD state in metadata)
- Buffers audio during speech
- On VAD_END, outputs merged AUDIO + VAD_END event
- TurnDetector receives AUDIO and decides if turn is complete

Note: This station expects PCM audio data. Transport layers are responsible
for format conversion (e.g., Opus -> PCM) before chunks enter the pipeline.
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import List, cast, Optional
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
    VAD workstation: Detects voice activity in streaming audio.
    
    Input: AUDIO_DELTA (streaming PCM fragments)
    Output: 
      - AUDIO_DELTA (passthrough with VAD state)
      - AUDIO (merged segments when voice ends)
      - EVENT_VAD_START/END
    
    Data Flow:
    - Receives continuous AUDIO_DELTA from Transport
    - Detects voice activity using VAD provider
    - Passthrough AUDIO_DELTA (with VAD state metadata)
    - Buffers audio during speech
    - On VAD_END, outputs merged AUDIO + VAD_END event
    - TurnDetector/SmartTurn downstream receives AUDIO
    
    Completion Contract:
    - EMITS_COMPLETION = False (TurnDetector decides when turn ends)
    - VAD only emits VAD_START/END events and buffered audio
    """
    
    # DetectorStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_DELTA, ChunkType.AUDIO]  # Support both for compatibility
    
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
    
    def _merge_audio_buffer(self, session_id: str, turn_id: int) -> Optional[AudioChunk]:
        """
        Merge all buffered audio into a single AudioChunk.
        
        Returns:
            Merged AudioChunk (AUDIO) with all buffered audio data, or None if buffer is empty
        """
        if not self._audio_buffer:
            return None
        
        # Merge audio data
        merged_audio = b''.join(chunk.data for chunk in self._audio_buffer)
        sample_rate = self._audio_buffer[0].sample_rate if self._audio_buffer else 16000
        channels = self._audio_buffer[0].channels if self._audio_buffer else 1
        
        return AudioChunk(
            type=ChunkType.AUDIO,  # Output complete audio segment
            data=merged_audio,
            source=self.name,
            session_id=session_id,
            turn_id=turn_id,
            sample_rate=sample_rate,
            channels=channels
        )
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process audio chunk through VAD.
        
        Logic:
        - Receive AUDIO_DELTA (streaming fragments)
        - Detect voice activity
        - Passthrough AUDIO_DELTA (with VAD state metadata)
        - Buffer audio during speech
        - On VAD_START: emit EVENT_VAD_START
        - On VAD_END: emit merged AUDIO + EVENT_VAD_END
        
        TurnDetector downstream will receive:
        - AUDIO_DELTA (passthrough, can be used for streaming ASR)
        - EVENT_VAD_START (can update state)
        - AUDIO (merged buffer from this speech segment)
        - EVENT_VAD_END (can decide if turn is complete)
        """
        # Only process audio data (PCM)
        if chunk.type not in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO):
            return
            yield  # Makes this an async generator
        
        
        # Detect voice activity
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        has_voice = await self.vad.detect(audio_data, VADEvent.CHUNK)
        
        # Passthrough AUDIO_DELTA with VAD state metadata
        # This allows downstream StreamingASR to receive continuous audio
        passthrough_chunk = AudioChunk(
            type=ChunkType.AUDIO_DELTA,
            data=chunk.data,
            source=self.name,
            session_id=chunk.session_id,
            turn_id=chunk.turn_id,
            sample_rate=getattr(chunk, 'sample_rate', 16000),
            channels=getattr(chunk, 'channels', 1),
            metadata={
                **chunk.metadata,
                "vad_state": "speaking" if has_voice else "silence",
                "is_speaking": self._is_speaking
            }
        )
        yield passthrough_chunk
        
        # Buffer audio while speaking (for AUDIO output)
        if self._is_speaking:
            # Type assertion: chunk is AudioChunk (validated by ALLOWED_INPUT_TYPES)
            self._audio_buffer.append(cast(AudioChunk, chunk))
        
        # State change: silence -> voice
        if has_voice and not self._is_speaking:
            await self.vad.detect(b'', VADEvent.START)
            self._is_speaking = True
            
            # Start buffering with this chunk
            # Type assertion: chunk is AudioChunk (validated by ALLOWED_INPUT_TYPES)
            self._audio_buffer.append(cast(AudioChunk, chunk))
            
            self.logger.info("Voice activity started")
            
            # Cancel turn timeout if configured
            if self.control_bus:
                self.control_bus.cancel_turn_timeout()
            
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
