"""
TTSStation - Text to Speech

Input: TEXT (complete sentences, source="agent")
Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP

Note: This station only processes TEXT chunks with source="agent".
Use SentenceAggregatorStation before this to convert TEXT_DELTA to TEXT.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator
from core.station import StreamStation
from core.chunk import Chunk, ChunkType, AudioChunk, EventChunk
from core.middleware import with_middlewares
from stations.middlewares import (
    MultiSignalHandlerMiddleware,
    InputValidatorMiddleware
)
from providers.tts import TTSProvider


@with_middlewares(
    # Handle multiple signals (CONTROL_INTERRUPT, EVENT_AGENT_STOP)
    MultiSignalHandlerMiddleware(
        signal_handlers={},  # Will be configured in _configure_middlewares_hook
        passthrough_signals=True
    ),
    # Validate input (only TEXT from agent, non-empty)
    # Note: Overrides default InputValidator to add required_source="agent"
    InputValidatorMiddleware(
        allowed_types=[ChunkType.TEXT],
        check_empty=True,
        required_source="agent",
        passthrough_on_invalid=True
    )
    # Note: StreamStation base class automatically provides:
    # - SignalHandlerMiddleware (handles CONTROL_INTERRUPT)
    # - InterruptDetectorMiddleware (detects turn_id changes)
    # - LatencyMonitorMiddleware (uses LATENCY_METRIC_NAME)
    # - ErrorHandlerMiddleware (error handling)
)
class TTSStation(StreamStation):
    """
    TTS workstation: Synthesizes text to audio.
    
    Input: TEXT (complete sentences, source="agent")
    Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP
    
    Note: Only processes TEXT chunks with source="agent".
    Use SentenceAggregatorStation to convert TEXT_DELTA to TEXT.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    LATENCY_METRIC_NAME = "tts_first_audio_ready"
    
    def __init__(self, tts_provider: TTSProvider, name: str = "TTS"):
        """
        Initialize TTS station.
        
        Args:
            tts_provider: TTS provider instance
            name: Station name
        """
        super().__init__(
            name=name,
            enable_interrupt_detection=True  # TTS needs interrupt detection during synthesis
        )
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
        # Extract text from data attribute (unified API)
        text = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
        
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
