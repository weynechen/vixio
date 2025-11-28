"""
Middleware system for Station processing pipeline.

Allows composing reusable processing logic as middleware chain.
Each middleware can intercept, transform, or enhance chunk processing.

Chain of Responsibility pattern
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Optional, TYPE_CHECKING
from functools import wraps
from core.chunk import Chunk
from loguru import logger

if TYPE_CHECKING:
    from core.station import Station


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
        self.logger = logger.bind(middleware=self.name)
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
        self.logger = logger.bind(station=station.name, middleware=self.name)
    
    @abstractmethod
    async def process(
        self,
        chunk: Chunk,
        next_handler: NextHandler
    ) -> AsyncIterator[Chunk]:
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
        pass


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


def with_middlewares(*middlewares: Middleware):
    """
    Decorator to add middlewares to a station's process_chunk method.
    
    Usage:
        ```python
        @with_middlewares(
            SignalHandlerMiddleware(),
            InputValidatorMiddleware(),
            EventEmitterMiddleware("START", "STOP")
        )
        class MyStation(Station):
            async def process_chunk(self, chunk):
                # Core logic only
                yield processed_chunk
        ```
    
    Args:
        *middlewares: Middleware instances to use as templates
        
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
                # Clone middlewares for this instance (shallow copy to avoid logger issues)
                self._middlewares = [_clone_middleware(m) for m in middlewares]
                
                # Attach to station
                for middleware in self._middlewares:
                    middleware.attach(self)
                
                # Call configuration hook if exists
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

