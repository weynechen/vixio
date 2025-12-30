"""
ASRStation - Speech to Text (Batch Mode)

Input: AUDIO (merged chunk from TurnDetector)
Output: TEXT_DELTA (streaming transcription) + EVENT_STREAM_COMPLETE

Data Flow:
- TurnDetector buffers audio during user speaking
- On turn end, TurnDetector outputs one merged AUDIO chunk
- ASR directly processes the audio chunk in process_chunk (not on_completion)
- This allows LatencyMonitorMiddleware to properly track first output

Completion Contract:
- AWAITS_COMPLETION: False (processes audio directly, not triggered by completion)
- EMITS_COMPLETION: True (emits completion after transcription, triggers TextAggregator)

Note: Outputs TEXT_DELTA for streaming scenarios.
ASR Provider interface is streaming (AsyncIterator), even for batch engines.
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, TextDeltaChunk, EventChunk
from vixio.core.middleware import with_middlewares
from vixio.stations.middlewares import TimeoutHandlerMiddleware, InputValidatorMiddleware
from vixio.providers.asr import ASRProvider, ASRStreamResult
from vixio.utils import get_latency_monitor


@with_middlewares(
    # Timeout handler for ASR processing
    TimeoutHandlerMiddleware(
        timeout_seconds=10.0,  # Will be overridden in __init__
        emit_timeout_event=True,
        send_interrupt_signal=True
    )
    # Note: StreamStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - LatencyMonitorMiddleware (monitors first output latency)
    # - ErrorHandlerMiddleware (error handling)
)
class ASRStation(StreamStation):
    """
    ASR workstation: Transcribes audio to text (batch mode, streaming output).
    
    Input: AUDIO (merged chunk from TurnDetector)
    Output: TEXT_DELTA (streaming) + EVENT_STREAM_COMPLETE
    
    Data Flow:
    - Receives single merged AUDIO chunk from TurnDetector
    - Directly processes audio in process_chunk (enables middleware monitoring)
    - Calls ASR provider with complete audio (batch mode)
    - Yields TEXT_DELTA as provider streams results (streaming output)
    - Emits completion to trigger downstream
    
    Note: Input is batch (AUDIO), output is streaming (TEXT_DELTA).
    This leverages ASR provider's ability to stream results while processing.
    
    Completion Contract:
    - Does NOT await completion (processes audio directly)
    - Emits completion after transcription (triggers TextAggregator)
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO]  # Support both for compatibility
    LATENCY_METRIC_NAME = "asr_complete"
    LATENCY_OUTPUT_TYPES = [ChunkType.TEXT_DELTA]  # Only monitor TEXT_DELTA, not EVENT_BOT_THINKING
    
    # Completion contract: process directly, emit completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = False  # Process audio directly in process_chunk
    
    def __init__(
        self, 
        asr_provider: ASRProvider, 
        timeout_seconds: Optional[float] = 10.0,
        name: str = "asr"
    ):
        """
        Initialize ASR station.
        
        Args:
            asr_provider: ASR provider instance
            timeout_seconds: Timeout for ASR processing (default: 10s, None = no timeout)
            name: Station name
        """
        super().__init__(name=name, output_role="user", enable_interrupt_detection=True)
        self.asr = asr_provider
        self.timeout_seconds = timeout_seconds
        self._is_processing = False
        
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'TimeoutHandlerMiddleware':
                middleware.timeout_seconds = self.timeout_seconds
            elif middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
        """
        # Reset ASR provider
        await self.asr.reset()
        self._is_processing = False
    
    async def cleanup(self) -> None:
        """
        Cleanup ASR resources.
        
        Releases ASR provider resources to free memory.
        """
        try:
            # Cleanup ASR provider
            if self.asr and hasattr(self.asr, 'cleanup'):
                await self.asr.cleanup()
                self.logger.debug("ASR provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up ASR provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process AUDIO chunk - directly transcribe and yield results (batch mode).
        
        TurnDetector sends a single merged audio chunk containing all buffered audio.
        We process it immediately (batch mode), allowing LatencyMonitorMiddleware to track output.
        ASR provider streams results (streaming output) as it processes the complete audio.
        
        Args:
            chunk: AudioChunk with merged audio data (AUDIO)
            
        Yields:
            EVENT_BOT_THINKING + TEXT_DELTA chunks + EVENT_STREAM_COMPLETE
        """
        if chunk.type not in (ChunkType.AUDIO) or not chunk.data:
            return
            yield  # Makes this an async generator
        
        self._is_processing = True
        audio_data = chunk.data
        self.logger.info(f"Processing {len(audio_data)} bytes of audio")
        
        # Emit EVENT_BOT_THINKING to let device switch to speaker early
        # This reduces perceived latency by letting device prepare while ASR/Agent process
        self.logger.info("ASR emitting EVENT_BOT_THINKING (device can switch to speaker)")
        yield EventChunk(
            type=ChunkType.EVENT_BOT_THINKING,
            event_data={"stage": "asr_start"},
            source=self.name,
            session_id=chunk.session_id,
            turn_id=chunk.turn_id
        )
        
        # Stream transcription results from provider
        has_result = False
        async for text in self.asr.transcribe_stream([audio_data]):
            if text:
                has_result = True
                self.logger.info(f"ASR result: '{text}'")
                
                # Output as TEXT_DELTA
                yield TextDeltaChunk(
                    type=ChunkType.TEXT_DELTA,
                    data=text,
                    source=self.name,
                    session_id=chunk.session_id,
                    turn_id=chunk.turn_id
                )
        
        if not has_result:
            self.logger.warning("ASR returned no text")
        
        # Emit completion event to trigger downstream 
        self.logger.info("ASR emitting completion event to trigger downstream")
        yield self.emit_completion(
            session_id=chunk.session_id,
            turn_id=chunk.turn_id
        )
        
        self._is_processing = False
        self.logger.debug("ASR process_chunk finished")


