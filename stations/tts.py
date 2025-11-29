"""
TTSStation - Text to Speech

Input: TEXT (complete sentences, source="agent")
Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP

Note: This station only processes TEXT chunks with source="agent".
Use SentenceSplitterStation before this to convert TEXT_DELTA to TEXT.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator
from core.station import Station
from core.chunk import Chunk, ChunkType, AudioChunk, EventChunk
from core.middleware import with_middlewares
from stations.middlewares import (
    MultiSignalHandlerMiddleware,
    InputValidatorMiddleware,
    InterruptDetectorMiddleware,
    LatencyMonitorMiddleware,
    ErrorHandlerMiddleware
)
from providers.tts import TTSProvider


@with_middlewares(
    # Handle multiple signals (CONTROL_INTERRUPT, EVENT_AGENT_STOP)
    MultiSignalHandlerMiddleware(
        signal_handlers={},  # Will be configured in __init__
        passthrough_signals=True
    ),
    # Validate input (only TEXT from agent, non-empty)
    InputValidatorMiddleware(
        allowed_types=[ChunkType.TEXT],
        check_empty=True,
        required_source="agent",
        passthrough_on_invalid=True
    ),
    # Detect interrupts during synthesis (turn ID changes)
    InterruptDetectorMiddleware(check_interval=5),
    # Monitor first audio latency
    LatencyMonitorMiddleware(
        record_first_token=True,
        metric_name="tts_first_audio_ready"
    ),
    # Handle errors
    ErrorHandlerMiddleware(
        emit_error_event=True,
        suppress_errors=False
    )
)
class TTSStation(Station):
    """
    TTS workstation: Synthesizes text to audio.
    
    Input: TEXT (complete sentences, source="agent")
    Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP
    
    Note: Only processes TEXT chunks with source="agent".
    Use SentenceSplitterStation to convert TEXT_DELTA to TEXT.
    """
    
    def __init__(self, tts_provider: TTSProvider, name: str = "TTS"):
        """
        Initialize TTS station.
        
        Args:
            tts_provider: TTS provider instance
            name: Station name
        """
        super().__init__(name=name)
        self.tts = tts_provider
        self._is_speaking = False
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Configure signal handlers for TTS-specific signals.
        """
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'MultiSignalHandlerMiddleware':
                # Configure signal handlers
                middleware.signal_handlers = {
                    ChunkType.CONTROL_INTERRUPT: self._handle_interrupt,
                    ChunkType.EVENT_AGENT_STOP: self._handle_agent_stop
                }
    
    async def _handle_interrupt(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Handle CONTROL_INTERRUPT signal.
        
        Cancel TTS synthesis and emit TTS_STOP event.
        """
        if self._is_speaking:
            self.tts.cancel()
            
            yield EventChunk(
                type=ChunkType.EVENT_TTS_STOP,
                event_data={"reason": "user_interrupt"},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            
            self._is_speaking = False
            self.logger.info("TTS cancelled by interrupt")
    
    async def _handle_agent_stop(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Handle EVENT_AGENT_STOP signal.
        
        Emit TTS_STOP event to mark end of turn.
        """
        if self._is_speaking:
            yield EventChunk(
                type=ChunkType.EVENT_TTS_STOP,
                event_data={"reason": "agent_complete"},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            
            self._is_speaking = False
            self.logger.info("TTS session complete (agent stopped)")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through TTS - CORE LOGIC ONLY.
        
        Middlewares handle:
        - MultiSignalHandlerMiddleware: handles CONTROL_INTERRUPT, EVENT_AGENT_STOP
        - InputValidatorMiddleware: validates TEXT from agent, non-empty
        - InterruptDetectorMiddleware: detects turn_id changes during synthesis
        - LatencyMonitorMiddleware: records first audio
        - ErrorHandlerMiddleware: handles exceptions
        
        Core logic:
        - Synthesize text to audio
        - Emit TTS events (START, SENTENCE_START)
        - Stream audio chunks
        
        Note: All signal handling is done by MultiSignalHandlerMiddleware
        """
        # Handle other signals (passthrough)
        if chunk.is_signal():
            yield chunk
            return
        
        # Data processing - CORE BUSINESS LOGIC
        text = chunk.content if hasattr(chunk, 'content') else str(chunk.data)
        
        self.logger.info(f"TTS synthesizing: '{text[:50]}...'")
        
        # Emit TTS START event (first time only)
        if not self._is_speaking:
            yield EventChunk(
                type=ChunkType.EVENT_TTS_START,
                event_data={"text_length": len(text)},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            self._is_speaking = True
        
        # Emit SENTENCE_START event (for client display)
        yield EventChunk(
            type=ChunkType.EVENT_TTS_SENTENCE_START,
            event_data={"text": text},
            source=self.name,
            session_id=chunk.session_id,
            turn_id=chunk.turn_id
        )
        
        # Synthesize text to audio
        # Note: InterruptDetectorMiddleware detects turn_id changes
        # Note: LatencyMonitorMiddleware records first audio automatically
        # Note: ErrorHandlerMiddleware catches exceptions
        audio_count = 0
        async for audio_data in self.tts.synthesize(text):
            if audio_data:
                audio_count += 1
                
                yield AudioChunk(
                    type=ChunkType.AUDIO_RAW,
                    data=audio_data,
                    sample_rate=16000,
                    channels=1,
                    source=self.name,
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
        
        self.logger.debug(f"TTS generated {audio_count} audio chunks")
        
        # Passthrough original text chunk
        yield chunk
