"""
Station base class - workstation in the pipeline

Processing rules:
1. Data chunks: Transform and yield processed results
2. Signal chunks: Immediately passthrough + optionally yield response chunks
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from core.chunk import Chunk
import logging

logger = logging.getLogger(__name__)


class Station(ABC):
    """
    Base station - workstation in the pipeline.
    
    Processing rules:
    1. Data chunks: Transform and yield processed results
    2. Signal chunks: Immediately passthrough + optionally yield response chunks
    
    Subclasses override process_chunk() to implement custom logic.
    """
    
    def __init__(self, name: Optional[str] = None):
        """
        Initialize station.
        
        Args:
            name: Station name for logging (defaults to class name)
        """
        self.name = name or self.__class__.__name__
        self.logger = logging.getLogger(f"station.{self.name}")
    
    async def process(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
        Main processing loop.
        
        Routing logic:
        - Signal chunks: Process (subclass can choose to passthrough or modify)
        - Data chunks: Process and yield results
        
        This method should NOT be overridden by subclasses.
        
        Args:
            input_stream: Async iterator of input chunks
            
        Yields:
            Processed chunks
        """
        async for chunk in input_stream:
            # Process all chunks (data and signals) through process_chunk
            try:
                async for output_chunk in self.process_chunk(chunk):
                    self.logger.debug(f"[{self.name}] Yield: {output_chunk}")
                    yield output_chunk
            except Exception as e:
                chunk_type = "signal" if chunk.is_signal() else "data"
                self.logger.error(f"[{self.name}] Error processing {chunk_type} {chunk}: {e}", exc_info=True)
    
    @abstractmethod
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process a single chunk.
        
        For data chunks:
        - Transform the data and yield new chunks
        - Or passthrough unchanged (yield chunk)
        
        For signal chunks:
        - Must yield the signal to pass it through (yield chunk)
        - Can update internal state
        - Can optionally yield additional chunks (e.g., ASR yields TEXT on EVENT_TURN_END)
        
        Args:
            chunk: Input chunk (data or signal)
            
        Yields:
            Output chunks (for signals, at minimum yield the signal itself to pass it through)
        """
        pass
    
    def __str__(self) -> str:
        return f"Station({self.name})"
    
    def __repr__(self) -> str:
        return self.__str__()


class PassthroughStation(Station):
    """
    Simple passthrough station - yields all chunks unchanged.
    
    Useful for:
    - Testing pipeline structure
    - Placeholder in pipeline
    - Adding logging without transformation
    """
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """Simply passthrough all chunks"""
        yield chunk
