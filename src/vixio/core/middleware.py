"""
Middleware system for Station processing pipeline.

Allows composing reusable processing logic as middleware chain.
Each middleware can intercept, transform, or enhance chunk processing.

Chain of Responsibility pattern
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Callable, Optional, TYPE_CHECKING
from functools import wraps
from vixio.core.chunk import Chunk
from loguru import logger

if TYPE_CHECKING:
    from vixio.core.station import Station


# Type alias for the next handler in the chain
NextHandler = Callable[[Chunk], AsyncIterator[Chunk]]


class Middleware(ABC):
    """
    Base middleware class for processing pipeline.
    
    Middleware can:
    - Intercept chunks before/after processing
    - Transform input/output
    - Add side effects (logging, monitoring, etc.)
    - Short-circuit processing
    """
    
    def __init__(self, name: Optional[str] = None):
        """
        Initialize middleware.
        
        Args:
            name: Middleware name for logging (defaults to class name)
        """
        self.name = name or self.__class__.__name__
        self.logger = logger.bind(component=self.name)
        self.station: Optional['Station'] = None
    
    def attach(self, station: 'Station') -> None:
        """
        Attach middleware to a station.
        
        Called when middleware is registered to a station.
        Allows middleware to access station context.
        
        Args:
            station: The station this middleware is attached to
        """
        self.station = station
        self.logger = logger.bind(component=self.name, attached_to=station.name)
    
    @abstractmethod
    async def process(
        self,
        chunk: Chunk,
        next_handler: NextHandler
    ) -> AsyncGenerator[Chunk, None]:
        """
        Process chunk with access to next handler in chain.
        
        Args:
            chunk: Input chunk to process
            next_handler: Next handler in the middleware chain
            
        Yields:
            Processed chunks
            
        Example:
            ```python
            async def process(self, chunk, next_handler):
                # Before processing
                yield EventChunk(type=ChunkType.EVENT_START)
                
                # Call next handler
                async for result in next_handler(chunk):
                    yield result
                
                # After processing
                yield EventChunk(type=ChunkType.EVENT_STOP)
            ```
        """
        yield  # type: ignore[misc]
        raise NotImplementedError


class DataMiddleware(Middleware):
    """
    Data-only middleware - processes only Data chunks.
    
    Signal chunks are passed through unchanged to next handler.
    Subclasses implement process_data() for data-specific logic.
    
    Use cases:
    - Input validation (check data types, content)
    - Latency monitoring (track data processing time)
    - Timeout handling (abort long-running data processing)
    - Interrupt detection (detect turn changes on data)
    """
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Route chunk based on type.
        
        Signal chunks: Pass through unchanged
        Data chunks: Process with process_data()
        """
        if chunk.is_signal():
            # Signals pass through unchanged
            async for result in next_handler(chunk):
                yield result
            return
        
        # Data chunks - call subclass implementation
        async for result in self.process_data(chunk, next_handler):
            yield result
    
    @abstractmethod
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process data chunk.
        
        Subclasses implement data-specific logic here.
        
        Args:
            chunk: Data chunk (guaranteed to be data, not signal)
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks
        """
        yield  # type: ignore[misc]
        raise NotImplementedError


class SignalMiddleware(Middleware):
    """
    Signal-only middleware - processes only Signal chunks.
    
    Data chunks are passed through unchanged to next handler.
    Subclasses implement process_signal() for signal-specific logic.
    
    Use cases:
    - Signal routing (handle CONTROL_STATE_RESET)
    - Multi-signal handling (route different signal types)
    - Signal logging/monitoring
    """
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncIterator[Chunk]:
        """
        Route chunk based on type.
        
        Data chunks: Pass through unchanged
        Signal chunks: Process with process_signal()
        """
        if not chunk.is_signal():
            # Data passes through unchanged
            async for result in next_handler(chunk):
                yield result
            return
        
        # Signal chunks - call subclass implementation
        async for result in self.process_signal(chunk, next_handler):
            yield result
    
    @abstractmethod
    async def process_signal(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process signal chunk.
        
        Subclasses implement signal-specific logic here.
        
        Args:
            chunk: Signal chunk (guaranteed to be signal, not data)
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks (typically yield chunk to pass signal through)
        """
        yield  # type: ignore[misc]
        raise NotImplementedError


