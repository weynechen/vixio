"""
TextAggregatorStation - Aggregates TEXT_DELTA into complete TEXT

Input: TEXT_DELTA (streaming)
Output: TEXT (aggregated complete text)

This station aggregates streaming text deltas and emits complete text
as a single TEXT chunk when a termination signal is received.

Use case: Aggregate ASR streaming output before sending to Agent.
"""

from typing import AsyncIterator
from core.station import Station
from core.chunk import Chunk, ChunkType, TextChunk


class TextAggregatorStation(Station):
    """
    Text aggregator: Aggregates TEXT_DELTA into complete TEXT.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete aggregated text)
    
    Workflow:
    1. Accumulate TEXT_DELTA chunks
    2. On EVENT_TEXT_COMPLETE: Emit complete text as TEXT
    3. Pass through all other chunks
    """
    
    def __init__(self, name: str = "TextAggregator"):
        """
        Initialize text aggregator station.
        
        Args:
            name: Station name
        """
        super().__init__(name=name)
        self._text_buffer = ""
        self._source = ""  # Remember the source of accumulated text
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through text aggregator.
        
        Logic:
        - On TEXT_DELTA: Accumulate to buffer
        - On EVENT_TEXT_COMPLETE: Emit complete TEXT
        - On CONTROL_INTERRUPT: Clear buffer
        - Passthrough all other chunks
        """
        # Handle signals
        if chunk.is_signal():
            # Emit complete text when text input is complete
            if chunk.type == ChunkType.EVENT_TEXT_COMPLETE:
                if self._text_buffer.strip():
                    self.logger.info(f"Aggregated text: '{self._text_buffer[:50]}...'")
                    
                    # Emit complete text as TEXT for Agent
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        content=self._text_buffer,
                        source=self._source or "aggregator",  # Preserve original source
                        session_id=chunk.session_id
                    )
                    
                    # Clear buffer
                    self._text_buffer = ""
                    self._source = ""
                else:
                    self.logger.debug("No text to aggregate")
            
            # Clear buffer on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                if self._text_buffer:
                    self.logger.debug("Clearing text buffer on interrupt")
                    self._text_buffer = ""
                    self._source = ""
            
            # Passthrough signals
            yield chunk
            return
        
        # Accumulate TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            delta = chunk.delta if hasattr(chunk, 'delta') else str(chunk.data or "")
            
            if delta:
                self._text_buffer += delta
                
                # Remember the source of the first chunk
                if not self._source and chunk.source:
                    self._source = chunk.source
                
                self.logger.debug(f"Accumulated {len(delta)} chars, buffer: {len(self._text_buffer)} chars")
            
            # Passthrough TEXT_DELTA
            yield chunk
        else:
            # Passthrough all other chunks
            yield chunk

