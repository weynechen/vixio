"""
Test client-initiated interrupt functionality

Test that client abort messages correctly trigger interrupt mechanism.
"""

import pytest
import asyncio
from typing import AsyncIterator
from core.chunk import Chunk, ChunkType, TextChunk, ControlChunk
from core.control_bus import ControlBus
from core.session import SessionManager


class MockTransport:
    """Mock transport for testing."""
    
    def __init__(self):
        self.connections = {}
        self.output_chunks = []
    
    async def on_new_connection(self, handler):
        self.handler = handler
    
    async def start(self):
        pass
    
    async def stop(self):
        pass
    
    def input_stream(self, connection_id: str) -> AsyncIterator[Chunk]:
        """Return async generator of chunks."""
        return self._input_generator(connection_id)
    
    async def _input_generator(self, connection_id: str):
        """Generate test input chunks."""
        if connection_id in self.connections:
            for chunk in self.connections[connection_id]:
                yield chunk
    
    async def output_chunk(self, connection_id: str, chunk: Chunk):
        """Store output chunks."""
        self.output_chunks.append((connection_id, chunk))
    
    def set_input_chunks(self, connection_id: str, chunks: list):
        """Set input chunks for a connection."""
        self.connections[connection_id] = chunks


class MockPipeline:
    """Mock pipeline for testing."""
    
    def __init__(self):
        self.control_bus = None
        self.processed_chunks = []
        self.queues_cleared = False
        self.slow_tasks_cancelled = False
    
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """Process input stream."""
        async for chunk in input_stream:
            self.processed_chunks.append(chunk)
            
            # Echo chunk back
            yield TextChunk(
                type=ChunkType.TEXT,
                content=f"Processed: {chunk}",
                session_id=chunk.session_id
            )
    
    def clear_queues(self, from_stage: int = 1):
        """Mock queue clearing."""
        self.queues_cleared = True
    
    def cancel_slow_tasks(self):
        """Mock task cancellation."""
        self.slow_tasks_cancelled = True


@pytest.mark.asyncio
async def test_client_abort_triggers_interrupt():
    """Test that client abort message triggers interrupt mechanism."""
    
    # Setup
    transport = MockTransport()
    
    def pipeline_factory():
        return MockPipeline()
    
    session_manager = SessionManager(transport, pipeline_factory)
    
    # Simulate connection
    connection_id = "test-connection-123"
    
    # Create test input with abort
    input_chunks = [
        TextChunk(
            type=ChunkType.TEXT,
            content="Hello",
            session_id=connection_id
        ),
        ControlChunk(
            type=ChunkType.CONTROL_INTERRUPT,
            command="abort",
            params={"reason": "client_abort"},
            session_id=connection_id
        ),
        TextChunk(
            type=ChunkType.TEXT,
            content="This should be in new turn",
            session_id=connection_id
        ),
    ]
    
    transport.set_input_chunks(connection_id, input_chunks)
    
    # Start session manager (non-blocking)
    start_task = asyncio.create_task(session_manager.start())
    
    # Trigger connection handler
    await asyncio.sleep(0.1)
    await transport.handler(connection_id)
    
    # Wait for processing
    await asyncio.sleep(0.5)
    
    # Verify interrupt was handled
    assert connection_id in session_manager._control_buses
    control_bus = session_manager._control_buses[connection_id]
    
    # Turn ID should have incremented due to interrupt
    assert control_bus.get_current_turn_id() >= 1
    
    # Check that pipeline received chunks
    pipeline = session_manager._pipelines.get(connection_id)
    if pipeline:
        assert len(pipeline.processed_chunks) >= 1
    
    # Cleanup
    await session_manager.stop()
    start_task.cancel()
    try:
        await start_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_control_interrupt_chunk_sent_to_control_bus():
    """Test that CONTROL_INTERRUPT chunk is detected and sent to ControlBus."""
    
    # Create control bus
    control_bus = ControlBus()
    
    # Create input stream with CONTROL_INTERRUPT
    async def input_stream():
        yield TextChunk(
            type=ChunkType.TEXT,
            content="test",
            session_id="test-session"
        )
        yield ControlChunk(
            type=ChunkType.CONTROL_INTERRUPT,
            command="abort",
            params={"reason": "test_abort"},
            session_id="test-session"
        )
    
    # Create session manager with mock transport
    transport = MockTransport()
    session_manager = SessionManager(transport, lambda: MockPipeline())
    
    # Wrap input stream (this is what Session does internally)
    wrapped_chunks = []
    async for chunk in session_manager._wrap_input_stream(
        input_stream(),
        control_bus,
        "test-connection"
    ):
        wrapped_chunks.append(chunk)
    
    # Verify all chunks were yielded
    assert len(wrapped_chunks) == 2
    
    # Verify interrupt signal was sent to ControlBus
    # The interrupt queue should have a signal
    assert control_bus._interrupt_queue.qsize() > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])



