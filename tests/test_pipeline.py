"""
Unit tests for Pipeline
"""

import pytest
from core.chunk import Chunk, ChunkType, TextChunk, AudioChunk, ControlChunk
from core.station import Station, PassthroughStation
from core.pipeline import Pipeline
from typing import AsyncIterator


class UppercaseStation(Station):
    """Test station that converts text to uppercase"""
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        if isinstance(chunk, TextChunk):
            yield TextChunk(
                content=chunk.content.upper(),
                session_id=chunk.session_id
            )
        else:
            yield chunk


class FilterTextStation(Station):
    """Test station that only passes text chunks"""
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        if isinstance(chunk, TextChunk):
            yield chunk
        elif chunk.is_signal():
            # Always passthrough signals
            yield chunk
        # Drop other data chunks


class TestPipeline:
    """Test Pipeline class"""
    
    @pytest.mark.asyncio
    async def test_empty_pipeline(self):
        """Test pipeline with no stations"""
        pipeline = Pipeline(stations=[])
        
        input_chunks = [TextChunk(content="test")]
        
        async def input_stream():
            for chunk in input_chunks:
                yield chunk
        
        output = [chunk async for chunk in pipeline.run(input_stream())]
        assert len(output) == 1
        assert output[0].content == "test"
    
    @pytest.mark.asyncio
    async def test_single_station(self):
        """Test pipeline with one station"""
        pipeline = Pipeline(stations=[UppercaseStation()])
        
        async def input_stream():
            yield TextChunk(content="hello")
            yield TextChunk(content="world")
        
        output = [chunk async for chunk in pipeline.run(input_stream())]
        assert len(output) == 2
        assert output[0].content == "HELLO"
        assert output[1].content == "WORLD"
    
    @pytest.mark.asyncio
    async def test_multiple_stations(self):
        """Test pipeline with multiple stations"""
        # PassthroughStation -> UppercaseStation
        pipeline = Pipeline(stations=[
            PassthroughStation(),
            UppercaseStation()
        ])
        
        async def input_stream():
            yield TextChunk(content="test")
        
        output = [chunk async for chunk in pipeline.run(input_stream())]
        assert len(output) == 1
        assert output[0].content == "TEST"
    
    @pytest.mark.asyncio
    async def test_signal_passthrough(self):
        """Test that signals passthrough all stations"""
        pipeline = Pipeline(stations=[
            FilterTextStation(),  # This station only yields text chunks
            UppercaseStation()
        ])
        
        async def input_stream():
            yield ControlChunk(type=ChunkType.CONTROL_START)
            yield TextChunk(content="hello")
            yield ControlChunk(type=ChunkType.CONTROL_STOP)
        
        output = [chunk async for chunk in pipeline.run(input_stream())]
        
        # Control chunks should passthrough even through FilterTextStation
        assert len(output) == 3
        assert output[0].type == ChunkType.CONTROL_START
        assert output[1].content == "HELLO"
        assert output[2].type == ChunkType.CONTROL_STOP
    
    @pytest.mark.asyncio
    async def test_add_station(self):
        """Test adding station to pipeline"""
        pipeline = Pipeline(stations=[PassthroughStation()])
        
        # Add uppercase station
        pipeline.add_station(UppercaseStation())
        
        assert len(pipeline.stations) == 2
        assert isinstance(pipeline.stations[1], UppercaseStation)
    
    @pytest.mark.asyncio
    async def test_remove_station(self):
        """Test removing station from pipeline"""
        upper_station = UppercaseStation(name="Uppercase")
        pipeline = Pipeline(stations=[
            PassthroughStation(name="Pass"),
            upper_station
        ])
        
        # Remove uppercase station
        result = pipeline.remove_station("Uppercase")
        
        assert result is True
        assert len(pipeline.stations) == 1
        assert pipeline.stations[0].name == "Pass"
    
    @pytest.mark.asyncio
    async def test_get_station(self):
        """Test getting station by name"""
        upper_station = UppercaseStation(name="Uppercase")
        pipeline = Pipeline(stations=[
            PassthroughStation(name="Pass"),
            upper_station
        ])
        
        found = pipeline.get_station("Uppercase")
        assert found is upper_station
        
        not_found = pipeline.get_station("NonExistent")
        assert not_found is None
    
    @pytest.mark.asyncio
    async def test_pipeline_name(self):
        """Test pipeline naming"""
        pipeline = Pipeline(
            stations=[PassthroughStation()],
            name="TestPipeline"
        )
        
        assert pipeline.name == "TestPipeline"
        assert "TestPipeline" in str(pipeline)
