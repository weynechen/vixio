"""
ASRStation - Speech to Text

Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
Output: TEXT_DELTA (transcription result, source="asr")

Note: Outputs TEXT_DELTA for consistency with streaming scenarios.
Use TextAggregatorStation after this to aggregate for Agent.

Refactored with middleware pattern for clean separation of concerns.
"""

from typing import AsyncIterator, List
from core.station import Station
from core.chunk import Chunk, ChunkType, TextDeltaChunk, EventChunk, is_audio_chunk
from core.middleware import with_middlewares
from stations.middlewares import (
    SignalHandlerMiddleware,
    LatencyMonitorMiddleware,
    ErrorHandlerMiddleware
)
from providers.asr import ASRProvider


@with_middlewares(
    # Handle signals (CONTROL_INTERRUPT - clear buffer)
    SignalHandlerMiddleware(
        on_interrupt=lambda: None,  # Will be set in __init__
        cancel_streaming=False
    ),
    # Monitor ASR completion latency
    LatencyMonitorMiddleware(
        record_first_token=True,
        metric_name="asr_complete"
    ),
    # Handle errors
    ErrorHandlerMiddleware(
        emit_error_event=True,
        suppress_errors=False
    )
)
class ASRStation(Station):
    """
    ASR workstation: Transcribes audio to text.
    
    Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
    Output: TEXT_DELTA (transcription result, source="asr")
    
    Note: Outputs TEXT_DELTA to maintain consistency with streaming ASR.
    Use TextAggregatorStation to aggregate before Agent.
    """
    
    def __init__(self, asr_provider: ASRProvider, name: str = "ASR"):
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
        
        Called by SignalHandlerMiddleware when CONTROL_INTERRUPT received.
        """
        # Clear audio buffer
        if self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks")
            self._audio_buffer.clear()
        
        # Reset ASR provider
        self.asr.reset()
    
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
        
        Middlewares handle: signal processing (CONTROL_INTERRUPT), latency monitoring, error handling.
        
        Core logic:
        - Collect AUDIO_RAW chunks into buffer
        - On EVENT_TURN_END: Transcribe buffered audio, yield TEXT_DELTA (source="asr"), clear buffer
        - Passthrough all other chunks
        
        Note: SignalHandlerMiddleware handles CONTROL_INTERRUPT (clears buffer via _handle_interrupt)
        """
        # Handle EVENT_TURN_END signal (trigger transcription)
        if chunk.type == ChunkType.EVENT_TURN_END:
            # Passthrough signal first (important for downstream)
            yield chunk
            
            # Transcribe buffered audio
            if self._audio_buffer:
                self.logger.info(f"Transcribing {len(self._audio_buffer)} audio chunks...")
                
                text = await self.asr.transcribe(self._audio_buffer)
                
                if text:
                    self.logger.info(f"ASR result: '{text}'")
                    
                    # Output as TEXT_DELTA with source="asr"
                    # Note: LatencyMonitorMiddleware automatically records this output
                    yield TextDeltaChunk(
                        type=ChunkType.TEXT_DELTA,
                        delta=text,
                        source="asr",
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
                    
                    # Emit TEXT_COMPLETE event to signal aggregator
                    yield EventChunk(
                        type=ChunkType.EVENT_TEXT_COMPLETE,
                        event_data={"source": "asr", "text_length": len(text)},
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
                else:
                    self.logger.warning("ASR returned empty text")
                    # Still emit complete event even if text is empty
                    yield EventChunk(
                        type=ChunkType.EVENT_TEXT_COMPLETE,
                        event_data={"source": "asr", "text_length": 0},
                        source=self.name,
                        session_id=chunk.session_id
                    )
                
                # Clear buffer
                self._audio_buffer.clear()
            else:
                self.logger.warning("EVENT_TURN_END received but no audio in buffer")
            
            return
        
        # Handle other signals (passthrough)
        if chunk.is_signal():
            yield chunk
            return
        
        # Collect AUDIO_RAW chunks
        if is_audio_chunk(chunk) and chunk.type == ChunkType.AUDIO_RAW:
            if chunk.data:
                self._audio_buffer.append(chunk.data)
                self.logger.debug(f"Buffered audio chunk, total: {len(self._audio_buffer)}")
            
            # Passthrough audio for downstream
            yield chunk
        else:
            # Passthrough other data types
            yield chunk
