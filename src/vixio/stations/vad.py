"""
VADStation - Voice Activity Detection

Input: AUDIO_RAW (PCM audio)
Output: 
  - Passthrough mode: AUDIO_RAW (realtime) + EVENT_VAD_START/END
  - Buffer mode: AUDIO_RAW (buffered, on VAD_END) + EVENT_VAD_START/END + EVENT_STREAM_COMPLETE

Modes:
- Passthrough (buffer_audio=False): Audio flows through in realtime, VAD events emitted.
  Use with TurnDetector which handles buffering and completion.
- Buffer (buffer_audio=True): Audio buffered during speech, output on VAD_END.
  Use without TurnDetector for simple VAD-triggered ASR.

Completion Contract:
- Passthrough mode: EMITS_COMPLETION = False (TurnDetector handles completion)
- Buffer mode: EMITS_COMPLETION = True (emits completion on VAD_END)

Note: This station expects PCM audio data. Transport layers are responsible
for format conversion (e.g., Opus -> PCM) before chunks enter the pipeline.

Refactored with middleware pattern for clean separation of concerns.
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
    VAD workstation: Detects voice activity in PCM audio stream.
    
    Input: AUDIO_RAW (PCM format)
    Output: 
      - Passthrough mode: AUDIO_RAW (realtime) + EVENT_VAD_START/END
      - Buffer mode: AUDIO_RAW (buffered) + EVENT_VAD_START/END + EVENT_STREAM_COMPLETE
    
    Modes:
    - Passthrough (default): Audio flows through realtime, TurnDetector buffers.
    - Buffer: Audio buffered here, output on VAD_END with completion signal.
    
    Completion Contract:
    - Passthrough: Does NOT emit completion (TurnDetector handles it)
    - Buffer: Emits EVENT_STREAM_COMPLETE on VAD_END
    
    Note: Expects PCM audio data. Transport layers handle format conversion.
    """
    
    # DetectorStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    
    # Default: passthrough mode, no completion
    # Changed dynamically based on buffer_audio setting
    EMITS_COMPLETION = False
    AWAITS_COMPLETION = False
    
    def __init__(
        self, 
        vad_provider: VADProvider, 
        buffer_audio: bool = True,
        name: str = "VAD"
    ):
        """
        Initialize VAD station.
        
        Args:
            vad_provider: VAD provider instance
            buffer_audio: If True, buffer audio and output on VAD_END with completion.
                         If False (default), passthrough audio realtime.
            name: Station name
        """
        super().__init__(name=name)
        self.vad = vad_provider
        self._is_speaking = False
        
        # Buffer mode configuration
        self.buffer_audio = buffer_audio
        self._audio_buffer: List[Chunk] = []
        
        # Update completion contract based on mode
        if buffer_audio:
            self.EMITS_COMPLETION = True
            self.logger.info("VAD running in buffer mode (emits completion on VAD_END)")
    
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
            # Send END event if VAD was active
            await self.vad.detect(b'', VADEvent.END)
        self._is_speaking = False
        await self.vad.reset()
        
        # Clear audio buffer if in buffer mode
        if self.buffer_audio and self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks on interrupt")
            self._audio_buffer.clear()
        
        self.logger.debug("VAD state reset by interrupt")
    
    async def reset_state(self) -> None:
        """Reset VAD state for new turn."""
        await super().reset_state()
        self._is_speaking = False
        
        # Clear audio buffer if in buffer mode
        if self.buffer_audio and self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks on state reset")
            self._audio_buffer.clear()
        
        self.logger.debug("VAD state reset")
    
    async def cleanup(self) -> None:
        """
        Cleanup VAD resources.
        
        Releases VAD provider resources to free memory.
        """
        try:
            # Clear audio buffer
            if self._audio_buffer:
                self._audio_buffer.clear()
            
            if self.vad and hasattr(self.vad, 'cleanup'):
                await self.vad.cleanup()
                self.logger.debug("VAD provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up VAD provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through VAD - CORE LOGIC ONLY.
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (AUDIO_RAW)
        - Output depends on mode:
          * Passthrough: AUDIO_RAW (realtime) + EVENT_VAD_START/END
          * Buffer: AUDIO_RAW (on VAD_END) + EVENT_VAD_START/END + EVENT_STREAM_COMPLETE
        
        Core logic:
        - Detect voice activity in AUDIO_RAW chunks
        - Emit EVENT_VAD_START/END on state change
        - Passthrough mode: Output audio immediately
        - Buffer mode: Buffer audio, output on VAD_END with completion
        
        Note: SignalHandlerMiddleware handles CONTROL_STATE_RESET (resets VAD via _handle_interrupt)
        """
        # Only process audio data (PCM)
        if chunk.type != ChunkType.AUDIO_RAW:
            return
            yield  # Makes this an async generator
        
        # Detect voice activity
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        has_voice = await self.vad.detect(audio_data, VADEvent.CHUNK)
        
        # Buffer audio if in buffer mode and speaking
        if self.buffer_audio and self._is_speaking:
            self._audio_buffer.append(chunk)
            self.logger.debug(f"Buffered audio chunk, total: {len(self._audio_buffer)}")
        
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
            
            # In buffer mode, start buffering (including this chunk)
            if self.buffer_audio:
                self._audio_buffer.append(chunk)
                self.logger.debug("Started buffering audio")
        
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
            
            # In buffer mode, output all buffered audio + completion
            if self.buffer_audio:
                self.logger.info(f"Outputting {len(self._audio_buffer)} buffered audio chunks")
                for audio_chunk in self._audio_buffer:
                    audio_chunk.source = self.name
                    yield audio_chunk
                
                # Clear buffer
                self._audio_buffer.clear()
                
                # Emit completion signal
                yield EventChunk(
                    type=ChunkType.EVENT_STREAM_COMPLETE,
                    source=self.name,
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id,
                    event_data={"reason": "vad_end"}
                )
                return
                yield  # Exit early in buffer mode
        
        # Passthrough mode: output audio immediately
        if not self.buffer_audio:
            chunk.source = self.name
            yield chunk
