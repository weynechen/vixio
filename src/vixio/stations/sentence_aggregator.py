"""
SentenceAggregatorStation - aggregate streaming text into sentences

Input: TEXT_DELTA, EVENT_STREAM_COMPLETE (trigger flush from Agent)
Output: TEXT (complete sentences) + EVENT_STREAM_COMPLETE

Completion Contract:
- AWAITS_COMPLETION: True (triggered by Agent's completion signal to flush remaining)
- EMITS_COMPLETION: True (emits completion after final flush, triggers TTS stop)

Refactored with middleware pattern and provider pattern for clean separation of concerns.
"""

from collections.abc import AsyncIterator, AsyncGenerator
from vixio.core.station import BufferStation
from vixio.core.chunk import Chunk, ChunkType, TextChunk, EventChunk
from vixio.core.middleware import with_middlewares
from vixio.stations.middlewares import LatencyMonitorMiddleware
from vixio.providers.sentence_aggregator import SentenceAggregatorProvider


@with_middlewares(
    # Monitor first sentence latency (custom for SentenceAggregator)
    LatencyMonitorMiddleware(
        record_first_token=True,
        metric_name="first_sentence_complete"
    )
    # Note: BufferStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - ErrorHandlerMiddleware (error handling)
)
class SentenceAggregatorStation(BufferStation):
    """
    Sentence aggregator: aggregates streaming text into complete sentences.
    
    Input: TEXT_DELTA (streaming), EVENT_STREAM_COMPLETE (trigger flush)
    Output: TEXT (complete sentences) + EVENT_STREAM_COMPLETE
    
    Completion Contract:
    - Awaits completion from Agent (triggers flush of remaining buffer)
    - Emits completion after final flush (triggers TTS stop)
    
    This station uses a provider pattern for different aggregation strategies:
    - SimpleSentenceAggregatorProviderCN: Enhanced rule-based for Chinese
    - SemanticSentenceAggregatorProvider: jieba-based semantic (future)
    - etc.
    """
    
    # BufferStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT_DELTA]
    
    # Completion contract: await agent completion to flush, emit completion for TTS
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = True
    
    def __init__(
        self,
        provider: SentenceAggregatorProvider,
        name: str = "SentenceAggregator"
    ):
        """
        Initialize sentence aggregator station.
        
        Args:
            provider: Sentence aggregator provider implementation
            name: Station name
        """
        super().__init__(name=name, output_role=None)  # Don't override role, pass through
        self.provider = provider
        self.logger.info(f"Using provider: {provider.name}")
        
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """Hook to configure middlewares."""
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """Handle interrupt signal - reset provider."""
        self.provider.reset()
        self.logger.debug("sentence aggregator provider reset")
    
    async def reset_state(self) -> None:
        """
        Reset state for new turn.
        
        Called automatically when turn changes. Flushes remaining buffer
        and resets provider state.
        """
        # Flush remaining buffer before reset
        remaining = self.provider.flush()
        if remaining:
            self.logger.warning(
                f"[SENTENCE_AGG] Turn reset with unflushed buffer: '{remaining[:50]}...' "
                f"(length={len(remaining)})"
            )
        
        # Reset provider state
        self.provider.reset()
        self.logger.debug(f"[SENTENCE_AGG] State reset, buffer cleared")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process chunk through sentence aggregator - CORE LOGIC ONLY.
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (TEXT_DELTA)
        - Passthrough signals (EVENT_*) for downstream nodes
        - Accumulate text and emit complete sentences immediately
        
        Core logic:
        - Accumulate TEXT_DELTA chunks and aggregate into sentences via provider
        - Emit complete sentences as soon as they're detected
        - Final flush is triggered by on_completion() when upstream sends EVENT_STREAM_COMPLETE
        - Passthrough signal chunks to allow event propagation
        
        Note: SignalHandlerMiddleware handles CONTROL_STATE_RESET (resets provider via _handle_interrupt)
        Note: LatencyMonitorMiddleware automatically records first sentence output
        """
        # Passthrough signal chunks (events need to reach OutputStation)
        # DAG accepts all signals, but BufferStation doesn't process them
        if chunk.is_signal():
            self.logger.debug(f"Passthrough signal: {chunk.type}")
            yield chunk
            return
        
        # Process TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            # Extract text from data attribute (unified API)
            delta = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
            
            self.logger.debug(
                f"[SENTENCE_AGG] Received TEXT_DELTA: '{delta}' "
                f"(source={chunk.source}, session={chunk.session_id[:8] if chunk.session_id else 'N/A'}, "
                f"turn={chunk.turn_id})"
            )
            
            if delta:
                # Add delta to provider and get complete sentences
                sentences = self.provider.add_chunk(delta)
                
                self.logger.debug(
                    f"[SENTENCE_AGG] Provider returned {len(sentences)} sentence(s), "
                    f"buffer_size={len(self.provider.buffer)}"
                )
                
                # Yield each complete sentence as TEXT chunk
                for sentence in sentences:
                    self.logger.info(f"Complete sentence: '{sentence[:50]}...'")
                    
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        data=sentence,
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
            else:
                self.logger.debug(f"[SENTENCE_AGG] Empty delta, skipped")
    
    async def on_completion(self, event: EventChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion event from upstream (Agent).
        
        Flushes remaining buffer from provider as final sentence and emits completion event.
        
        Args:
            event: EventChunk with EVENT_STREAM_COMPLETE from Agent
            
        Yields:
            Final TEXT chunk (if any) + completion event
        """
        self.logger.debug(
            f"[SENTENCE_AGG] Received COMPLETION event from {event.source}, "
            f"buffer_size={len(self.provider.buffer)}"
        )
        
        remaining = self.provider.flush()
        if remaining:
            self.logger.info(f"Flushing final sentence: '{remaining[:50]}...'")
            yield TextChunk(
                type=ChunkType.TEXT,
                data=remaining,
                source=self.name,
                session_id=event.session_id,
                turn_id=event.turn_id
            )
        else:
            self.logger.debug(f"[SENTENCE_AGG] No remaining text to flush")
        
        # Emit completion event (triggers TTS stop)
        self.logger.debug(f"[SENTENCE_AGG] Emitting completion event")
        yield self.emit_completion(
            session_id=event.session_id,
            turn_id=event.turn_id
        )

