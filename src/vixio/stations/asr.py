"""
ASRStation - Speech to Text (Streaming)

Input: AUDIO_RAW (from TurnDetector buffer), EVENT_STREAM_COMPLETE (trigger)
Output: TEXT_DELTA (streaming transcription) + EVENT_STREAM_COMPLETE

Data Flow:
- TurnDetector buffers audio during user speaking
- On turn end, TurnDetector outputs all buffered AUDIO_RAW + EVENT_STREAM_COMPLETE
- ASR collects all AUDIO_RAW until EVENT_STREAM_COMPLETE
- On completion, ASR processes all collected audio through streaming provider

Completion Contract:
- AWAITS_COMPLETION: True (triggered by TurnDetector's completion signal)
- EMITS_COMPLETION: True (emits completion after transcription, triggers TextAggregator)

Note: Outputs TEXT_DELTA for streaming scenarios.
ASR Provider interface is streaming (AsyncIterator), even for batch engines.
"""

from typing import AsyncIterator, List
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, TextDeltaChunk, EventChunk
from vixio.core.middleware import with_middlewares
from vixio.providers.asr import ASRProvider


@with_middlewares(
    # Note: StreamStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - ErrorHandlerMiddleware (error handling)
)
class ASRStation(StreamStation):
    """
    ASR workstation: Transcribes audio to text (streaming output).
    
    Input: AUDIO_RAW (from TurnDetector), EVENT_STREAM_COMPLETE (trigger)
    Output: TEXT_DELTA (streaming) + EVENT_STREAM_COMPLETE
    
    Data Flow:
    - Receives burst of AUDIO_RAW from TurnDetector (already buffered upstream)
    - Collects audio until EVENT_STREAM_COMPLETE arrives
    - On completion, processes all audio through streaming ASR provider
    - Yields TEXT_DELTA as provider streams results
    
    Completion Contract:
    - Awaits completion from TurnDetector (triggers transcription)
    - Emits completion after transcription (triggers TextAggregator)
    
    Note: ASR itself doesn't buffer long-term - audio is buffered by TurnDetector.
    ASR only collects the burst of audio chunks between turn end and completion.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    LATENCY_METRIC_NAME = "asr_complete"
    
    # Completion contract: await turn completion, emit text completion
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = True
    
    def __init__(self, asr_provider: ASRProvider, name: str = "asr"):
        """
        Initialize ASR station.
        
        Args:
            asr_provider: ASR provider instance
            name: Station name
        """
        super().__init__(name=name, enable_interrupt_detection=True)
        self.asr = asr_provider
        # Temporary collection for audio burst from TurnDetector
        self._pending_audio: List[bytes] = []
        
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        # Set interrupt callback to clear pending audio
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
        """
        # Clear pending audio
        if self._pending_audio:
            self.logger.debug(f"Clearing {len(self._pending_audio)} pending audio chunks")
            self._pending_audio.clear()
        
        # Reset ASR provider
        await self.asr.reset()
    
    async def cleanup(self) -> None:
        """
        Cleanup ASR resources.
        
        Releases ASR provider resources to free memory.
        """
        try:
            # Clear pending audio
            self._pending_audio.clear()
            
            # Cleanup ASR provider
            if self.asr and hasattr(self.asr, 'cleanup'):
                await self.asr.cleanup()
                self.logger.debug("ASR provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up ASR provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process AUDIO_RAW chunk - collect for pending transcription.
        
        Audio chunks arrive in burst from TurnDetector (already buffered there).
        Collect them until on_completion() triggers transcription.
        
        Note: This is not long-term buffering - TurnDetector handles that.
        This just collects the burst of audio between turn end and completion signal.
        """
        if chunk.type == ChunkType.AUDIO_RAW:
            if chunk.data:
                self._pending_audio.append(chunk.data)
                self.logger.debug(f"Collected audio chunk, pending: {len(self._pending_audio)}")
        
        # Don't yield anything during collection
        return
        yield  # Makes this an async generator
    
    async def on_completion(self, event: EventChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion event from TurnDetector.
        
        Processes all collected audio through streaming ASR provider,
        yielding TEXT_DELTA chunks as results stream in.
        
        Args:
            event: EventChunk with EVENT_STREAM_COMPLETE from TurnDetector
            
        Yields:
            TEXT_DELTA chunks + completion event
        """
        if not self._pending_audio:
            self.logger.warning("Completion received but no audio collected")
            return
            yield  # Make this an async generator
        
        self.logger.info(f"Transcribing {len(self._pending_audio)} audio chunks (streaming)...")
        
        # Stream transcription results from provider
        has_result = False
        async for text in self.asr.transcribe_stream(self._pending_audio):
            if text:
                has_result = True
                self.logger.info(f"ASR result: '{text}'")
                
                # Output as TEXT_DELTA
                yield TextDeltaChunk(
                    type=ChunkType.TEXT_DELTA,
                    data=text,
                    source=self.name,
                    session_id=event.session_id,
                    turn_id=event.turn_id
                )
        
        if not has_result:
            self.logger.warning("ASR returned no text")
        
        # Clear collected audio
        self._pending_audio.clear()
        
        # Emit completion event to trigger downstream 
        yield self.emit_completion(
            session_id=event.session_id,
            turn_id=event.turn_id
        )
