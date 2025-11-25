"""
Station base class - workstation in the pipeline

Processing rules:
1. Data chunks: Transform and yield processed results
2. Signal chunks: Immediately passthrough + optionally yield response chunks
3. Turn-aware: Discard chunks from old turns, reset state on new turns
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, TYPE_CHECKING
from core.chunk import Chunk
from loguru import logger

if TYPE_CHECKING:
    from core.control_bus import ControlBus


class Station(ABC):
    """
    Base station - workstation in the pipeline.
    
    Processing rules:
    1. Data chunks: Transform and yield processed results
    2. Signal chunks: Immediately passthrough + optionally yield response chunks
    3. Turn-aware: Discard chunks from old turns, reset state on new turns
    
    Subclasses override process_chunk() to implement custom logic.
    """
    
    def __init__(self, name: Optional[str] = None):
        """
        Initialize station.
        
        Args:
            name: Station name for logging (defaults to class name)
        """
        self.name = name or self.__class__.__name__
        self.logger = logger.bind(station=self.name)
        
        # Turn tracking
        self.current_turn_id = 0
        self.control_bus: Optional['ControlBus'] = None
    
    async def process(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
        Main processing loop with turn-awareness.
        
        Turn handling:
        - Discard chunks from old turns (turn_id < current_turn_id)
        - Reset state when new turn starts (turn_id > current_turn_id)
        - Process chunks from current turn normally
        
        This method should NOT be overridden by subclasses.
        
        Args:
            input_stream: Async iterator of input chunks
            
        Yields:
            Processed chunks
        """
        async for chunk in input_stream:
            # Check turn ID
            if chunk.turn_id < self.current_turn_id:
                # Old turn, discard
                self.logger.debug(f"[{self.name}] Discarding old chunk: turn {chunk.turn_id} < {self.current_turn_id}")
                continue
            
            if chunk.turn_id > self.current_turn_id:
                # New turn started, reset state
                self.logger.info(f"[{self.name}] New turn detected: {chunk.turn_id} (was {self.current_turn_id})")
                self.current_turn_id = chunk.turn_id
                await self.reset_state()
            
            # Process chunk
            try:
                async for output_chunk in self.process_chunk(chunk):
                    # Propagate turn ID
                    output_chunk.turn_id = self.current_turn_id
                    self.logger.debug(f"[{self.name}] Yield: {output_chunk}")
                    yield output_chunk
            except Exception as e:
                chunk_type = "signal" if chunk.is_signal() else "data"
                self.logger.error(f"[{self.name}] Error processing {chunk_type} {chunk}: {e}", exc_info=True)
    
    async def reset_state(self) -> None:
        """
        Reset station state for new turn.
        
        Called automatically when a new turn starts.
        Subclasses can override to clear accumulated state.
        
        Example: ASR station clears audio buffer, Agent clears conversation history.
        """
        self.logger.debug(f"[{self.name}] State reset for turn {self.current_turn_id}")
    
    def should_process(self, chunk: Chunk) -> bool:
        """
        Check if chunk should be processed based on turn ID.
        
        Args:
            chunk: Chunk to check
            
        Returns:
            True if chunk should be processed
        """
        return chunk.turn_id >= self.current_turn_id
    
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
