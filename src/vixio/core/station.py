"""
Station base class - workstation in the pipeline

Processing rules:
1. Data chunks: Transform and yield processed results
2. Signal chunks: Immediately passthrough + optionally yield response chunks
3. Turn-aware: Discard chunks from old turns, reset state on new turns

Completion Contract:
- EMITS_COMPLETION: Station declares it emits completion signal when done
- AWAITS_COMPLETION: Station declares it needs completion signal to trigger output
- DAG automatically binds completion signals between adjacent stations
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, AsyncGenerator
from enum import Enum
from typing import List, Optional, TYPE_CHECKING, cast
from vixio.core.chunk import Chunk, ChunkType, EventChunk, is_completion_event
from loguru import logger

if TYPE_CHECKING:
    from vixio.core.control_bus import ControlBus


class StationRole(str, Enum):
    """
    Station behavior role - helps DAG understand station characteristics
    
    Roles:
    - STREAM: One-to-one/many transformation, emits completion when done
    - BUFFER: Accumulates data, needs completion signal to trigger output
    - DETECTOR: State machine, emits state change events
    - SOURCE: Input source (e.g., InputStation)
    - SINK: Output endpoint (e.g., OutputStation)
    """
    STREAM = "stream"           # Streaming processor (Agent, TTS)
    BUFFER = "buffer"           # Buffer aggregator (TextAggregator, ASR)
    DETECTOR = "detector"       # State detector (VAD, TurnDetector)
    SOURCE = "source"           # Input source
    SINK = "sink"               # Output endpoint


class Station(ABC):
    """
    Base station - workstation in the pipeline.
    
    Processing rules:
    1. Data chunks: Transform and yield processed results
    2. Signal chunks: Immediately passthrough + optionally yield response chunks
    3. Turn-aware: Discard chunks from old turns, reset state on new turns
    
    Completion Contract:
    - ROLE: Station behavior type (STREAM, BUFFER, DETECTOR, SOURCE, SINK)
    - INPUT_TYPES: List of ChunkTypes this station accepts
    - OUTPUT_TYPES: List of ChunkTypes this station produces
    - EMITS_COMPLETION: True if station emits completion signal when done
    - AWAITS_COMPLETION: True if station needs completion signal to trigger output
    
    Subclasses override process_chunk() to implement core logic.
    Subclasses can override on_completion() to handle completion signals.
    """
    
    # ============ Completion Contract (subclasses should override) ============
    ROLE: StationRole = StationRole.STREAM
    INPUT_TYPES: List[ChunkType] = []       # Accepted input types
    OUTPUT_TYPES: List[ChunkType] = []      # Produced output types
    EMITS_COMPLETION: bool = False          # Emits completion signal when done
    AWAITS_COMPLETION: bool = False         # Needs completion signal to trigger
    
    def __init__(self, name: Optional[str] = None, output_role: Optional[str] = None):
        """
        Initialize station.
        
        Args:
            name: Station name for logging (defaults to class name)
            output_role: Output chunk role ("user" or "bot"), None means don't override chunk's role
        """
        self.name = name or self.__class__.__name__
        self._session_id: Optional[str] = None
        self.logger = logger.bind(component=self.name)
        
        # Role management: station can declare output role to override chunk's default
        self.output_role = output_role
        
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
        
        Completion handling:
        - If chunk is EVENT_STREAM_COMPLETE and this station AWAITS_COMPLETION,
          call on_completion() instead of process_chunk()
        
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
            
            # Handle completion event specially (consumed, not passthrough)
            if is_completion_event(chunk) and self.AWAITS_COMPLETION:
                try:
                    self.logger.debug(f"[{self.name}] Processing completion event from {chunk.source}")
                    # Type assertion: chunk is guaranteed to be EventChunk here due to is_completion_event check
                    event_chunk = cast(EventChunk, chunk)
                    async for output_chunk in self.on_completion(event_chunk):
                        # Propagate turn ID
                        output_chunk.turn_id = self.current_turn_id
                        # Apply station output role if configured
                        self._apply_output_role(output_chunk)
                        self.logger.debug(f"[{self.name}] Yield from on_completion: {output_chunk}")
                        yield output_chunk
                except Exception as e:
                    chunk_str = str(chunk).replace('{', '{{').replace('}', '}}')
                    self.logger.error(f"[{self.name}] Error in on_completion {chunk_str}: {e}", exc_info=True)
                continue  # Consume completion event, don't passthrough
            
            # Process chunk normally
            try:
                async for output_chunk in self.process_chunk(chunk):
                    # Propagate turn ID
                    output_chunk.turn_id = self.current_turn_id
                    # Apply station output role if configured
                    self._apply_output_role(output_chunk)
                    self.logger.debug(f"[{self.name}] Yield: {output_chunk}")
                    yield output_chunk
            except Exception as e:
                chunk_type = "signal" if chunk.is_signal() else "data"
                chunk_str = str(chunk).replace('{', '{{').replace('}', '}}')  # Escape braces for loguru
                self.logger.error(f"[{self.name}] Error processing {chunk_type} {chunk_str}: {e}", exc_info=True)
    
    def _apply_output_role(self, chunk: Chunk) -> None:
        """
        Apply station-level output role to chunk if configured.
        
        If self.output_role is set, override the chunk's role attribute.
        This allows stations to declare their output role uniformly.
        
        Args:
            chunk: Output chunk to modify (in-place)
        """
        if self.output_role is not None:
            chunk.role = self.output_role
    
    async def reset_state(self) -> None:
        """
        Reset station state for new turn.
        
        Called automatically when a new turn starts.
        Subclasses can override to clear accumulated state.
        
        Example: ASR station clears audio buffer, Agent clears conversation history.
        """
        self.logger.debug(f"[{self.name}] State reset for turn {self.current_turn_id}")
    
    async def on_completion(self, event: EventChunk) -> AsyncIterator[Chunk]:
        """
        Handle completion event from upstream station.
        
        Called when upstream station emits EVENT_STREAM_COMPLETE.
        Only called if this station has AWAITS_COMPLETION = True.
        
        Default behavior: do nothing (subclasses override for buffer flush, etc.)
        
        Args:
            event: EventChunk with type=EVENT_STREAM_COMPLETE from upstream
            
        Yields:
            Output chunks (e.g., aggregated text from buffer)
        
        Example:
            class TextAggregatorStation(BufferStation):
                AWAITS_COMPLETION = True
                
                async def on_completion(self, event):
                    if self._buffer:
                        yield TextChunk(data=self._buffer)
                        self._buffer = ""
        """
        # Default: do nothing
        return
        yield  # Make this a generator
    
    def emit_completion(self, session_id: Optional[str] = None, turn_id: int = 0) -> EventChunk:
        """
        Create a completion event.
        
        Helper method for stations that emit completion signals.
        Only meaningful if EMITS_COMPLETION = True.
        
        Args:
            session_id: Session ID (optional)
            turn_id: Turn ID
            
        Returns:
            EventChunk with type=EVENT_STREAM_COMPLETE
        """
        return EventChunk(
            type=ChunkType.EVENT_STREAM_COMPLETE,
            source=self.name,
            session_id=session_id or self._session_id,
            turn_id=turn_id
        )
    
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
        self.logger = logger.bind(component=self.name, session_id=session_id_short)
    
    @abstractmethod
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process a single chunk.
        
        For data chunks:
        - Transform the data and yield new chunks
        - Or passthrough unchanged (yield chunk)
        
        For signal chunks:
        - Must yield the signal to pass it through (yield chunk)
        - Can update internal state
        - Can optionally yield additional chunks (e.g., ASR yields TEXT on completion signal)
        
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
                    self.register_handler(ChunkType.AUDIO, self._handle_audio)
                    self.register_handler(ChunkType.EVENT_STREAM_COMPLETE, self._handle_turn_end)
                
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
    - Typically EMITS_COMPLETION = True (emits completion when stream ends)
    
    Use cases: Agent, TTS
    
    Default middlewares (auto-applied via decorator):
    - InputValidatorMiddleware: Validate input types (subclass specifies allowed_types)
    - SignalHandlerMiddleware: Handle CONTROL_STATE_RESET
    - InterruptDetectorMiddleware: Detect turn_id changes
    - LatencyMonitorMiddleware: Monitor first output latency
    - ErrorHandlerMiddleware: Error handling
    
    Subclasses should define:
    - ALLOWED_INPUT_TYPES: List[ChunkType] - Allowed input chunk types (alias for INPUT_TYPES)
    - LATENCY_METRIC_NAME: str - Latency metric name (default: station name)
    - LATENCY_OUTPUT_TYPES: List[ChunkType] (optional) - Output types to monitor (default: all)
    - EMITS_COMPLETION: bool - Whether to emit completion when stream ends
    """
    
    # Station role
    ROLE = StationRole.STREAM
    
    # Subclasses should override these
    ALLOWED_INPUT_TYPES: list = []  # e.g., [ChunkType.TEXT] (alias for INPUT_TYPES)
    LATENCY_METRIC_NAME: str = ""  # e.g., "agent_first_token"
    LATENCY_OUTPUT_TYPES: list = None  # e.g., [ChunkType.AUDIO] (None = all types)
    
    # Stream stations typically emit completion when done
    EMITS_COMPLETION: bool = True
    AWAITS_COMPLETION: bool = False
    
    def __init__(
        self,
        timeout_seconds: Optional[float] = None,
        enable_interrupt_detection: bool = True,
        name: Optional[str] = None,
        output_role: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize stream station.
        
        Args:
            timeout_seconds: Processing timeout in seconds (None = no timeout)
            enable_interrupt_detection: Enable interrupt detection during streaming
            name: Station name (defaults to class name)
            output_role: Output chunk role ("user" or "bot"), None means don't override
            **kwargs: Additional arguments for base Station
        """
        super().__init__(name=name, output_role=output_role, **kwargs)
        self.timeout_seconds = timeout_seconds
        self.enable_interrupt_detection = enable_interrupt_detection
        
        # Set latency metric name from class attribute if not overridden
        if not self.LATENCY_METRIC_NAME:
            self.LATENCY_METRIC_NAME = f"{self.name.lower()}_first_output"
    
    @property
    def INPUT_TYPES(self) -> list:
        """Alias for ALLOWED_INPUT_TYPES for compatibility"""
        return self.ALLOWED_INPUT_TYPES


class BufferStation(Station):
    """
    Buffer/aggregation station base class.
    
    Characteristics:
    - Collects data, outputs on completion signal trigger
    - Has internal buffer
    - Processing usually instantaneous
    - AWAITS_COMPLETION = True (needs upstream completion to flush)
    - May EMITS_COMPLETION = True (if downstream also needs trigger)
    
    Use cases: TextAggregator, SentenceAggregator, ASR (buffer audio, trigger on completion)
    
    Default middlewares (auto-applied via decorator):
    - InputValidatorMiddleware: Validate input types (subclass specifies allowed_types)
    - SignalHandlerMiddleware: Handle CONTROL_STATE_RESET (clear buffer)
    - ErrorHandlerMiddleware: Error handling
    
    Subclasses should define:
    - ALLOWED_INPUT_TYPES: List[ChunkType] - Allowed input chunk types
    - EMITS_COMPLETION: bool - Whether to emit completion after flush
    
    Subclasses should implement:
    - on_completion(): Handle completion signal (flush buffer)
    """
    
    # Station role
    ROLE = StationRole.BUFFER
    
    # Subclasses should override these
    ALLOWED_INPUT_TYPES: list = []  # e.g., [ChunkType.TEXT_DELTA]
    
    # Buffer stations await completion to flush, may also emit completion
    EMITS_COMPLETION: bool = False  # Subclass can override
    AWAITS_COMPLETION: bool = True  # Buffer stations need completion signal
    
    def __init__(self, name: Optional[str] = None, output_role: Optional[str] = None, **kwargs):
        """
        Initialize buffer station.
        
        Args:
            name: Station name (defaults to class name)
            output_role: Output chunk role ("user" or "bot"), None means don't override
            **kwargs: Additional arguments for base Station
        """
        super().__init__(name=name, output_role=output_role, **kwargs)
    
    @property
    def INPUT_TYPES(self) -> list:
        """Alias for ALLOWED_INPUT_TYPES for compatibility"""
        return self.ALLOWED_INPUT_TYPES


class DetectorStation(Station):
    """
    Detector/event emitter station base class.
    
    Characteristics:
    - State machine logic
    - Detects pattern changes, emits completion signals
    - Passthrough data + emit signals on state change
    - EMITS_COMPLETION = True (emits completion when state changes, e.g., VAD_END)
    
    Use cases: VAD, TurnDetector
    
    Default middlewares (auto-applied via decorator):
    - InputValidatorMiddleware: Validate input types (subclass specifies allowed_types)
    - SignalHandlerMiddleware: Handle CONTROL_STATE_RESET
    - ErrorHandlerMiddleware: Error handling
    
    Subclasses should define:
    - ALLOWED_INPUT_TYPES: List[ChunkType] - Allowed input chunk types
    """
    
    # Station role
    ROLE = StationRole.DETECTOR
    
    # Subclasses should override these
    ALLOWED_INPUT_TYPES: list = []  # e.g., [ChunkType.AUDIO]
    
    # Detectors emit completion signals on state change
    EMITS_COMPLETION: bool = True
    AWAITS_COMPLETION: bool = False
    
    def __init__(self, name: Optional[str] = None, output_role: Optional[str] = None, **kwargs):
        """
        Initialize detector station.
        
        Args:
            name: Station name (defaults to class name)
            output_role: Output chunk role ("user" or "bot"), None means don't override
            **kwargs: Additional arguments for base Station
        """
        super().__init__(name=name, output_role=output_role, **kwargs)
    
    @property
    def INPUT_TYPES(self) -> list:
        """Alias for ALLOWED_INPUT_TYPES for compatibility"""
        return self.ALLOWED_INPUT_TYPES
