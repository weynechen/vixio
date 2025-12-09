"""
ASRStation - Speech to Text

Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
Output: TEXT_DELTA (transcription result, source="asr")

Note: Outputs TEXT_DELTA for consistency with streaming scenarios.
Use TextAggregatorStation after this to aggregate for Agent.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator, List
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, TextDeltaChunk, EventChunk, is_audio_chunk
from vixio.core.middleware import with_middlewares
from vixio.providers.asr import ASRProvider


@with_middlewares(
    # Note: StreamStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_INTERRUPT)
    # - InterruptDetectorMiddleware (detects turn_id changes)
    # - LatencyMonitorMiddleware (uses LATENCY_METRIC_NAME)
    # - ErrorHandlerMiddleware (error handling)
)
class ASRStation(StreamStation):
    """
    ASR workstation: Transcribes audio to text.
    
    Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
    Output: TEXT_DELTA (transcription result, source="asr")
    
    Note: Outputs TEXT_DELTA to maintain consistency with streaming ASR.
    Use TextAggregatorStation to aggregate before Agent.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    LATENCY_METRIC_NAME = "asr_complete"
    """
    ASR workstation: Transcribes audio to text.
    
    Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
    Output: TEXT_DELTA (transcription result, source="asr")
    
    Note: Outputs TEXT_DELTA to maintain consistency with streaming ASR.
    Use TextAggregatorStation to aggregate before Agent.
    """
    
    def __init__(self, asr_provider: ASRProvider, name: str = "asr"):  # Lowercase for consistent source tracking
        """
        Initialize ASR station.
        
        Args:
            asr_provider: ASR provider instance
            name: Station name
        """
        super().__init__(
            name=name,
            enable_interrupt_detection=False  # ASR doesn't need interrupt detection during processing
        )
        self.asr = asr_provider
        self._audio_buffer: List[bytes] = []
        
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        # Set interrupt callback to clear audio buffer
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_INTERRUPT received.
        """
        # Clear audio buffer
        if self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks")
            self._audio_buffer.clear()
        
        # Reset ASR provider
        await self.asr.reset()  # ← Fixed: added await
    
    async def cleanup(self) -> None:
        """
        Cleanup ASR resources.
        
        Releases ASR provider resources to free memory.
        """
        try:
            # Clear audio buffer
            self._audio_buffer.clear()
            
            # Cleanup ASR provider
            if self.asr and hasattr(self.asr, 'cleanup'):
                await self.asr.cleanup()
                self.logger.debug("ASR provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up ASR provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through ASR - CORE LOGIC ONLY.
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (AUDIO_RAW)
        - Do NOT passthrough - DAG handles routing to downstream nodes
        - Output: TEXT_DELTA + EVENT_TEXT_COMPLETE
        
        Core logic:
        - Collect AUDIO_RAW chunks into buffer
        - On EVENT_TURN_END: Transcribe buffered audio, yield TEXT_DELTA, clear buffer
        
        Note: SignalHandlerMiddleware handles CONTROL_INTERRUPT (clears buffer via _handle_interrupt)
        """
        # Handle EVENT_TURN_END signal (trigger transcription)
        if chunk.type == ChunkType.EVENT_TURN_END:
            # Transcribe buffered audio
            if self._audio_buffer:
                self.logger.info(f"Transcribing {len(self._audio_buffer)} audio chunks...")
                
                text = await self.asr.transcribe(self._audio_buffer)
                
                if text:
                    self.logger.info(f"ASR result: '{text}'")
                    
                    # Output as TEXT_DELTA
                    yield TextDeltaChunk(
                        type=ChunkType.TEXT_DELTA,
                        data=text,
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
                    
                    # Emit TEXT_COMPLETE event to signal aggregator
                    yield EventChunk(
                        type=ChunkType.EVENT_TEXT_COMPLETE,
                        event_data={"source": self.name, "text_length": len(text)},
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
                else:
                    self.logger.warning("ASR returned empty text")
                    # Still emit complete event even if text is empty
                    yield EventChunk(
                        type=ChunkType.EVENT_TEXT_COMPLETE,
                        event_data={"source": self.name, "text_length": 0},
                        source=self.name,
                        session_id=chunk.session_id
                    )
                
                # Clear buffer
                self._audio_buffer.clear()
            else:
                self.logger.warning("EVENT_TURN_END received but no audio in buffer")
            
            return
        
        # Collect AUDIO_RAW chunks
        if chunk.type == ChunkType.AUDIO_RAW:
            if chunk.data:
                self._audio_buffer.append(chunk.data)
                self.logger.debug(f"Buffered audio chunk, total: {len(self._audio_buffer)}")
