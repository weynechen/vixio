"""
ASRStation - Speech to Text (Streaming)

Input: AUDIO_RAW (merged chunk from TurnDetector)
Output: TEXT_DELTA (streaming transcription) + EVENT_STREAM_COMPLETE

Data Flow:
- TurnDetector buffers audio during user speaking
- On turn end, TurnDetector outputs one merged AUDIO_RAW chunk
- ASR directly processes the audio chunk in process_chunk (not on_completion)
- This allows LatencyMonitorMiddleware to properly track first output

Completion Contract:
- AWAITS_COMPLETION: False (processes audio directly, not triggered by completion)
- EMITS_COMPLETION: True (emits completion after transcription, triggers TextAggregator)

Note: Outputs TEXT_DELTA for streaming scenarios.
ASR Provider interface is streaming (AsyncIterator), even for batch engines.
"""

from typing import AsyncIterator
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, TextDeltaChunk, EventChunk
from vixio.core.middleware import with_middlewares
from vixio.providers.asr import ASRProvider


@with_middlewares(
    # Note: StreamStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - LatencyMonitorMiddleware (monitors first output latency)
    # - ErrorHandlerMiddleware (error handling)
)
class ASRStation(StreamStation):
    """
    ASR workstation: Transcribes audio to text (streaming output).
    
    Input: AUDIO_RAW (merged chunk from TurnDetector)
    Output: TEXT_DELTA (streaming) + EVENT_STREAM_COMPLETE
    
    Data Flow:
    - Receives single merged AUDIO_RAW chunk from TurnDetector
    - Directly processes audio in process_chunk (enables middleware monitoring)
    - Yields TEXT_DELTA as provider streams results
    - Emits completion to trigger downstream
    
    Completion Contract:
    - Does NOT await completion (processes audio directly)
    - Emits completion after transcription (triggers TextAggregator)
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    LATENCY_METRIC_NAME = "asr_complete"
    LATENCY_OUTPUT_TYPES = [ChunkType.TEXT_DELTA]  # Only monitor TEXT_DELTA, not EVENT_BOT_THINKING
    
    # Completion contract: process directly, emit completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = False  # Process audio directly in process_chunk
    
    def __init__(self, asr_provider: ASRProvider, name: str = "asr"):
        """
        Initialize ASR station.
        
        Args:
            asr_provider: ASR provider instance
            name: Station name
        """
        super().__init__(name=name, enable_interrupt_detection=True)
        self.asr = asr_provider
        self._is_processing = False
        
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        # Set interrupt callback
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
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
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process AUDIO_RAW chunk - directly transcribe and yield results.
        
        TurnDetector sends a single merged audio chunk containing all buffered audio.
        We process it immediately, allowing LatencyMonitorMiddleware to track output.
        
        Args:
            chunk: AudioChunk with merged audio data
            
        Yields:
            EVENT_BOT_THINKING + TEXT_DELTA chunks + EVENT_STREAM_COMPLETE
        """
        if chunk.type != ChunkType.AUDIO_RAW or not chunk.data:
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
