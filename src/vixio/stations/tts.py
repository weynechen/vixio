"""
TTSStation - Text to Speech

Input: TEXT (complete sentences), EVENT_STREAM_COMPLETE (trigger TTS_STOP from SentenceAggregator)
Output: AUDIO (streaming) + EVENT_TTS_START/SENTENCE_START/STOP

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
    Output: AUDIO (streaming) + EVENT_TTS_START/SENTENCE_START/STOP
    
    Completion Contract:
    - Awaits completion from SentenceAggregator (triggers TTS_STOP)
    - Emits completion after all synthesis done
    
    Note: Only processes TEXT chunks from SentenceAggregator.
    Use SentenceAggregatorStation to convert TEXT_DELTA to TEXT.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    LATENCY_METRIC_NAME = "tts_first_audio_ready"
    LATENCY_OUTPUT_TYPES = [ChunkType.AUDIO]  # Only monitor audio output, not events
    
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
        - Output: AUDIO + TTS events
        
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
                    type=ChunkType.AUDIO,  # TTS outputs complete audio frames
                    data=audio_data,
                    sample_rate=self.tts.sample_rate,
                    channels=1,
                    source=self.name,
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
        
        self.logger.info(f"TTS generated {audio_count} audio chunks")


@with_middlewares(
    # Timeout handler for TTS processing
    TimeoutHandlerMiddleware(
        timeout_seconds=30.0,  # Longer timeout for streaming mode
        emit_timeout_event=True,
        send_interrupt_signal=True
    ),
    # Validate input (TEXT_DELTA for streaming)
    InputValidatorMiddleware(
        allowed_types=[ChunkType.TEXT_DELTA],
        check_empty=True,
        passthrough_on_invalid=False
    )
    # Note: StreamStation base class automatically provides:
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - InterruptDetectorMiddleware (detects turn_id changes)
    # - LatencyMonitorMiddleware (uses LATENCY_METRIC_NAME)
    # - ErrorHandlerMiddleware (error handling)
)
class StreamingTTSStation(StreamStation):
    """
    Streaming TTS workstation: Synthesizes streaming text to audio (server_commit mode).
    
    Input: TEXT_DELTA (streaming text from Agent)
    Output: AUDIO (streaming) + EVENT_TTS_START/STOP
    
    Mode:
    - Receives TEXT_DELTA continuously from Agent
    - Appends to TTS provider's WebSocket
    - TTS provider intelligently segments and synthesizes (server_commit mode)
    - Streams audio chunks as they become ready
    - No need for SentenceAggregator
    
    Completion Contract:
    - Awaits completion from Agent (triggers finish())
    - Emits completion after all synthesis done
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT_DELTA]
    LATENCY_METRIC_NAME = "tts_first_audio_ready"
    LATENCY_OUTPUT_TYPES = [ChunkType.AUDIO]
    
    # Completion contract: await text stream completion, emit completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = True
    
    def __init__(
        self, 
        tts_provider: TTSProvider, 
        timeout_seconds: Optional[float] = 30.0,
        name: str = "streaming_tts"
    ): 
        """
        Initialize Streaming TTS station.
        
        Args:
            tts_provider: TTS provider instance (must support streaming input)
            timeout_seconds: Timeout for TTS processing (default: 30s, None = no timeout)
            name: Station name
        """
        super().__init__(
            name=name, 
            output_role="bot", 
            enable_interrupt_detection=False  # Agent handles interrupts
        )
        self.tts = tts_provider
        self.timeout_seconds = timeout_seconds
        self._is_speaking = False
        self._text_buffer = []  # Buffer for text deltas
        
        # Check if provider supports streaming input
        if hasattr(tts_provider, 'supports_streaming_input'):
            if not tts_provider.supports_streaming_input:
                self.logger.warning(
                    "TTS provider does not support streaming input. "
                    "StreamingTTSStation may not work as expected."
                )
        
        self.logger.info("StreamingTTS initialized (server_commit mode)")
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        # Set timeout from init parameter
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'TimeoutHandlerMiddleware':
                middleware.timeout_seconds = self.timeout_seconds
                
        # Set interrupt callback to stop TTS
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
        """
        self._is_speaking = False
        self._text_buffer.clear()
        
        # Stop TTS provider if it has a stop method
        if hasattr(self.tts, 'stop'):
            try:
                await self.tts.stop()
            except Exception as e:
                self.logger.error(f"Error stopping TTS provider: {e}")
        
        self.logger.debug("StreamingTTS state reset by interrupt")
    
    async def reset_state(self) -> None:
        """Reset TTS state for new turn."""
        await super().reset_state()
        self._is_speaking = False
        self._text_buffer.clear()
        
        # CRITICAL: Reset provider state to clear stale audio queue and completion event
        # This prevents Turn N+1 from receiving stale audio from Turn N
        # and ensures finish_stream() waits for new audio instead of returning immediately
        if hasattr(self.tts, 'reset_state'):
            try:
                await self.tts.reset_state()
                self.logger.info("StreamingTTS state reset (including provider)")
            except Exception as e:
                self.logger.error(f"Error resetting TTS provider state: {e}")
        else:
            self.logger.info("StreamingTTS state reset (provider has no reset_state)")
    
    async def on_completion(self, event: EventChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion event from upstream (Agent).
        
        Finishes text stream and emits TTS_STOP event.
        
        Args:
            event: EventChunk with EVENT_STREAM_COMPLETE from Agent
            
        Yields:
            Remaining AUDIO + EVENT_TTS_STOP + completion event
        """
        if not self._is_speaking:
            # No TTS session active
            self.logger.debug("StreamingTTS on_completion: no active session")
            yield event  # Pass through completion
            return
        
        self.logger.info("StreamingTTS received completion, finishing text stream")
        
        # Finish text stream (tells TTS provider to flush and complete)
        if hasattr(self.tts, 'finish_stream'):
            try:
                async for audio_data in self.tts.finish_stream():
                    if audio_data:
                        yield AudioChunk(
                            type=ChunkType.AUDIO,
                            data=audio_data,
                            sample_rate=self.tts.sample_rate,
                            channels=1,
                            source=self.name,
                            session_id=event.session_id,
                            turn_id=event.turn_id
                        )
            except Exception as e:
                self.logger.error(f"Error finishing TTS stream: {e}")
        
        # Emit TTS_STOP
        yield EventChunk(
            type=ChunkType.EVENT_TTS_STOP,
            event_data={},
            source=self.name,
            session_id=event.session_id,
            turn_id=event.turn_id
        )
        
        self._is_speaking = False
        self.logger.info("StreamingTTS session complete")
        
        # Emit completion event
        yield event
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process TEXT_DELTA chunks through streaming TTS.
        
        Logic:
        - Receive TEXT_DELTA from Agent
        - Append to TTS provider's WebSocket
        - TTS provider segments and synthesizes intelligently
        - Stream audio chunks as they become ready
        
        Note: Middlewares handle signal processing, validation, interrupt detection,
        latency monitoring, and error handling.
        """
        # Only process TEXT_DELTA chunks
        if chunk.type != ChunkType.TEXT_DELTA:
            return
            yield
        
        text_delta = chunk.data if isinstance(chunk.data, str) else str(chunk.data)
        if not text_delta:
            return
            yield
        
        # Buffer text delta
        self._text_buffer.append(text_delta)
        
        self.logger.debug(f"StreamingTTS received TEXT_DELTA: {text_delta[:50]}...")
        
        # Emit TTS START event (first time only)
        if not self._is_speaking:
            self.logger.info("StreamingTTS emitting EVENT_TTS_START")
            yield EventChunk(
                type=ChunkType.EVENT_TTS_START,
                event_data={"mode": "streaming"},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            self._is_speaking = True
        
        # Append text to TTS provider's stream
        if hasattr(self.tts, 'append_text_stream'):
            try:
                async for audio_data in self.tts.append_text_stream(text_delta):
                    if audio_data:
                        yield AudioChunk(
                            type=ChunkType.AUDIO,
                            data=audio_data,
                            sample_rate=self.tts.sample_rate,
                            channels=1,
                            source=self.name,
                            session_id=chunk.session_id,
                            turn_id=chunk.turn_id
                        )
            except Exception as e:
                self.logger.error(f"Error appending text to TTS stream: {e}")
        else:
            # Fallback: buffer until completion (not ideal)
            self.logger.warning(
                "TTS provider does not support append_text_stream. "
                "Buffering text until completion."
            )
