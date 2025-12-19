"""
TTSStation - Text to Speech

Input: TEXT (complete sentences), EVENT_STREAM_COMPLETE (trigger TTS_STOP from SentenceAggregator)
Output: AUDIO_RAW (streaming) + EVENT_TTS_START/SENTENCE_START/STOP

Completion Contract:
- AWAITS_COMPLETION: True (completion signal triggers TTS_STOP event)
- EMITS_COMPLETION: True (emits completion after all synthesis done)

Note: This station only processes TEXT chunks from SentenceAggregator.
Use SentenceAggregatorStation before this to convert TEXT_DELTA to TEXT.

Refactored with middleware pattern for clean separation of concerns.
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, AudioChunk, EventChunk
from vixio.core.middleware import with_middlewares
from vixio.stations.middlewares import (
    InputValidatorMiddleware,
    TimeoutHandlerMiddleware
)
from vixio.providers.tts import TTSProvider


@with_middlewares(
    # Timeout handler for TTS processing
    TimeoutHandlerMiddleware(
        timeout_seconds=15.0,  # Will be overridden in __init__
        emit_timeout_event=True,
        send_interrupt_signal=True
    ),
    # Validate input (only TEXT, non-empty)
    # Note: No source check - DAG routing ensures correct data flow
    InputValidatorMiddleware(
        allowed_types=[ChunkType.TEXT],
        check_empty=True,
        passthrough_on_invalid=False  # DAG handles routing
    )
    # Note: StreamStation base class automatically provides:
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - InterruptDetectorMiddleware (detects turn_id changes)
    # - LatencyMonitorMiddleware (uses LATENCY_METRIC_NAME)
    # - ErrorHandlerMiddleware (error handling)
)
class TTSStation(StreamStation):
    """
    TTS workstation: Synthesizes text to audio.
    
    Input: TEXT (complete sentences), EVENT_STREAM_COMPLETE (triggers TTS_STOP)
    Output: AUDIO_RAW (streaming) + EVENT_TTS_START/SENTENCE_START/STOP
    
    Completion Contract:
    - Awaits completion from SentenceAggregator (triggers TTS_STOP)
    - Emits completion after all synthesis done
    
    Note: Only processes TEXT chunks from SentenceAggregator.
    Use SentenceAggregatorStation to convert TEXT_DELTA to TEXT.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    LATENCY_METRIC_NAME = "tts_first_audio_ready"
    LATENCY_OUTPUT_TYPES = [ChunkType.AUDIO_RAW]  # Only monitor audio output, not events
    
    # Completion contract: await sentence completion for TTS_STOP, emit completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = True
    
    def __init__(
        self, 
        tts_provider: TTSProvider, 
        timeout_seconds: Optional[float] = 15.0,
        name: str = "tts"
    ): 
        """
        Initialize TTS station.
        
        Args:
            tts_provider: TTS provider instance
            timeout_seconds: Timeout for TTS processing (default: 15s, None = no timeout)
            name: Station name
        """
        super().__init__(
            name=name,
            output_role="bot",
            enable_interrupt_detection=True  # TTS needs interrupt detection during synthesis
        )
        self.tts = tts_provider
        self.timeout_seconds = timeout_seconds
        self._is_speaking = False
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Configure signal handlers for TTS-specific signals.
        """
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'TimeoutHandlerMiddleware':
                middleware.timeout_seconds = self.timeout_seconds
            elif middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle CONTROL_STATE_RESET signal.
        
        Cancel TTS synthesis and reset state.
        Note: TTS_STOP is NOT emitted here - OutputStation handles client notification via CONTROL_TURN_SWITCH.
        """
        self.logger.info(f"TTS _handle_interrupt called, _is_speaking={self._is_speaking}")
        if self._is_speaking:
            self.tts.cancel()
            self._is_speaking = False
            self.logger.info("TTS cancelled by interrupt, _is_speaking set to False")
    
    async def reset_state(self) -> None:
        """Reset TTS state for new turn."""
        old_speaking = self._is_speaking
        self._is_speaking = False
        self.logger.info(f"TTS reset_state called, _is_speaking: {old_speaking} -> False")
    
    async def on_completion(self, event: EventChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion event from upstream (SentenceAggregator).
        
        Emits TTS_STOP event to signal TTS session complete.
        
        Note: Due to sequential chunk processing, when completion arrives:
        - All TEXT chunks have been processed (they come before completion event)
        - TTS synthesis for each TEXT is complete (process_chunk runs to completion)
        - So TTS_STOP here correctly indicates "TTS has finished all synthesis"
        
        Edge case: if no TEXT was processed (_is_speaking=False), no TTS_STOP needed.
        
        Args:
            event: EventChunk with EVENT_STREAM_COMPLETE from SentenceAggregator
            
        Yields:
            EVENT_TTS_STOP + completion event
        """
        if not self._is_speaking:
            # No TTS session active, nothing to stop
            self.logger.debug("Completion received but TTS never started, no TTS_STOP needed")
            return
            yield  # Make this an async generator
        
        # Emit TTS_STOP - all TEXT has been processed at this point
        yield EventChunk(
            type=ChunkType.EVENT_TTS_STOP,
            event_data={"reason": "synthesis_complete"},
            source=self.name,
            session_id=event.session_id,
            turn_id=event.turn_id
        )
        
        self._is_speaking = False
        self.logger.info("TTS session complete (all sentences synthesized)")
        
        # Emit completion event
        yield self.emit_completion(
            session_id=event.session_id,
            turn_id=event.turn_id
        )
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
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
            yield  # Makes this an async generator
        
        # Extract text from data attribute
        text = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
        
        if not text:
            return
            yield  # Makes this an async generator
        
        self.logger.info(f"TTS synthesizing: '{text[:50]}...'")
        
        # Emit TTS START event (first time only)
        if not self._is_speaking:
            self.logger.info(f"TTS emitting EVENT_TTS_START (turn={chunk.turn_id}), _is_speaking: False -> True")
            yield EventChunk(
                type=ChunkType.EVENT_TTS_START,
                event_data={"text_length": len(text)},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            self._is_speaking = True
        else:
            self.logger.debug(f"TTS skipping EVENT_TTS_START (already speaking, turn={chunk.turn_id})")
        
        # Emit SENTENCE_START event (for client sync)
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