class UniversalMiddleware(Middleware):
    """
    Universal middleware - processes both Data and Signal chunks.
    
    Subclasses implement both process_data() and process_signal().
    
    Use cases:
    - Event emission (emit events before/after any processing)
    - Error handling (catch errors from both data and signal processing)
    - Metrics collection (track all chunk types)
    """
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Route chunk to appropriate handler.
        
        Signal chunks: process_signal()
        Data chunks: process_data()
        """
        if chunk.is_signal():
            async for result in self.process_signal(chunk, next_handler):
                yield result
        else:
            async for result in self.process_data(chunk, next_handler):
                yield result
    
    @abstractmethod
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process data chunk.
        
        Args:
            chunk: Data chunk
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks
        """
        yield  # type: ignore[misc]
        raise NotImplementedError
    
    @abstractmethod
    async def process_signal(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Process signal chunk.
        
        Args:
            chunk: Signal chunk
            next_handler: Next handler in chain
            
        Yields:
            Processed chunks
        """
        yield  # type: ignore[misc]
        raise NotImplementedError


class MiddlewareChain:
    """
    Chains multiple middlewares together.
    
    Middlewares are executed in order, each calling the next in the chain.
    The final handler is the station's core processing logic.
    """
    
    def __init__(self, middlewares: list[Middleware], final_handler: NextHandler):
        """
        Initialize middleware chain.
        
        Args:
            middlewares: List of middlewares to chain (executed in order)
            final_handler: Final handler to call after all middlewares
        """
        self.middlewares = middlewares
        self.final_handler = final_handler
    
    async def execute(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Execute the middleware chain.
        
        Args:
            chunk: Input chunk
            
        Yields:
            Processed chunks
        """
        if not self.middlewares:
            # No middlewares, call final handler directly
            async for result in self.final_handler(chunk):
                yield result
            return
        
        # Build chain from last to first
        handler = self.final_handler
        for middleware in reversed(self.middlewares):
            # Capture current handler in closure
            handler = self._wrap_middleware(middleware, handler)
        
        # Execute chain
        async for result in handler(chunk):
            yield result
    
    def _wrap_middleware(
        self,
        middleware: Middleware,
        next_handler: NextHandler
    ) -> NextHandler:
        """
        Wrap a middleware with the next handler.
        
        Args:
            middleware: Middleware to wrap
            next_handler: Next handler in chain
            
        Returns:
            Wrapped handler function
        """
        async def wrapped(chunk: Chunk) -> AsyncIterator[Chunk]:
            async for result in middleware.process(chunk, next_handler):
                yield result
        
        return wrapped


def _clone_middleware(middleware: Middleware) -> Middleware:
    """
    Clone a middleware instance without using deepcopy (to avoid logger serialization issues).
    
    Creates a new instance of the same class with the same attributes,
    except for logger which will be recreated in attach().
    """
    # Get the class
    cls = middleware.__class__
    
    # Get init parameters from instance attributes
    # This is a simple shallow copy of non-callable, non-logger attributes
    init_kwargs = {}
    for key, value in middleware.__dict__.items():
        # Skip private attributes, logger, and station reference
        if key.startswith('_') or key == 'logger' or key == 'station':
            continue
        # Skip callable attributes (methods)
        if callable(value):
            continue
        # Copy the value
        init_kwargs[key] = value
    
    # Create new instance with same parameters
    # Note: This assumes middleware __init__ accepts these as kwargs
    try:
        new_middleware = cls(**init_kwargs)
    except TypeError:
        # If direct kwargs don't work, try to instantiate with minimal args
        # and then copy attributes
        new_middleware = cls.__new__(cls)
        new_middleware.__dict__.update(init_kwargs)
        # Reinitialize name and logger
        if not hasattr(new_middleware, 'name'):
            new_middleware.name = middleware.name
    
    return new_middleware


def _create_default_middlewares(station_instance):
    """
    Create default middlewares based on station type.
    
    Args:
        station_instance: The station instance
        
    Returns:
        List of default middleware instances
    """
    from vixio.stations.middlewares.input_validator import InputValidatorMiddleware
    from vixio.stations.middlewares.signal_handler import SignalHandlerMiddleware
    from vixio.stations.middlewares.interrupt_detector import InterruptDetectorMiddleware
    from vixio.stations.middlewares.latency_monitor import LatencyMonitorMiddleware
    from vixio.stations.middlewares.error_handler import ErrorHandlerMiddleware
    
    # Import station types to check isinstance
    # Use TYPE_CHECKING import to avoid circular imports
    from vixio.core.station import StreamStation, BufferStation, DetectorStation
    
    default_middlewares = []
    
    # Common middlewares for all typed stations
    if isinstance(station_instance, (StreamStation, BufferStation, DetectorStation)):
        # 1. InputValidator - all stations validate input
        if hasattr(station_instance, 'ALLOWED_INPUT_TYPES') and station_instance.ALLOWED_INPUT_TYPES:
            default_middlewares.append(
                InputValidatorMiddleware(
                    allowed_types=station_instance.ALLOWED_INPUT_TYPES,
                    check_empty=True,  # Always check for empty content
                    passthrough_on_invalid=True
                )
            )
        
        # 2. SignalHandler - all stations handle signals
        default_middlewares.append(SignalHandlerMiddleware())
        
        # 3. ErrorHandler - all stations handle errors
        default_middlewares.append(ErrorHandlerMiddleware())
    
    # StreamStation-specific middlewares
    if isinstance(station_instance, StreamStation):
        # InterruptDetector - detect turn changes during streaming
        if station_instance.enable_interrupt_detection:
            default_middlewares.insert(-1, InterruptDetectorMiddleware())  # Insert before ErrorHandler
        
        # LatencyMonitor - monitor first output latency
        if hasattr(station_instance, 'LATENCY_METRIC_NAME') and station_instance.LATENCY_METRIC_NAME:
            # Check if station specifies which output types to monitor
            output_types = None
            if hasattr(station_instance, 'LATENCY_OUTPUT_TYPES'):
                output_types = station_instance.LATENCY_OUTPUT_TYPES
            
            default_middlewares.insert(-1, LatencyMonitorMiddleware(
                metric_name=station_instance.LATENCY_METRIC_NAME,
                output_types=output_types
            ))
    
    return default_middlewares


def with_middlewares(*middlewares: Middleware):
    """
    Decorator to add middlewares to a station's process_chunk method.
    
    Supports automatic default middlewares for StreamStation, BufferStation, DetectorStation:
    - Default middlewares are applied based on station type
    - Explicit middlewares (decorator arguments) are prepended (higher priority)
    - Station subclasses can override _get_custom_middlewares() to add more
    
    Usage:
        ```python
        # StreamStation with automatic defaults + custom middlewares
        @with_middlewares(
            TimeoutHandlerMiddleware(30.0)  # Custom middleware
        )
        class AgentStation(StreamStation):
            ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
            LATENCY_METRIC_NAME = "agent_first_token"
            
            async def process_chunk(self, chunk):
                # Core logic only
                yield processed_chunk
        ```
    
    Args:
        *middlewares: Middleware instances to use as templates (prepended to defaults)
        
    Returns:
        Decorator function
        
    Note: Each station instance gets its own cloned middlewares.
    For per-instance configuration, override _configure_middlewares_hook() in your station.
    """
    def decorator(cls):
        # Store original process_chunk method
        original_process_chunk = cls.process_chunk
        
        # Store middleware templates on class
        cls._middleware_templates = middlewares
        
        # Create wrapped method
        @wraps(original_process_chunk)
        async def wrapped_process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
            # Create instance-specific middlewares on first call
            if not hasattr(self, '_middlewares'):
                # 1. Get default middlewares from station type
                default_middlewares = _create_default_middlewares(self)
                
                # 2. Prepend explicit middlewares (decorator args) - higher priority
                explicit_middlewares = list(middlewares)
                
                # 3. Allow station to add custom middlewares via hook
                custom_middlewares = []
                if hasattr(self, '_get_custom_middlewares'):
                    custom_middlewares = self._get_custom_middlewares() or []
                
                # 4. Merge: explicit + custom + defaults
                all_middleware_templates = explicit_middlewares + custom_middlewares + default_middlewares
                
                # 5. Clone middlewares for this instance
                self._middlewares = [_clone_middleware(m) for m in all_middleware_templates]
                
                # 6. Attach to station
                for middleware in self._middlewares:
                    middleware.attach(self)
                
                # 7. Call configuration hook if exists
                if hasattr(self, '_configure_middlewares_hook'):
                    self._configure_middlewares_hook(self._middlewares)
            
            # Create chain with original method as final handler
            chain = MiddlewareChain(
                self._middlewares,
                lambda c: original_process_chunk(self, c)
            )
            
            # Execute chain
            async for result in chain.execute(chunk):
                yield result
        
        # Replace process_chunk with wrapped version
        cls.process_chunk = wrapped_process_chunk
        return cls
    
    return decorator

