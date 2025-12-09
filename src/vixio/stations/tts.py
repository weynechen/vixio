"""
TTSStation - Text to Speech

Input: TEXT (complete sentences, source="SentenceAggregator")
Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP

Note: This station only processes TEXT chunks from SentenceAggregator.
Use SentenceAggregatorStation before this to convert TEXT_DELTA to TEXT.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, AudioChunk, EventChunk
from vixio.core.middleware import with_middlewares
from vixio.stations.middlewares import (
    MultiSignalHandlerMiddleware,
    InputValidatorMiddleware
)
from vixio.providers.tts import TTSProvider


@with_middlewares(
    # Handle multiple signals (CONTROL_INTERRUPT, EVENT_AGENT_STOP)
    MultiSignalHandlerMiddleware(
        signal_handlers={},  # Will be configured in _configure_middlewares_hook
        passthrough_signals=False  # DAG handles routing, no passthrough
    ),
    # Validate input (only TEXT, non-empty)
    # Note: No source check - DAG routing ensures correct data flow
    InputValidatorMiddleware(
        allowed_types=[ChunkType.TEXT],
        check_empty=True,
        passthrough_on_invalid=False  # DAG handles routing
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
    
    Input: TEXT (complete sentences, source="SentenceAggregator")
    Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP
    
    Note: Only processes TEXT chunks from SentenceAggregator.
    Use SentenceAggregatorStation to convert TEXT_DELTA to TEXT.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    LATENCY_METRIC_NAME = "tts_first_audio_ready"
    LATENCY_OUTPUT_TYPES = [ChunkType.AUDIO_RAW]  # Only monitor audio output, not events
    
    def __init__(self, tts_provider: TTSProvider, name: str = "tts"):  # Lowercase for consistent source tracking
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
        
        Cancel TTS synthesis and reset state.
        Note: TTS_STOP is NOT emitted here - OutputStation handles client notification via CONTROL_ABORT.
        """
        if self._is_speaking:
            self.tts.cancel()
            self._is_speaking = False
            self.logger.info("TTS cancelled by interrupt")
            
    async def _handle_agent_stop(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Handle EVENT_AGENT_STOP signal.
        
        Emit TTS_STOP to signal TTS session complete.
        
        Note: Due to sequential chunk processing, when EVENT_AGENT_STOP arrives:
        - All TEXT chunks have been processed (they come before EVENT_AGENT_STOP)
        - TTS synthesis for each TEXT is complete (process_chunk runs to completion)
        - So TTS_STOP here correctly indicates "TTS has finished all synthesis"
        
        Edge case: if no TEXT was processed (_is_speaking=False), no TTS_STOP needed.
        """
        if not self._is_speaking:
            # No TTS session active, nothing to stop
            self.logger.debug("Agent stopped but TTS never started, no TTS_STOP needed")
            return
            yield  # Keep as generator
        
        # Emit TTS_STOP - all TEXT has been processed at this point
        yield EventChunk(
            type=ChunkType.EVENT_TTS_STOP,
            event_data={"reason": "synthesis_complete"},
            source=self.name,
            session_id=chunk.session_id,
            turn_id=chunk.turn_id
        )
        
        self._is_speaking = False
        self.logger.info("TTS session complete (all sentences synthesized)")
    
    async def reset_state(self) -> None:
        """Reset TTS state for new turn."""
        self._is_speaking = False
        self.logger.debug("TTS state reset for new turn")
            
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through TTS - CORE LOGIC ONLY.
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (TEXT)
        - Do NOT passthrough - DAG handles routing to downstream nodes
        - Output: AUDIO_RAW + TTS events
        
        Core logic:
        - Synthesize text to audio
        - Emit TTS events (START, SENTENCE_START)
        - Stream audio chunks
        
        Note: Middlewares handle signal processing, validation, interrupt detection,
        latency monitoring, and error handling.
        """
        # Only process TEXT chunks
        if chunk.type != ChunkType.TEXT:
            return
        
        # Extract text from data attribute
        text = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
        
        if not text:
            return
        
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
        
        self.logger.info(f"TTS generated {audio_count} audio chunks")
