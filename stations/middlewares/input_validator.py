"""
Input Validator Middleware

Validates and filters input chunks before processing.
"""

from typing import AsyncIterator, Callable, Optional
from core.middleware import Middleware, NextHandler
from core.chunk import Chunk, ChunkType, is_text_chunk


class InputValidatorMiddleware(Middleware):
    """
    Validates input chunks and filters invalid ones.
    
    Can validate:
    - Chunk type (e.g., only TEXT chunks)
    - Content presence (non-empty)
    - Source attribute (e.g., only from "agent")
    - Custom validation logic
    """
    
    def __init__(
        self,
        allowed_types: Optional[list[ChunkType]] = None,
        check_empty: bool = True,
        required_source: Optional[str] = None,
        custom_validator: Optional[Callable[[Chunk], bool]] = None,
        passthrough_on_invalid: bool = True,
        name: str = "InputValidator"
    ):
        """
        Initialize input validator middleware.
        
        Args:
            allowed_types: List of allowed chunk types (None = all types)
            check_empty: Whether to check for empty content
            required_source: Required source attribute value (None = any source)
            custom_validator: Custom validation function (returns True if valid)
            passthrough_on_invalid: Whether to passthrough invalid chunks
            name: Middleware name
        """
        super().__init__(name)
        self.allowed_types = allowed_types
        self.check_empty = check_empty
        self.required_source = required_source
        self.custom_validator = custom_validator
        self.passthrough_on_invalid = passthrough_on_invalid
    
    async def process(self, chunk: Chunk, next_handler: NextHandler) -> AsyncIterator[Chunk]:
        """
        Validate input and forward to next handler if valid.
        
        Args:
            chunk: Input chunk
            next_handler: Next handler in chain
            
        Yields:
            Validated chunks or passthrough on invalid
        """
        # Check chunk type
        if self.allowed_types and chunk.type not in self.allowed_types:
            self.logger.debug(f"Chunk type {chunk.type} not in allowed types, skipping")
            if self.passthrough_on_invalid:
                yield chunk
            return
        
        # Check source
        if self.required_source and hasattr(chunk, 'source'):
            if chunk.source != self.required_source:
                self.logger.debug(
                    f"Chunk source '{chunk.source}' != required '{self.required_source}', skipping"
                )
                if self.passthrough_on_invalid:
                    yield chunk
                return
        
        # Check empty content for text chunks
        if self.check_empty and is_text_chunk(chunk):
            # Extract text content
            if hasattr(chunk, 'content'):
                text = chunk.content
            else:
                text = str(chunk.data) if chunk.data else ""
            
            if not text or not text.strip():
                self.logger.debug("Empty text content, skipping")
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

