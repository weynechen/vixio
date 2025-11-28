"""
Unit tests for SentenceSplitterStation
"""

import pytest
from core.chunk import Chunk, ChunkType, TextChunk, TextDeltaChunk, EventChunk, ControlChunk
from stations.sentence_splitter import SentenceSplitterStation, SentenceSplitter


class TestSentenceSplitter:
    """Test the SentenceSplitter utility class"""
    
    def test_split_single_sentence(self):
        """Test splitting a single complete sentence"""
        splitter = SentenceSplitter(min_sentence_length=5)
        
        # Add chunks that form a sentence
        result1 = splitter.add_chunk("Hello")
        assert len(result1) == 0  # No complete sentence yet
        
        result2 = splitter.add_chunk(" world")
        assert len(result2) == 0  # Still no complete sentence
        
        result3 = splitter.add_chunk(".")
        assert len(result3) == 1
        assert result3[0] == "Hello world."
    
    def test_split_chinese_sentence(self):
        """Test splitting Chinese sentences"""
        splitter = SentenceSplitter(min_sentence_length=5)
        
        result = splitter.add_chunk("你好世界。这是测试。")
        assert len(result) == 2
        assert result[0] == "你好世界。"
        assert result[1] == "这是测试。"
    
    def test_split_multiple_punctuation(self):
        """Test different sentence endings"""
        splitter = SentenceSplitter(min_sentence_length=5)
        
        # Exclamation mark
        result1 = splitter.add_chunk("Great!")
        assert len(result1) == 1
        assert result1[0] == "Great!"
        
        # Question mark
        result2 = splitter.add_chunk("How are you?")
        assert len(result2) == 1
        assert result2[0] == "How are you?"
        
        # Semicolon
        result3 = splitter.add_chunk("First part; second part;")
        assert len(result3) == 2
    
    def test_min_sentence_length(self):
        """Test minimum sentence length filtering"""
        splitter = SentenceSplitter(min_sentence_length=10)
        
        # Too short - should not yield
        result = splitter.add_chunk("Hi.")
        assert len(result) == 0
        
        # Long enough - should yield
        splitter.reset()
        result = splitter.add_chunk("This is long enough.")
        assert len(result) == 1
    
    def test_flush(self):
        """Test flushing remaining buffer"""
        splitter = SentenceSplitter(min_sentence_length=5)
        
        splitter.add_chunk("Hello world")
        remaining = splitter.flush()
        assert remaining == "Hello world"
        
        # Buffer should be empty after flush
        assert splitter.buffer == ""
    
    def test_reset(self):
        """Test resetting buffer"""
        splitter = SentenceSplitter(min_sentence_length=5)
        
        splitter.add_chunk("Hello world")
        assert splitter.buffer == "Hello world"
        
        splitter.reset()
        assert splitter.buffer == ""


