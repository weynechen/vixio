"""
AgentStation - LLM Agent Integration

Input: TEXT
Output: TEXT_DELTA (streaming) + EVENT_AGENT_START/STOP
"""

from typing import AsyncIterator
from core.station import Station
from core.chunk import Chunk, ChunkType, TextChunk, TextDeltaChunk, EventChunk, is_text_chunk
from providers.agent import AgentProvider


class AgentStation(Station):
    """
    Agent workstation: Processes text through LLM agent.
    
    Input: TEXT
    Output: TEXT_DELTA (streaming) + EVENT_AGENT_START/STOP
    """
    
    def __init__(self, agent_provider: AgentProvider, name: str = "Agent"):
        """
        Initialize Agent station.
        
        Args:
            agent_provider: Agent provider instance
            name: Station name
        """
        super().__init__(name=name)
        self.agent = agent_provider
        self._is_thinking = False
    
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
            
            self.logger.info(f"Agent processing: '{text[:50]}...'")
            
            # Emit AGENT_START event
            yield EventChunk(
                type=ChunkType.EVENT_AGENT_START,
                event_data={"input_text": text[:100]},
                source_station=self.name,
                session_id=chunk.session_id
            )
            self._is_thinking = True
            
            # Stream agent response
            try:
                delta_count = 0
                async for delta in self.agent.chat(text):
                    if delta:
                        delta_count += 1
                        yield TextDeltaChunk(
                            type=ChunkType.TEXT_DELTA,
                            delta=delta,
                            session_id=chunk.session_id
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
            
            # Passthrough original text chunk
            yield chunk
        else:
            # Passthrough non-text data
            yield chunk
