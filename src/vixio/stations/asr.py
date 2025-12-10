"""
ASRStation - Speech to Text

Input: AUDIO_RAW (collect), EVENT_STREAM_COMPLETE (trigger from TurnDetector)
Output: TEXT_DELTA (transcription result) + EVENT_STREAM_COMPLETE

Completion Contract:
- AWAITS_COMPLETION: True (triggered by TurnDetector's completion signal)
- EMITS_COMPLETION: True (emits completion after transcription, triggers TextAggregator)

Note: Outputs TEXT_DELTA for consistency with streaming scenarios.
Use TextAggregatorStation after this to aggregate for Agent.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator, List
from vixio.core.station import BufferStation
from vixio.core.chunk import Chunk, ChunkType, TextDeltaChunk, EventChunk, is_audio_chunk
from vixio.core.middleware import with_middlewares
from vixio.providers.asr import ASRProvider


@with_middlewares(
    # Note: BufferStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - ErrorHandlerMiddleware (error handling)
)
class ASRStation(BufferStation):
    """
    ASR workstation: Transcribes audio to text.
    
    Input: AUDIO_RAW (collect), EVENT_STREAM_COMPLETE (trigger transcription)
    Output: TEXT_DELTA (transcription result) + EVENT_STREAM_COMPLETE
    
    Completion Contract:
    - Awaits completion from TurnDetector (triggers transcription)
    - Emits completion after transcription (triggers TextAggregator)
    
    Note: Outputs TEXT_DELTA to maintain consistency with streaming ASR.
    Use TextAggregatorStation to aggregate before Agent.
    """
    
    # BufferStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    LATENCY_METRIC_NAME = "asr_complete"
    
    # Completion contract: await turn completion, emit text completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = True
    
    def __init__(self, asr_provider: ASRProvider, name: str = "asr"):  # Lowercase for consistent source tracking
        """
        Initialize ASR station.
        
        Args:
            asr_provider: ASR provider instance
            name: Station name
        """
        super().__init__(name=name)
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
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
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
        - Collect audio into buffer (transcription triggered by on_completion)
        
        Core logic:
        - Collect AUDIO_RAW chunks into buffer
        - Transcription is triggered by on_completion() when upstream sends EVENT_STREAM_COMPLETE
        
        Note: SignalHandlerMiddleware handles CONTROL_STATE_RESET (clears buffer via _handle_interrupt)
        """
        # Collect AUDIO_RAW chunks
        if chunk.type == ChunkType.AUDIO_RAW:
            if chunk.data:
                self._audio_buffer.append(chunk.data)
                self.logger.debug(f"Buffered audio chunk, total: {len(self._audio_buffer)}")
        
        # Must be async generator (yield nothing if just buffering)
        return
        yield  # Makes this an async generator
    
    async def on_completion(self, event: EventChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion event from upstream.
        
        Triggers transcription of buffered audio and emits:
        1. TEXT_DELTA with transcription result
        2. Completion event to trigger downstream TextAggregator
        
        Args:
            event: EventChunk with EVENT_STREAM_COMPLETE from upstream
            
        Yields:
            TEXT_DELTA + completion event
        """
        if not self._audio_buffer:
            self.logger.warning("Completion received but no audio in buffer")
            return
            yield  # Make this an async generator
        
        self.logger.info(f"Transcribing {len(self._audio_buffer)} audio chunks...")
        
        text = await self.asr.transcribe(self._audio_buffer)
        
        if text:
            self.logger.info(f"ASR result: '{text}'")
            
            # Output as TEXT_DELTA
            yield TextDeltaChunk(
                type=ChunkType.TEXT_DELTA,
                data=text,
                source=self.name,
                session_id=event.session_id,
                turn_id=event.turn_id
            )
        else:
            self.logger.warning("ASR returned empty text")
        
        # Clear buffer
        self._audio_buffer.clear()
        
        # Emit completion event to trigger downstream 
        yield self.emit_completion(
            session_id=event.session_id,
            turn_id=event.turn_id
        )
