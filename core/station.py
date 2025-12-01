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
        self._session_id: Optional[str] = None
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
    
    def set_session_id(self, session_id: str) -> None:
        """
        Set session ID for this station and rebind logger.
        
        This method is called by Pipeline when session_id is set,
        automatically updating the logger to include session_id.
        
        Args:
            session_id: Session identifier (will be truncated to 8 chars for display)
        """
        self._session_id = session_id
        # Truncate to 8 chars for readability
        session_id_short = session_id[:8] if session_id and len(session_id) > 8 else (session_id or "--------")
        # Rebind logger with session_id
        self.logger = logger.bind(station=self.name, session_id=session_id_short)
    
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
        
        Note for future refactoring:
            If process_chunk becomes too complex with many if/elif branches for different chunk types,
            consider introducing Strategy Pattern for chunk type dispatching:
            
            Example architecture:
            - Middleware (current): Handles cross-cutting concerns (validation, monitoring, signals)
            - Strategy Pattern (future): Handles business logic per chunk type
            
            Implementation approach:
            ```
            class Station:
                def __init__(self):
                    self._chunk_handlers = {}  # ChunkType -> handler mapping
                
                def register_handler(self, chunk_type, handler):
                    self._chunk_handlers[chunk_type] = handler
                
                async def dispatch_to_handler(self, chunk):
                    handler = self._chunk_handlers.get(chunk.type, self._default_handler)
                    async for result in handler(chunk):
                        yield result
            
            class ASRStation(StreamStation):
                def _setup_handlers(self):
                    self.register_handler(ChunkType.AUDIO_RAW, self._handle_audio)
                    self.register_handler(ChunkType.EVENT_TURN_END, self._handle_turn_end)
                
                async def process_chunk(self, chunk):
                    # Clean: delegate to registered handlers
                    async for result in self.dispatch_to_handler(chunk):
                        yield result
            ```
            
            Benefits:
            - Cleaner code: Each chunk type has its own handler method
            - Better testability: Test handlers independently
            - Easier maintenance: Add/modify handlers without touching main logic
            - Clear structure: Handler registry shows all supported chunk types at a glance
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


class StreamStation(Station):
    """
    Stream processing station base class.
    
    Characteristics:
    - One-to-one or one-to-many transformations
    - May process for extended time (requires timeout, interrupt handling)
    - Async streaming output
    
    Use cases: Agent, ASR, TTS
    
    Default middlewares (auto-applied via decorator):
    - InputValidatorMiddleware: Validate input types (subclass specifies allowed_types)
    - SignalHandlerMiddleware: Handle CONTROL_INTERRUPT
    - InterruptDetectorMiddleware: Detect turn_id changes
    - LatencyMonitorMiddleware: Monitor first output latency
    - ErrorHandlerMiddleware: Error handling
    
    Subclasses should define:
    - ALLOWED_INPUT_TYPES: List[ChunkType] - Allowed input chunk types
    - LATENCY_METRIC_NAME: str - Latency metric name (default: station name)
    - LATENCY_OUTPUT_TYPES: List[ChunkType] (optional) - Output types to monitor (default: all)
    """
    
    # Subclasses should override these
    ALLOWED_INPUT_TYPES: list = []  # e.g., [ChunkType.TEXT]
    LATENCY_METRIC_NAME: str = ""  # e.g., "agent_first_token"
    LATENCY_OUTPUT_TYPES: list = None  # e.g., [ChunkType.AUDIO_RAW] (None = all types)
    
    def __init__(
        self,
        timeout_seconds: Optional[float] = None,
        enable_interrupt_detection: bool = True,
        name: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize stream station.
        
        Args:
            timeout_seconds: Processing timeout in seconds (None = no timeout)
            enable_interrupt_detection: Enable interrupt detection during streaming
            name: Station name (defaults to class name)
            **kwargs: Additional arguments for base Station
        """
        super().__init__(name=name, **kwargs)
        self.timeout_seconds = timeout_seconds
        self.enable_interrupt_detection = enable_interrupt_detection
        
        # Set latency metric name from class attribute if not overridden
        if not self.LATENCY_METRIC_NAME:
            self.LATENCY_METRIC_NAME = f"{self.name.lower()}_first_output"


class BufferStation(Station):
    """
    Buffer/aggregation station base class.
    
    Characteristics:
    - Collects data, outputs on specific signal trigger
    - Has internal buffer
    - Processing usually instantaneous
    
    Use cases: TextAggregator, SentenceAggregator
    
    Default middlewares (auto-applied via decorator):
    - InputValidatorMiddleware: Validate input types (subclass specifies allowed_types)
    - SignalHandlerMiddleware: Handle CONTROL_INTERRUPT (clear buffer)
    - ErrorHandlerMiddleware: Error handling
    
    Subclasses should define:
    - ALLOWED_INPUT_TYPES: List[ChunkType] - Allowed input chunk types
    """
    
    # Subclasses should override these
    ALLOWED_INPUT_TYPES: list = []  # e.g., [ChunkType.TEXT_DELTA]
    
    def __init__(self, name: Optional[str] = None, **kwargs):
        """
        Initialize buffer station.
        
        Args:
            name: Station name (defaults to class name)
            **kwargs: Additional arguments for base Station
        """
        super().__init__(name=name, **kwargs)


class DetectorStation(Station):
    """
    Detector/event emitter station base class.
    
    Characteristics:
    - State machine logic
    - Detects pattern changes, emits events
    - Passthrough data + emit events
    
    Use cases: VAD, TurnDetector
    
    Default middlewares (auto-applied via decorator):
    - InputValidatorMiddleware: Validate input types (subclass specifies allowed_types)
    - SignalHandlerMiddleware: Handle CONTROL_INTERRUPT
    - ErrorHandlerMiddleware: Error handling
    
    Subclasses should define:
    - ALLOWED_INPUT_TYPES: List[ChunkType] - Allowed input chunk types
    """
    
    # Subclasses should override these
    ALLOWED_INPUT_TYPES: list = []  # e.g., [ChunkType.AUDIO_RAW]
    
    def __init__(self, name: Optional[str] = None, **kwargs):
        """
        Initialize detector station.
        
        Args:
            name: Station name (defaults to class name)
            **kwargs: Additional arguments for base Station
        """
        super().__init__(name=name, **kwargs)
