"""
SentenceSplitterStation - Split streaming text into sentences

Input: TEXT_DELTA
Output: TEXT (complete sentences)
"""

import re
from typing import AsyncIterator, List
from core.station import Station
from core.chunk import Chunk, ChunkType, TextChunk, TextDeltaChunk
from utils import get_latency_monitor


class SentenceSplitter:
    """
    Split streaming text into sentences for real-time processing.
    
    Supports Chinese and English sentence boundaries.
    """
    
    # Sentence ending punctuation
    # Chinese: 。！？；
    # English: . ! ? ;
    # Also handle ellipsis: ... …
    SENTENCE_ENDINGS = r'[。！？；.!?;]|\.{3}|…'
    
    # Pattern to detect sentence boundary
    # Sentence ends with punctuation, optionally followed by quotes/brackets
    SENTENCE_PATTERN = re.compile(
        f'({SENTENCE_ENDINGS})(["\']?[）】」』]?)',
        re.UNICODE
    )
    
    def __init__(self, min_sentence_length: int = 5):
        """
        Initialize sentence splitter.
        
        Args:
            min_sentence_length: Minimum characters to consider a valid sentence
        """
        self.min_sentence_length = min_sentence_length
        self.buffer = ""
    
    def add_chunk(self, chunk: str) -> List[str]:
        """
        Add text chunk and extract complete sentences.
        
        Args:
            chunk: New text chunk (delta)
            
        Returns:
            List of complete sentences
        """
        self.buffer += chunk
        sentences = []
        
        # Find all sentence boundaries
        matches = list(self.SENTENCE_PATTERN.finditer(self.buffer))
        
        if not matches:
            # No complete sentence yet
            return []
        
        # Extract complete sentences
        last_end = 0
        for match in matches:
            sentence_end = match.end()
            sentence = self.buffer[last_end:sentence_end].strip()
            
            # Only yield if sentence is long enough
            if len(sentence) >= self.min_sentence_length:
                sentences.append(sentence)
                last_end = sentence_end
        
        # Keep remaining text in buffer
        if last_end > 0:
            self.buffer = self.buffer[last_end:].strip()
        
        return sentences
    
    def flush(self) -> str:
        """
        Get remaining text in buffer as final sentence.
        
        Returns:
            Remaining text (may not end with punctuation)
        """
        remaining = self.buffer.strip()
        self.buffer = ""
        return remaining if len(remaining) >= self.min_sentence_length else ""
    
    def reset(self) -> None:
        """Reset buffer for new conversation."""
        self.buffer = ""


class SentenceSplitterStation(Station):
    """
    Sentence splitter: Splits streaming text into complete sentences.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete sentences)
    
    This station is useful for feeding complete sentences to TTS
    instead of waiting for the entire response.
    """
    
    def __init__(
        self,
        min_sentence_length: int = 5,
        name: str = "SentenceSplitter"
    ):
        """
        Initialize sentence splitter station.
        
        Args:
            min_sentence_length: Minimum sentence length
            name: Station name
        """
        super().__init__(name=name)
        self.min_sentence_length = min_sentence_length
        self._splitter = SentenceSplitter(min_sentence_length)
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
        self._first_sentence_recorded = {}
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through sentence splitter.
        
        Logic:
        - On TEXT_DELTA: Add to buffer, yield complete sentences as TEXT
        - On EVENT_AGENT_STOP: Flush remaining buffer
        - On CONTROL_INTERRUPT: Reset buffer
        - Passthrough all other chunks
        """
        # Handle signals
        if chunk.is_signal():
            # Flush buffer when agent stops
            if chunk.type == ChunkType.EVENT_AGENT_STOP:
                remaining = self._splitter.flush()
                if remaining:
                    self.logger.info(f"Flushing final sentence: '{remaining[:50]}...'")
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        content=remaining,
                        source="agent",  # Final sentence from agent
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id  # Inherit turn_id from event
                    )
            
            # Reset on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._splitter.reset()
                # Clear first sentence tracking for this session
                self._first_sentence_recorded.clear()
                self.logger.debug("Sentence splitter reset")
            
            # Passthrough signals
            yield chunk
            return
        
        # Process TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            delta = chunk.delta if hasattr(chunk, 'delta') else str(chunk.data)
            
            if delta and chunk.source == "agent":
                # Add delta to splitter and get complete sentences
                sentences = self._splitter.add_chunk(delta)
                
                # Yield each complete sentence as TEXT chunk
                for i, sentence in enumerate(sentences):
                    self.logger.info(f"Complete sentence: '{sentence[:50]}...'")
                    
                    # Record T4: first_sentence_complete (only for first sentence per turn)
                    session_turn_key = f"{chunk.session_id}_{chunk.turn_id}"
                    if session_turn_key not in self._first_sentence_recorded:
                        self._latency_monitor.record(
                            chunk.session_id,
                            chunk.turn_id,
                            "first_sentence_complete"
                        )
                        self._first_sentence_recorded[session_turn_key] = True
                        self.logger.debug("Recorded first sentence complete")
                    
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        content=sentence,
                        source=chunk.source,  # Preserve source (e.g., "agent")
                        session_id=chunk.session_id,
                        turn_id=chunk.turn_id
                    )
            
            # Passthrough TEXT_DELTA
            yield chunk
        else:
            # Passthrough all other chunks
            yield chunk