class TestSentenceSplitterStation:
    """Test the SentenceSplitterStation"""
    
    @pytest.fixture
    def splitter_station(self):
        """Create SentenceSplitterStation"""
        return SentenceSplitterStation(min_sentence_length=5)
    
    @pytest.mark.asyncio
    async def test_split_text_delta_to_sentences(self, splitter_station):
        """Test splitting TEXT_DELTA into TEXT sentences"""
        session_id = "test-session"
        
        # Send deltas that form sentences
        delta1 = TextDeltaChunk(
            type=ChunkType.TEXT_DELTA,
            delta="Hello",
            session_id=session_id
        )
        
        chunks1 = []
        async for chunk in splitter_station.process_chunk(delta1):
            chunks1.append(chunk)
        
        # No complete sentence yet - only passthrough delta
        assert len(chunks1) == 1
        assert chunks1[0].type == ChunkType.TEXT_DELTA
        
        # Send more deltas
        delta2 = TextDeltaChunk(
            type=ChunkType.TEXT_DELTA,
            delta=" world.",
            session_id=session_id
        )
        
        chunks2 = []
        async for chunk in splitter_station.process_chunk(delta2):
            chunks2.append(chunk)
        
        # Should yield: TEXT (complete sentence) + TEXT_DELTA (passthrough)
        assert len(chunks2) == 2
        assert chunks2[0].type == ChunkType.TEXT
        assert chunks2[0].content == "Hello world."
        assert chunks2[1].type == ChunkType.TEXT_DELTA
    
    @pytest.mark.asyncio
    async def test_flush_on_agent_stop(self, splitter_station):
        """Test flushing remaining text on EVENT_AGENT_STOP"""
        session_id = "test-session"
        
        # Buffer some text without sentence ending
        delta = TextDeltaChunk(
            type=ChunkType.TEXT_DELTA,
            delta="Incomplete sentence",
            session_id=session_id
        )
        async for _ in splitter_station.process_chunk(delta):
            pass
        
        # Send AGENT_STOP
        agent_stop = EventChunk(
            type=ChunkType.EVENT_AGENT_STOP,
            event_data={},
            source="Agent",
            session_id=session_id
        )
        
        chunks = []
        async for chunk in splitter_station.process_chunk(agent_stop):
            chunks.append(chunk)
        
        # Should yield: TEXT (flushed) + AGENT_STOP (passthrough)
        assert len(chunks) == 2
        assert chunks[0].type == ChunkType.TEXT
        assert chunks[0].content == "Incomplete sentence"
        assert chunks[1].type == ChunkType.EVENT_AGENT_STOP
    
    @pytest.mark.asyncio
    async def test_reset_on_interrupt(self, splitter_station):
        """Test resetting buffer on CONTROL_INTERRUPT"""
        session_id = "test-session"
        
        # Buffer some text
        delta = TextDeltaChunk(
            type=ChunkType.TEXT_DELTA,
            delta="Some text",
            session_id=session_id
        )
        async for _ in splitter_station.process_chunk(delta):
            pass
        
        # Send interrupt
        interrupt = ControlChunk(
            type=ChunkType.CONTROL_INTERRUPT,
            command="stop",
            session_id=session_id
        )
        
        chunks = []
        async for chunk in splitter_station.process_chunk(interrupt):
            chunks.append(chunk)
        
        # Should passthrough interrupt
        assert len(chunks) == 1
        assert chunks[0].type == ChunkType.CONTROL_INTERRUPT
        
        # Buffer should be cleared
        assert splitter_station._splitter.buffer == ""
    
    @pytest.mark.asyncio
    async def test_passthrough_non_text_delta(self, splitter_station):
        """Test passthrough of non-TEXT_DELTA chunks"""
        session_id = "test-session"
        
        # Send TEXT chunk (not TEXT_DELTA)
        text_chunk = TextChunk(
            type=ChunkType.TEXT,
            content="Complete text",
            session_id=session_id
        )
        
        chunks = []
        async for chunk in splitter_station.process_chunk(text_chunk):
            chunks.append(chunk)
        
        # Should just passthrough
        assert len(chunks) == 1
        assert chunks[0].type == ChunkType.TEXT
    
    @pytest.mark.asyncio
    async def test_chinese_and_english_mixed(self, splitter_station):
        """Test splitting mixed Chinese and English sentences"""
        session_id = "test-session"
        
        # Use longer sentences to meet min_sentence_length requirement (5 chars)
        # Send deltas gradually to simulate streaming
        deltas = ["你好世界！", "How are you? ", "很高兴见到你。"]
        all_chunks = []
        
        for delta_text in deltas:
            delta = TextDeltaChunk(
                type=ChunkType.TEXT_DELTA,
                delta=delta_text,
                session_id=session_id
            )
            
            async for chunk in splitter_station.process_chunk(delta):
                all_chunks.append(chunk)
        
        # Extract TEXT chunks (complete sentences)
        text_chunks = [c for c in all_chunks if c.type == ChunkType.TEXT]
        
        # Should yield 3 complete sentences
        assert len(text_chunks) == 3
        assert text_chunks[0].content == "你好世界！"
        assert text_chunks[1].content == "How are you?"
        assert text_chunks[2].content == "很高兴见到你。"

