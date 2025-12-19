"""
Input Validator Middleware

Validates and filters input chunks before processing.
"""

from collections.abc import AsyncIterator, AsyncGenerator
from typing import Callable, Optional
from vixio.core.middleware import DataMiddleware, NextHandler
from vixio.core.chunk import Chunk, ChunkType, is_text_chunk


class InputValidatorMiddleware(DataMiddleware):
    """
    Validates input data chunks and filters invalid ones.
    
    Signal chunks are passed through unchanged.
    
    Can validate:
    - Chunk type (e.g., only TEXT chunks)
    - Content presence (non-empty)
    - Custom validation logic
    
    Note: Source checking is NOT supported in DAG architecture.
    Stations should only care about chunk type, not source.
    DAG routing handles data flow based on ALLOWED_INPUT_TYPES.
    """
    
    def __init__(
        self,
        allowed_types: Optional[list[ChunkType]] = None,
        check_empty: bool = True,
        custom_validator: Optional[Callable[[Chunk], bool]] = None,
        passthrough_on_invalid: bool = False,
        name: str = "InputValidator",
        **kwargs  # Accept but ignore deprecated parameters
    ):
        """
        Initialize input validator middleware.
        
        Args:
            allowed_types: List of allowed chunk types (None = all types)
            check_empty: Whether to check for empty content
            custom_validator: Custom validation function (returns True if valid)
            passthrough_on_invalid: Whether to passthrough invalid chunks (default: False for DAG)
            name: Middleware name
        """
        super().__init__(name)
        self.allowed_types = allowed_types
        self.check_empty = check_empty
        self.custom_validator = custom_validator
        self.passthrough_on_invalid = passthrough_on_invalid
        
        # Warn about deprecated parameters
        if 'required_source' in kwargs and kwargs['required_source']:
            self.logger.warning(
                "required_source is deprecated in DAG architecture. "
                "Stations should not check chunk.source. Ignoring."
            )
    
    async def process_data(self, chunk: Chunk, next_handler: NextHandler) -> AsyncGenerator[Chunk, None]:
        """
        Validate data chunk and forward to next handler if valid.
        
        Args:
            chunk: Data chunk (not signal)
            next_handler: Next handler in chain
            
        Yields:
            Validated chunks
        """
        
        # Check empty content FIRST, before type checking
        # Empty content should ALWAYS be dropped
        if self.check_empty and is_text_chunk(chunk):
            text = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
            
            self.logger.debug(f"[InputValidator] Text chunk detected, extracted text: {repr(text)[:100]}")
            
            if not text or not text.strip():
                self.logger.warning(f"[InputValidator] Dropping empty text chunk: {chunk}")
                return
            else:
                self.logger.debug(f"[InputValidator] Text chunk has content, continuing validation")
        
        # Check chunk type
        if self.allowed_types and chunk.type not in self.allowed_types:
            self.logger.debug(f"Chunk type {chunk.type} not in allowed types, skipping")
            if self.passthrough_on_invalid:
                yield chunk
            return
        
        # Custom validation
        if self.custom_validator:
            try:
                if not self.custom_validator(chunk):
                    self.logger.debug("Custom validation failed, skipping")
                    if self.passthrough_on_invalid:
                        yield chunk
                    return
            except Exception as e:
                self.logger.error(f"Custom validator error: {e}")
                if self.passthrough_on_invalid:
                    yield chunk
                return
        
        # All validations passed, forward to next handler
        async for result in next_handler(chunk):
            yield result

