"""
Unit tests for AgentStation
"""

import pytest
from core.chunk import Chunk, ChunkType, TextChunk, TextDeltaChunk, EventChunk, ControlChunk
from stations.agent import AgentStation
from providers.agent import AgentProvider
from typing import AsyncIterator, Dict, Any, Optional


class MockAgentProvider(AgentProvider):
    """Mock Agent provider for testing"""
    
    def __init__(self):
        super().__init__(name="MockAgent", config={})
        self._response_deltas = ["Hello", " ", "World", "!"]
        self._reset_called = False
        self._chat_called = False
        self._initialized = True  # Start initialized for testing
    
    async def initialize(
        self,
        tools: Optional[list] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Initialize agent"""
        self._initialized = True
    
    async def chat(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """Return configured response deltas"""
        self._chat_called = True
        self._last_message = message
        
        for delta in self._response_deltas:
            yield delta
    
    async def reset_conversation(self) -> None:
        """Track reset calls"""
        self._reset_called = True
    
    def set_response(self, deltas: list):
        """Set the response deltas"""
        self._response_deltas = deltas


@pytest.fixture
def mock_agent():
    """Create mock Agent provider"""
    return MockAgentProvider()


@pytest.fixture
def agent_station(mock_agent):
    """Create AgentStation with mock provider"""
    return AgentStation(mock_agent)


@pytest.mark.asyncio
async def test_agent_processes_text(agent_station, mock_agent):
    """Test AgentStation processes text and streams deltas"""
    session_id = "test-session"
    
    text_chunk = TextChunk(
        type=ChunkType.TEXT,
        content="Hello Agent",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in agent_station.process_chunk(text_chunk):
        chunks.append(chunk)
    
    # Should yield: AGENT_START + TEXT_DELTA * 4 + AGENT_STOP + TEXT (passthrough)
    assert len(chunks) == 7
    assert chunks[0].type == ChunkType.EVENT_AGENT_START
    assert chunks[1].type == ChunkType.TEXT_DELTA
    assert chunks[1].delta == "Hello"
    assert chunks[2].type == ChunkType.TEXT_DELTA
    assert chunks[2].delta == " "
    assert chunks[3].type == ChunkType.TEXT_DELTA
    assert chunks[3].delta == "World"
    assert chunks[4].type == ChunkType.TEXT_DELTA
    assert chunks[4].delta == "!"
    assert chunks[5].type == ChunkType.EVENT_AGENT_STOP
    assert chunks[6].type == ChunkType.TEXT
    
    # Agent should have been called
    assert mock_agent._chat_called
    assert mock_agent._last_message == "Hello Agent"


@pytest.mark.asyncio
async def test_agent_skips_empty_text(agent_station, mock_agent):
    """Test AgentStation skips empty text"""
    session_id = "test-session"
    
    empty_text = TextChunk(
        type=ChunkType.TEXT,
        content="",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in agent_station.process_chunk(empty_text):
        chunks.append(chunk)
    
    # Should only passthrough (no agent processing)
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.TEXT
    assert not mock_agent._chat_called


@pytest.mark.asyncio
async def test_agent_resets_on_interrupt(agent_station, mock_agent):
    """Test AgentStation resets conversation on CONTROL_INTERRUPT"""
    session_id = "test-session"
    
    # Process some text first
    text = TextChunk(
        type=ChunkType.TEXT,
        content="Hello",
        session_id=session_id
    )
    async for _ in agent_station.process_chunk(text):
        pass
    
    # Send interrupt
    interrupt = ControlChunk(
        type=ChunkType.CONTROL_INTERRUPT,
        command="stop",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in agent_station.process_chunk(interrupt):
        chunks.append(chunk)
    
    # Should passthrough interrupt
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.CONTROL_INTERRUPT
    assert mock_agent._reset_called


@pytest.mark.asyncio
async def test_agent_handles_no_response(agent_station, mock_agent):
    """Test AgentStation handles case when agent yields no deltas"""
    session_id = "test-session"
    
    # Set agent to yield no deltas
    mock_agent.set_response([])
    
    text_chunk = TextChunk(
        type=ChunkType.TEXT,
        content="Hello",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in agent_station.process_chunk(text_chunk):
        chunks.append(chunk)
    
    # Should yield: AGENT_START + AGENT_STOP + TEXT (passthrough)
    assert len(chunks) == 3
    assert chunks[0].type == ChunkType.EVENT_AGENT_START
    assert chunks[1].type == ChunkType.EVENT_AGENT_STOP
    assert chunks[2].type == ChunkType.TEXT


@pytest.mark.asyncio
async def test_agent_handles_uninitialized_provider(mock_agent):
    """Test AgentStation handles uninitialized provider"""
    # Create uninitialized agent
    mock_agent._initialized = False
    agent_station = AgentStation(mock_agent)
    
    session_id = "test-session"
    
    text_chunk = TextChunk(
        type=ChunkType.TEXT,
        content="Hello",
        session_id=session_id
    )
    
    chunks = []
    async for chunk in agent_station.process_chunk(text_chunk):
        chunks.append(chunk)
    
    # Should yield: ERROR + TEXT (passthrough)
    assert len(chunks) == 2
    assert chunks[0].type == ChunkType.EVENT_ERROR
    assert "not initialized" in chunks[0].event_data.get("error", "").lower()
    assert chunks[1].type == ChunkType.TEXT


@pytest.mark.asyncio
async def test_agent_passthrough_non_text_chunks(agent_station):
    """Test AgentStation passes through non-text chunks"""
    session_id = "test-session"
    
    # Send audio chunk
    from core.chunk import AudioChunk
    audio_chunk = AudioChunk(
        type=ChunkType.AUDIO_RAW,
        data=b'\x00\x01' * 100,
        sample_rate=16000,
        channels=1,
        session_id=session_id
    )
    
    chunks = []
    async for chunk in agent_station.process_chunk(audio_chunk):
        chunks.append(chunk)
    
    # Should just passthrough
    assert len(chunks) == 1
    assert chunks[0].type == ChunkType.AUDIO_RAW

