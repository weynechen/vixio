"""
AgentStation - LLM Agent Integration

Input: TEXT (complete user input from TextAggregator)
Output: TEXT_DELTA (streaming, source="agent") + EVENT_AGENT_START/STOP

Note: Agent is the central processing node. All text must go through Agent,
even if it's just a passthrough (echo agent) or translation agent.
"""

import asyncio
from typing import AsyncIterator, Optional
from core.station import Station
from core.chunk import Chunk, ChunkType, TextChunk, TextDeltaChunk, EventChunk, is_text_chunk
from providers.agent import AgentProvider
from utils import get_latency_monitor


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
        self.timeout_seconds = timeout_seconds
        self._is_thinking = False
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
    
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
        Process chunk through Agent.
        
        Logic:
        - On TEXT: Send to agent, stream TEXT_DELTA responses
        - Emit EVENT_AGENT_START before first response
        - Emit EVENT_AGENT_STOP after last response
        - On CONTROL_INTERRUPT: Reset conversation
        """
        # Handle signals
        if chunk.is_signal():
            # Reset conversation on interrupt
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                if self.agent.is_initialized():
                    await self.agent.reset_conversation()
                    self.logger.info("Agent conversation reset")
                self._is_thinking = False
            
            # Passthrough signals
            yield chunk
            return
        
        # Process text chunks
        if is_text_chunk(chunk):
            # Extract text content
            if hasattr(chunk, 'content'):
                text = chunk.content
            else:
                text = str(chunk.data) if chunk.data else ""
            
            if not text or not text.strip():
                self.logger.debug("Skipping empty text for Agent")
                yield chunk  # Passthrough
                return
            
            # Check if agent is initialized
            if not self.agent.is_initialized():
                self.logger.error("Agent not initialized, cannot process text")
                yield EventChunk(
                    type=ChunkType.EVENT_ERROR,
                    event_data={"error": "Agent not initialized", "source": "Agent"},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                yield chunk  # Passthrough
                return
            
            self.logger.info(f"Agent processing: '{text[:50]}...' (chunk.turn_id={chunk.turn_id}, self.current_turn_id={self.current_turn_id})")
            
            # Passthrough input TEXT first for immediate client feedback
            # This allows ASR results to be sent immediately before Agent processing
            yield chunk
            
            # Emit AGENT_START event
            yield EventChunk(
                type=ChunkType.EVENT_AGENT_START,
                event_data={"input_text": text[:100]},
                source_station=self.name,
                session_id=chunk.session_id
            )
            self._is_thinking = True
            
            # Stream agent response with timeout
            try:
                delta_count = 0
                start_time = asyncio.get_event_loop().time()
                first_token_recorded = False
                
                async for delta in self.agent.chat(text):
                    # Check if interrupted (turn_id changed)
                    if self.control_bus:
                        current_turn = self.control_bus.get_current_turn_id()
                        if current_turn > self.current_turn_id:
                            self.logger.info(f"Agent interrupted: turn {self.current_turn_id} -> {current_turn}")
                            break
                    
                    # Check timeout
                    if self.timeout_seconds:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        if elapsed > self.timeout_seconds:
                            self.logger.warning(f"Agent processing timeout after {elapsed:.1f}s")
                            
                            # Send interrupt signal via ControlBus
                            if self.control_bus:
                                await self.control_bus.send_interrupt(
                                    source=self.name,
                                    reason="agent_timeout",
                                    metadata={
                                        "timeout_seconds": self.timeout_seconds,
                                        "elapsed_seconds": elapsed
                                    }
                                )
                            
                            # Emit timeout event
                            yield EventChunk(
                                type=ChunkType.EVENT_TIMEOUT,
                                event_data={
                                    "source": "Agent",
                                    "timeout_seconds": self.timeout_seconds,
                                    "elapsed_seconds": elapsed
                                },
                                source_station=self.name,
                                session_id=chunk.session_id
                            )
                            
                            break
                    
                    if delta:
                        delta_count += 1
                        
                        # Record T3: agent_first_token (TTFT - Time To First Token)
                        if not first_token_recorded:
                            self._latency_monitor.record(
                                chunk.session_id,
                                chunk.turn_id,
                                "agent_first_token"
                            )
                            first_token_recorded = True
                            self.logger.debug("Recorded agent first token (TTFT)")
                        
                        yield TextDeltaChunk(
                            type=ChunkType.TEXT_DELTA,
                            delta=delta,
                            source="agent",  # Mark as agent output
                            session_id=chunk.session_id,
                            turn_id=chunk.turn_id
                        )
                
                self.logger.debug(f"Agent generated {delta_count} text deltas")
                
                # Emit AGENT_STOP event
                yield EventChunk(
                    type=ChunkType.EVENT_AGENT_STOP,
                    event_data={"delta_count": delta_count},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                self._is_thinking = False
            
            except asyncio.TimeoutError:
                self.logger.error(f"Agent processing timed out after {self.timeout_seconds}s")
                
                # Send interrupt signal
                if self.control_bus:
                    await self.control_bus.send_interrupt(
                        source=self.name,
                        reason="agent_timeout",
                        metadata={"timeout_seconds": self.timeout_seconds}
                    )
                
                # Emit timeout event
                yield EventChunk(
                    type=ChunkType.EVENT_TIMEOUT,
                    event_data={"source": "Agent", "timeout_seconds": self.timeout_seconds},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                
                self._is_thinking = False
            
            except Exception as e:
                self.logger.error(f"Agent processing failed: {e}", exc_info=True)
                
                # Emit error event
                yield EventChunk(
                    type=ChunkType.EVENT_ERROR,
                    event_data={"error": str(e), "source": "Agent"},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                
                self._is_thinking = False
        else:
            # Passthrough non-text data
            yield chunk
