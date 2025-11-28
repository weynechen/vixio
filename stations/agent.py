"""
AgentStation - LLM Agent Integration

Input: TEXT (complete user input from TextAggregator)
Output: TEXT_DELTA (streaming, source="agent") + EVENT_AGENT_START/STOP

Note: Agent is the central processing node. All text must go through Agent,
even if it's just a passthrough (echo agent) or translation agent.

Refactored with middleware pattern for clean separation of concerns.
"""

import asyncio
from typing import AsyncIterator, Optional
from core.station import Station
from core.chunk import Chunk, ChunkType, TextDeltaChunk, is_text_chunk
from core.middleware import with_middlewares
from stations.middlewares import (
    SignalHandlerMiddleware,
    InputValidatorMiddleware,
    EventEmitterMiddleware,
    TimeoutHandlerMiddleware,
    InterruptDetectorMiddleware,
    LatencyMonitorMiddleware,
    ErrorHandlerMiddleware
)
from providers.agent import AgentProvider


@with_middlewares(
    # Handle signals (CONTROL_INTERRUPT, etc.)
    SignalHandlerMiddleware(
        on_interrupt=lambda: None,  # Will be set in __init__
        cancel_streaming=False  # Handled by agent stream closure
    ),
    # Validate input (only non-empty TEXT chunks)
    InputValidatorMiddleware(
        allowed_types=[ChunkType.TEXT],
        check_empty=True,
        passthrough_on_invalid=True
    ),
    # Emit AGENT_START/STOP events
    EventEmitterMiddleware(
        start_event=ChunkType.EVENT_AGENT_START,
        stop_event=ChunkType.EVENT_AGENT_STOP,
        emit_on_interrupt=True
    ),
    # Monitor timeout
    TimeoutHandlerMiddleware(
        timeout_seconds=30.0,  # Will be overridden in __init__
        emit_timeout_event=True,
        send_interrupt_signal=True
    ),
    # Detect interrupts (turn ID changes)
    InterruptDetectorMiddleware(check_interval=5),
    # Monitor TTFT (Time To First Token)
    LatencyMonitorMiddleware(
        record_first_token=True,
        metric_name="agent_first_token"
    ),
    # Handle errors
    ErrorHandlerMiddleware(
        emit_error_event=True,
        suppress_errors=False
    )
)
class AgentStation(Station):
    """
    Agent workstation: Processes text through LLM agent.
    
    Input: TEXT (complete user input)
    Output: TEXT_DELTA (streaming, source="agent") + EVENT_AGENT_START/STOP
    
    Note: All TEXT_DELTA output has source="agent" to distinguish from ASR output.
    """
    
    def __init__(
        self,
        agent_provider: AgentProvider,
        timeout_seconds: Optional[float] = 30.0,
        name: str = "Agent"
    ):
        """
        Initialize Agent station.
        
        Args:
            agent_provider: Agent provider instance
            timeout_seconds: Timeout for agent processing (default: 30s, None = no timeout)
            name: Station name
        """
        super().__init__(name=name)
        self.agent = agent_provider
        self._streaming_generator: Optional[AsyncIterator] = None
        self.timeout_seconds = timeout_seconds
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        # Find TimeoutHandlerMiddleware and update timeout
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'TimeoutHandlerMiddleware':
                middleware.timeout_seconds = self.timeout_seconds
            elif middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                # Set interrupt callback to reset agent conversation
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_INTERRUPT received.
        """
        # Close ongoing streaming generator
        if self._streaming_generator is not None:
            try:
                await self._streaming_generator.aclose()
                self.logger.info("Closed Agent streaming generator")
            except Exception as e:
                self.logger.warning(f"Error closing Agent stream: {e}")
            finally:
                self._streaming_generator = None
        
        # Reset conversation
        if self.agent.is_initialized():
            await self.agent.reset_conversation()
            self.logger.info("Agent conversation reset")
    
    async def cleanup(self) -> None:
        """
        Cleanup agent resources.
        
        Calls shutdown on the agent provider to release resources properly.
        """
        try:
            if self.agent and hasattr(self.agent, 'shutdown'):
                await self.agent.shutdown()
                self.logger.debug("Agent provider cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up agent provider: {e}")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through Agent - CORE LOGIC ONLY.
        
        All cross-cutting concerns (signal handling, validation, events, timeout,
        interrupt detection, latency monitoring, error handling) are handled by
        middlewares via the @with_middlewares decorator.
        
        This method now contains ONLY the core business logic:
        - Extract text from chunk
        - Check agent initialization
        - Stream agent response as TEXT_DELTA chunks
        
        Note: Middlewares handle the rest automatically in the correct order.
        """
        # Extract text content
        if hasattr(chunk, 'content'):
            text = chunk.content
        else:
            text = str(chunk.data) if chunk.data else ""
        
        # Check if agent is initialized
        if not self.agent.is_initialized():
            self.logger.error("Agent not initialized, cannot process text")
            # ErrorHandlerMiddleware will catch and emit error event
            raise RuntimeError("Agent not initialized")
        
        self.logger.info(
            f"Agent processing: '{text[:50]}...' "
            f"(turn_id={chunk.turn_id})"
        )
        
        # Passthrough input TEXT first for immediate client feedback
        yield chunk
        
        # Stream agent response - CORE BUSINESS LOGIC
        # Store generator reference for cleanup on interrupt
        agent_stream = self.agent.chat(text)
        self._streaming_generator = agent_stream
        
        try:
            async for delta in agent_stream:
                if delta:
                    # Yield text delta
                    # Note: LatencyMonitorMiddleware automatically records first token
                    yield TextDeltaChunk(
                        type=ChunkType.TEXT_DELTA,
                        delta=delta,
                        source="agent",  # Mark as agent output
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
        
        finally:
            # Ensure generator is properly closed
            await agent_stream.aclose()
            self._streaming_generator = None
            self.logger.debug("Agent stream closed")
