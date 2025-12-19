"""
JoinNode - merges results from multiple upstream branches

Use case: Combining results from parallel processing branches
- ASR result + Speaker ID (from voice print)
- Text analysis + Emotion detection
- Multiple sensor inputs

Design:
- Waits for results from all upstream branches (identified by unique sources)
- Merges by turn_id
- Outputs combined result with all data in metadata
"""

import asyncio
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Dict, Optional, Set, Any
from vixio.core.station import Station
from vixio.core.chunk import Chunk, ChunkType, TextChunk


class JoinNode(Station):
    """
    JoinNode: Merges results from multiple upstream branches by turn_id.
    
    Usage:
        # In DAG setup
        dag.add_node("join", JoinNode(upstream_count=2))
        dag.add_edge("asr", "join")
        dag.add_edge("voiceprint", "join")
        dag.add_edge("join", "agent")
    
    Behavior:
    - Collects chunks from different sources for the same turn_id
    - When all expected sources have contributed, merges and outputs
    - Timeout mechanism prevents indefinite waiting
    
    Output:
    - Primary chunk (TEXT preferred) with merged metadata
    - metadata["merged_sources"] contains all source data
    """
    
    # Accept all types - JoinNode merges by turn_id, not by type
    ALLOWED_INPUT_TYPES = []
    
    def __init__(
        self,
        upstream_count: int = 2,
        timeout_seconds: float = 5.0,
        expected_sources: Optional[Set[str]] = None,
        name: str = "JoinNode"
    ):
        """
        Initialize JoinNode.
        
        Args:
            upstream_count: Number of upstream branches to wait for
            timeout_seconds: Max time to wait for all branches (per turn)
            expected_sources: Optional set of expected source names
                             If provided, waits for these specific sources
                             If None, waits for upstream_count unique sources
            name: Station name
        """
        super().__init__(name=name)
        self.upstream_count = upstream_count
        self.timeout_seconds = timeout_seconds
        self.expected_sources = expected_sources
        
        # Pending chunks: {turn_id: {source: chunk}}
        self._pending: Dict[int, Dict[str, Chunk]] = {}
        # Timeout tasks: {turn_id: task}
        self._timeout_tasks: Dict[int, asyncio.Task] = {}
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Collect chunk and merge when all upstream branches have contributed.
        
        Args:
            chunk: Input chunk from any upstream branch
            
        Yields:
            Merged chunk when all branches have contributed
        """
        turn_id = chunk.turn_id
        source = chunk.source or "unknown"
        
        # Skip signals - pass through without merging
        if chunk.is_signal():
            yield chunk
            return
        
        # Initialize pending dict for this turn
        if turn_id not in self._pending:
            self._pending[turn_id] = {}
            # Start timeout task
            self._start_timeout(turn_id)
        
        # Store chunk by source
        self._pending[turn_id][source] = chunk
        self.logger.debug(
            f"Collected chunk from '{source}' for turn {turn_id}, "
            f"have {len(self._pending[turn_id])}/{self.upstream_count}"
        )
        
        # Check if ready to merge
        if self._is_ready(turn_id):
            # Cancel timeout task
            self._cancel_timeout(turn_id)
            
            # Merge and yield
            chunks = self._pending.pop(turn_id)
            merged = self._merge_chunks(chunks, turn_id)
            
            self.logger.info(
                f"Merged {len(chunks)} chunks for turn {turn_id}: "
                f"sources={list(chunks.keys())}"
            )
            yield merged
    
    def _is_ready(self, turn_id: int) -> bool:
        """Check if we have all expected chunks for this turn."""
        if turn_id not in self._pending:
            return False
        
        collected_sources = set(self._pending[turn_id].keys())
        
        if self.expected_sources:
            # Check if all expected sources have contributed
            return collected_sources >= self.expected_sources
        else:
            # Check if we have enough unique sources
            return len(collected_sources) >= self.upstream_count
    
    def _merge_chunks(self, chunks: Dict[str, Chunk], turn_id: int) -> Chunk:
        """
        Merge multiple chunks into one.
        
        Strategy:
        - Find primary chunk (TEXT preferred, then first chunk)
        - Store all data in metadata["merged_sources"]
        
        Args:
            chunks: Dictionary of {source: chunk}
            turn_id: Turn ID for the merged chunk
            
        Returns:
            Merged chunk
        """
        # Build merged sources metadata
        merged_sources: Dict[str, Any] = {}
        for source, chunk in chunks.items():
            merged_sources[source] = {
                "type": chunk.type.value,
                "data": chunk.data,
                "timestamp": chunk.timestamp,
            }
            # Include chunk metadata if present
            if chunk.metadata:
                merged_sources[source]["metadata"] = chunk.metadata
        
        # Find primary chunk (prefer TEXT type)
        primary_chunk = None
        for source, chunk in chunks.items():
            if chunk.type == ChunkType.TEXT:
                primary_chunk = chunk
                break
        
        # If no TEXT, use first chunk
        if primary_chunk is None:
            primary_chunk = list(chunks.values())[0]
        
        # Create merged chunk based on primary
        if primary_chunk.type == ChunkType.TEXT:
            merged = TextChunk(
                data=primary_chunk.data,
                source=self.name,
                session_id=primary_chunk.session_id,
                turn_id=turn_id,
            )
        else:
            merged = Chunk(
                type=primary_chunk.type,
                data=primary_chunk.data,
                source=self.name,
                session_id=primary_chunk.session_id,
                turn_id=turn_id,
            )
        
        # Add merged sources to metadata
        merged.metadata["merged_sources"] = merged_sources
        merged.metadata["merge_count"] = len(chunks)
        
        return merged
    
    def _start_timeout(self, turn_id: int) -> None:
        """Start timeout task for a turn."""
        async def timeout_handler():
            await asyncio.sleep(self.timeout_seconds)
            
            # Timeout reached - merge whatever we have
            if turn_id in self._pending:
                chunks = self._pending.pop(turn_id)
                if chunks:
                    self.logger.warning(
                        f"Timeout for turn {turn_id}, merging partial results: "
                        f"have {len(chunks)}/{self.upstream_count} sources"
                    )
                    # Note: Can't yield from here, would need a queue
                    # For now, just log and discard
                    # In production, consider using a separate output queue
        
        task = asyncio.create_task(timeout_handler())
        self._timeout_tasks[turn_id] = task
    
    def _cancel_timeout(self, turn_id: int) -> None:
        """Cancel timeout task for a turn."""
        if turn_id in self._timeout_tasks:
            task = self._timeout_tasks.pop(turn_id)
            if not task.done():
                task.cancel()
    
    async def reset_state(self) -> None:
        """Reset state for new turn."""
        await super().reset_state()
        
        # Cancel all timeout tasks
        for task in self._timeout_tasks.values():
            if not task.done():
                task.cancel()
        self._timeout_tasks.clear()
        
        # Clear pending chunks
        self._pending.clear()
        
        self.logger.debug("JoinNode state reset")
    
    async def cleanup(self) -> None:
        """Cleanup resources."""
        await self.reset_state()
        self.logger.debug("JoinNode cleaned up")