@with_middlewares(
    # Timeout handler for ASR processing
    TimeoutHandlerMiddleware(
        timeout_seconds=30.0,  # Longer timeout for streaming mode
        emit_timeout_event=True,
        send_interrupt_signal=True
    ),
    # Validate input (AUDIO_DELTA for streaming)
    InputValidatorMiddleware(
        allowed_types=[ChunkType.AUDIO_DELTA],
        check_empty=True,
        passthrough_on_invalid=False
    )
    # Note: StreamStation base class automatically provides:
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - LatencyMonitorMiddleware (uses LATENCY_METRIC_NAME)
    # - ErrorHandlerMiddleware (error handling)
)
class StreamingASRStation(StreamStation):
    """
    Streaming ASR workstation: Transcribes continuous audio with built-in VAD.
    
    Input: AUDIO_DELTA (continuous audio stream)
    Output: TEXT_DELTA (streaming) + EVENT_STREAM_COMPLETE
    
    Mode (Streaming):
    - Receives continuous AUDIO_DELTA from Transport
    - Maintains long connection to ASR provider
    - ASR provider has built-in VAD (detects speech boundaries)
    - Streams TEXT_DELTA as recognition progresses
    - Emits completion when ASR detects end of speech
    
    Completion Contract:
    - Does NOT await completion (processes audio continuously)
    - Emits completion when ASR detects speech end
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_DELTA]
    LATENCY_METRIC_NAME = "asr_complete"
    LATENCY_OUTPUT_TYPES = [ChunkType.TEXT_DELTA]
    
    # Completion contract: process continuously, emit completion on speech end
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = False
    
    def __init__(
        self, 
        asr_provider: ASRProvider, 
        timeout_seconds: Optional[float] = 30.0,
        name: str = "streaming_asr"
    ):
        """
        Initialize Streaming ASR station.
        
        Args:
            asr_provider: ASR provider instance (must support streaming input)
            timeout_seconds: Timeout for ASR processing (default: 30s, None = no timeout)
            name: Station name
        """
        super().__init__(name=name, output_role="user", enable_interrupt_detection=True)
        self.asr = asr_provider
        self.timeout_seconds = timeout_seconds
        self._is_processing = False
        self._audio_buffer = []
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
        
        # Check if provider supports streaming and VAD
        if hasattr(asr_provider, 'supports_streaming_input'):
            if not asr_provider.supports_streaming_input:
                self.logger.warning(
                    "ASR provider does not support streaming input. "
                    "StreamingASRStation may not work as expected."
                )
        
        if hasattr(asr_provider, 'supports_vad'):
            if not asr_provider.supports_vad:
                self.logger.warning(
                    "ASR provider does not have built-in VAD. "
                    "StreamingASRStation requires ASR with VAD."
                )
        
        self.logger.info("StreamingASR initialized (continuous mode with built-in VAD)")
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        """
        # Set timeout from init parameter
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'TimeoutHandlerMiddleware':
                middleware.timeout_seconds = self.timeout_seconds
                
        # Set interrupt callback
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
        """
        self._is_processing = False
        self._audio_buffer.clear()
        
        # Stop ASR provider if it has a stop method
        if hasattr(self.asr, 'stop_streaming'):
            try:
                await self.asr.stop_streaming()
            except Exception as e:
                self.logger.error(f"Error stopping ASR provider: {e}")
        
        self.logger.debug("StreamingASR state reset by interrupt")
    
    async def reset_state(self) -> None:
        """Reset ASR state for new turn."""
        await super().reset_state()
        self._is_processing = False
        self._audio_buffer.clear()
        self.logger.debug("StreamingASR state reset")
    
    async def cleanup(self) -> None:
        """
        Cleanup ASR resources.
        """
        try:
            self._audio_buffer.clear()
            
            # Cleanup ASR provider
            if self.asr and hasattr(self.asr, 'cleanup'):
                await self.asr.cleanup()
                self.logger.debug("StreamingASR provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up StreamingASR provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process AUDIO_DELTA chunks - continuous streaming transcription.
        
        Flow:
        - Receive continuous AUDIO_DELTA from Transport
        - Append to ASR provider's connection
        - ASR provider processes with built-in VAD
        - Stream TEXT_DELTA as recognition progresses
        - Emit completion when ASR detects speech end
        
        Note: Middlewares handle signal processing, validation, interrupt detection,
        latency monitoring, and error handling.
        """
        if chunk.type != ChunkType.AUDIO_DELTA or not chunk.data:
            return
            yield
        
        audio_data = chunk.data
        self.logger.debug(f"StreamingASR received {len(audio_data)} bytes")
        
        # Start processing session if not already
        if not self._is_processing:
            self._is_processing = True
            self.logger.info("StreamingASR session started")
        
        # Append audio to ASR provider's stream
        if hasattr(self.asr, 'append_audio_continuous'):
            try:
                async for result in self.asr.append_audio_continuous(audio_data):
                    # Handle ASRStreamResult (structured result with text and/or events)
                    if isinstance(result, ASRStreamResult):
                        # Process VAD events for latency monitoring
                        if result.event == "speech_stopped":
                            # Record T0: user_speech_end with accurate VAD timestamp
                            self._latency_monitor.record(
                                chunk.session_id,
                                chunk.turn_id,
                                "user_speech_end",
                                timestamp=result.timestamp
                            )
                            self.logger.debug(f"Recorded user_speech_end at {result.timestamp}")
                        
                        # Process text result
                        if result.text:
                            self.logger.info(f"StreamingASR result: '{result.text}'")
                            yield TextDeltaChunk(
                                type=ChunkType.TEXT_DELTA,
                                data=result.text,
                                source=self.name,
                                role="user",
                                session_id=chunk.session_id,
                                turn_id=chunk.turn_id
                            )
                    # Backward compatibility: handle plain string results
                    elif isinstance(result, str) and result:
                        self.logger.info(f"StreamingASR result: '{result}'")
                        yield TextDeltaChunk(
                            type=ChunkType.TEXT_DELTA,
                            data=result,
                            source=self.name,
                            role="user",
                            session_id=chunk.session_id,
                            turn_id=chunk.turn_id
                        )
            except Exception as e:
                self.logger.error(f"Error in StreamingASR: {e}")
        
        # Check if ASR detected speech end (via provider's state)
        if hasattr(self.asr, 'is_speech_ended') and self.asr.is_speech_ended():
            self.logger.info("StreamingASR detected speech end, emitting BOT_THINKING + completion")
            
            # First emit BOT_THINKING to switch device to speaker state
            yield EventChunk(
                type=ChunkType.EVENT_BOT_THINKING,
                event_data={"mode": "streaming", "reason": "speech_ended"},
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            
            # Then emit completion event
            yield self.emit_completion(
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
            
            self._is_processing = False
