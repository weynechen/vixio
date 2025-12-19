"""
AgentStation - LLM Agent Integration

Input: TEXT (complete user input from TextAggregator)
Output: TEXT_DELTA (streaming) + EVENT_STREAM_COMPLETE

Completion Contract:
- AWAITS_COMPLETION: False (triggered by TEXT data, not completion signal)
- EMITS_COMPLETION: True (emits completion when stream ends, triggers SentenceAggregator flush)

Note: Agent is the central processing node. All text must go through Agent,
even if it's just a passthrough (echo agent) or translation agent.

Refactored with middleware pattern for clean separation of concerns.

Vision Support:
- Extracts visual_context from chunk.metadata
- Processes through VisionStrategy (Describe or Passthrough)
- Passes MultimodalMessage to Agent
"""

import asyncio
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, List
from vixio.core.station import StreamStation
from vixio.core.chunk import Chunk, ChunkType, TextDeltaChunk, is_text_chunk
from vixio.core.middleware import with_middlewares
from vixio.stations.middlewares import (
    TimeoutHandlerMiddleware
)
from vixio.providers.agent import AgentProvider
from vixio.providers.vision import (
    ImageContent, 
    VisionStrategy, 
    PassthroughStrategy
)


@with_middlewares(
    # Monitor timeout (custom configuration for Agent)
    TimeoutHandlerMiddleware(
        timeout_seconds=30.0,  # Will be overridden in __init__
        emit_timeout_event=True,
        send_interrupt_signal=True
    )
    # Note: StreamStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - SignalHandlerMiddleware (handles CONTROL_STATE_RESET)
    # - InterruptDetectorMiddleware (detects turn_id changes)
    # - LatencyMonitorMiddleware (uses LATENCY_METRIC_NAME)
    # - ErrorHandlerMiddleware (error handling)
)
class AgentStation(StreamStation):
    """
    Agent workstation: Processes text through LLM agent.
    
    Input: TEXT (complete user input)
    Output: TEXT_DELTA (streaming) + EVENT_STREAM_COMPLETE
    
    Completion Contract:
    - Does NOT await completion (triggered by TEXT data)
    - Emits completion when stream ends (triggers SentenceAggregator flush)
    
    Note: All TEXT_DELTA output has source="agent" to distinguish from ASR output.
    """
    
    # StreamStation configuration
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    LATENCY_METRIC_NAME = "agent_first_token"
    
    # Completion contract: emit completion when stream ends
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = False  # Triggered by TEXT data, not completion signal
    
    def __init__(
        self,
        agent_provider: AgentProvider,
        vision_strategy: Optional[VisionStrategy] = None,
        timeout_seconds: Optional[float] = 30.0,
        name: str = "agent"  # Lowercase for consistent source tracking
    ):
        """
        Initialize Agent station.
        
        Args:
            agent_provider: Agent provider instance
            vision_strategy: Vision processing strategy (default: PassthroughStrategy)
                - PassthroughStrategy: Pass images directly to VLM
                - DescribeStrategy: Convert images to text descriptions for regular LLM
            timeout_seconds: Timeout for agent processing (default: 30s, None = no timeout)
            name: Station name
        """
        super().__init__(
            name=name,
            output_role="bot",
            timeout_seconds=timeout_seconds,
            enable_interrupt_detection=True
        )
        self.agent = agent_provider
        self.vision_strategy = vision_strategy or PassthroughStrategy()
        self._streaming_generator: Optional[AsyncIterator] = None
    
    def set_session_id(self, session_id: str) -> None:
        """
        Set session ID for this station and propagate to agent provider.
        
        This ensures the agent's conversation memory uses the same session_id
        as the Pipeline (which comes from SessionManager's connection_id).
        
        Args:
            session_id: Session identifier from SessionManager
        """
        # Call parent to set station's session_id and rebind logger
        super().set_session_id(session_id)
        
        # Propagate session_id to agent provider for conversation memory
        if hasattr(self.agent, 'set_session_id'):
            self.agent.set_session_id(session_id)
            self.logger.debug(f"Session ID propagated to agent provider: {session_id[:8]}...")
    
    def _configure_middlewares_hook(self, middlewares: list) -> None:
        """
        Hook called when middlewares are attached.
        
        Allows customizing middleware settings after attachment.
        """
        for middleware in middlewares:
            if middleware.__class__.__name__ == 'TimeoutHandlerMiddleware':
                middleware.timeout_seconds = self.timeout_seconds
            elif middleware.__class__.__name__ == 'SignalHandlerMiddleware':
                middleware.on_interrupt = self._handle_interrupt
    
    async def _handle_interrupt(self) -> None:
        """
        Handle interrupt signal.
        
        Called by SignalHandlerMiddleware when CONTROL_STATE_RESET received.
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
    
    def _extract_visual_context(self, chunk: Chunk) -> Optional[List[ImageContent]]:
        """
        Extract visual context from chunk metadata.
        
        Args:
            chunk: Input chunk
            
        Returns:
            List of ImageContent or None
        """
        visual_ctx = chunk.metadata.get("visual_context")
        if visual_ctx is None:
            return None
        
        # Support single image or list of images
        if isinstance(visual_ctx, ImageContent):
            return [visual_ctx]
        elif isinstance(visual_ctx, list):
            return [img for img in visual_ctx if isinstance(img, ImageContent)]
        else:
            self.logger.warning(f"Unknown visual_context type: {type(visual_ctx)}")
            return None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process chunk through Agent - CORE LOGIC ONLY.
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (TEXT)
        - Do NOT passthrough - DAG handles routing to downstream nodes
        - Output: TEXT_DELTA (streaming)
        
        Core logic:
        - Extract text from chunk
        - Extract visual context from metadata
        - Process through VisionStrategy
        - Stream agent response as TEXT_DELTA chunks
        
        Note: Middlewares handle signal processing, validation, events, timeout,
        interrupt detection, latency monitoring, and error handling.
        """
        # Only process TEXT chunks
        if chunk.type != ChunkType.TEXT:
            return
            yield  # Makes this an async generator
        
        # Extract text content (unified using chunk.data)
        text = chunk.data if isinstance(chunk.data, str) else (str(chunk.data) if chunk.data else "")
        
        # Extract visual context from metadata
        images = self._extract_visual_context(chunk)
        
        # Log extracted content
        if images:
            self.logger.info(f"[Agent] Extracted text: {repr(text)[:100]} with {len(images)} image(s)")
        else:
            self.logger.info(f"[Agent] Extracted text: {repr(text)[:100]}")
        
        # Check if agent is initialized
        if not self.agent.is_initialized():
            self.logger.error("Agent not initialized, cannot process text")
            raise RuntimeError("Agent not initialized")
        
        self.logger.info(
            f"Agent processing: '{text[:50]}...' "
            f"(turn_id={chunk.turn_id})"
        )
        
        # Process through VisionStrategy
        multimodal_msg = await self.vision_strategy.process(text, images)
        
        # Stream agent response
        agent_stream = self.agent.chat(multimodal_msg)
        self._streaming_generator = agent_stream
        
        try:
            async for delta in agent_stream:
                if delta:
                    yield TextDeltaChunk(
                        type=ChunkType.TEXT_DELTA,
                        data=delta,
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
            
            # Stream complete - emit completion signal (triggers SentenceAggregator flush)
            yield self.emit_completion(
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
        
        finally:
            await agent_stream.aclose()
            self._streaming_generator = None
            self.logger.debug("Agent stream closed")
