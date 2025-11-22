"""
Transport base - interface between external world and pipeline

Responsibilities:
1. Accept connections from clients
2. Protocol conversion (specific to each transport implementation)
3. Input buffering: Cache audio before VAD detection
4. Output buffering: Cache and control audio playback
5. Connection lifecycle management
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable, Optional
from core.chunk import Chunk, ChunkType
import asyncio
from collections import deque
import logging

logger = logging.getLogger(__name__)


class TransportBase(ABC):
    """
    Transport base - interface between external world and pipeline.
    
    Responsibilities:
    1. Accept connections from clients
    2. Protocol conversion (specific to each transport implementation)
    3. Input buffering: Cache audio before VAD detection
    4. Output buffering: Cache and control audio playback
    5. Connection lifecycle management
    
    Design principle:
    - Each Transport implementation includes its own protocol logic
    - User only needs to choose a Transport (e.g., XiaozhiTransport)
    - No need to separately configure protocol handlers
    """
    
    @abstractmethod
    async def start(self) -> None:
        """Start the transport server"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport server"""
        pass
    
    @abstractmethod
    async def input_stream(self, connection_id: str) -> AsyncIterator[Chunk]:
        """
        Get input chunk stream for a connection.
        
        This method should:
        1. Receive raw protocol messages
        2. Convert to Chunks
        3. Buffer audio chunks until VAD detection
        4. Yield chunks to pipeline
        
        Args:
            connection_id: Unique connection identifier
            
        Yields:
            Chunks for pipeline processing
        """
        pass
    
    @abstractmethod
    async def output_chunk(self, connection_id: str, chunk: Chunk) -> None:
        """
        Send a chunk to the client.
        
        This method should:
        1. Buffer audio chunks for smooth playback
        2. Convert chunks to protocol messages
        3. Send to client
        
        Args:
            connection_id: Target connection
            chunk: Chunk from pipeline
        """
        pass
    
    @abstractmethod
    async def on_new_connection(
        self,
        handler: Callable[[str], Awaitable[None]]
    ) -> None:
        """
        Register callback for new connections.
        
        Args:
            handler: Async function called with connection_id when client connects
        """
        pass


class TransportBufferMixin:
    """
    Mixin providing input/output buffering capabilities.
    
    This can be used by Transport implementations to handle buffering.
    """
    
    def __init__(self):
        """Initialize buffer structures"""
        # Input buffer: Store audio before VAD detection
        self._input_buffers = {}  # connection_id -> deque[AudioChunk]
        self._input_buffer_enabled = {}  # connection_id -> bool
        
        # Output buffer: Store audio for playback control
        self._output_buffers = {}  # connection_id -> deque[AudioChunk]
        self._output_playing = {}  # connection_id -> bool
        
        self.logger = logging.getLogger(f"{self.__class__.__name__}.BufferMixin")
    
    def _init_buffers(self, connection_id: str):
        """
        Initialize buffers for a new connection.
        
        Args:
            connection_id: Connection to initialize buffers for
        """
        self._input_buffers[connection_id] = deque(maxlen=1000)  # ~20s at 20ms chunks
        self._input_buffer_enabled[connection_id] = True  # Buffer by default
        self._output_buffers[connection_id] = deque()
        self._output_playing[connection_id] = False
        self.logger.debug(f"Initialized buffers for connection {connection_id[:8]}")
    
    def _cleanup_buffers(self, connection_id: str):
        """
        Cleanup buffers for disconnected connection.
        
        Args:
            connection_id: Connection to cleanup
        """
        self._input_buffers.pop(connection_id, None)
        self._input_buffer_enabled.pop(connection_id, None)
        self._output_buffers.pop(connection_id, None)
        self._output_playing.pop(connection_id, None)
        self.logger.debug(f"Cleaned up buffers for connection {connection_id[:8]}")
    
    def _should_buffer_input(self, connection_id: str) -> bool:
        """
        Check if input should be buffered (before VAD detection).
        
        Args:
            connection_id: Connection to check
            
        Returns:
            True if input should be buffered
        """
        return self._input_buffer_enabled.get(connection_id, True)
    
    def _enable_input_passthrough(self, connection_id: str):
        """
        Enable input passthrough (after VAD detection).
        
        This should be called when VAD detects voice.
        Also flushes buffered chunks.
        
        Args:
            connection_id: Connection to enable passthrough for
        """
        self._input_buffer_enabled[connection_id] = False
        self.logger.debug(f"Enabled input passthrough for connection {connection_id[:8]}")
    
    def _disable_input_passthrough(self, connection_id: str):
        """
        Disable input passthrough (back to buffering).
        
        This should be called when VAD stops detecting voice.
        
        Args:
            connection_id: Connection to disable passthrough for
        """
        self._input_buffer_enabled[connection_id] = True
        # Clear old buffer
        if connection_id in self._input_buffers:
            self._input_buffers[connection_id].clear()
        self.logger.debug(f"Disabled input passthrough for connection {connection_id[:8]}")
    
    def _add_to_input_buffer(self, connection_id: str, chunk: Chunk):
        """
        Add chunk to input buffer.
        
        Args:
            connection_id: Connection to add chunk for
            chunk: Chunk to buffer
        """
        if connection_id in self._input_buffers:
            self._input_buffers[connection_id].append(chunk)
            self.logger.debug(f"Buffered input chunk for {connection_id[:8]}, buffer size: {len(self._input_buffers[connection_id])}")
    
    def _get_buffered_input(self, connection_id: str) -> list:
        """
        Get all buffered input chunks.
        
        Args:
            connection_id: Connection to get buffered chunks for
            
        Returns:
            List of buffered chunks
        """
        if connection_id in self._input_buffers:
            buffered = list(self._input_buffers[connection_id])
            self._input_buffers[connection_id].clear()
            self.logger.debug(f"Flushed {len(buffered)} buffered input chunks for {connection_id[:8]}")
            return buffered
        return []
    
    def _add_to_output_buffer(self, connection_id: str, chunk: Chunk):
        """
        Add chunk to output buffer.
        
        Args:
            connection_id: Connection to add chunk for
            chunk: Chunk to buffer
        """
        if connection_id in self._output_buffers:
            self._output_buffers[connection_id].append(chunk)
            self.logger.debug(f"Buffered output chunk for {connection_id[:8]}, buffer size: {len(self._output_buffers[connection_id])}")
    
    async def _flush_output_buffer(self, connection_id: str, send_func: Callable[[bytes], Awaitable[None]]):
        """
        Flush output buffer to client.
        
        Args:
            connection_id: Connection to flush buffer for
            send_func: Async function to send raw data to client
        """
        if connection_id not in self._output_buffers:
            return
        
        self._output_playing[connection_id] = True
        self.logger.debug(f"Starting output buffer flush for {connection_id[:8]}")
        
        try:
            chunk_count = 0
            while self._output_buffers[connection_id]:
                chunk = self._output_buffers[connection_id].popleft()
                if chunk.data:
                    await send_func(chunk.data)
                    chunk_count += 1
                    # Add small delay for playback pacing
                    await asyncio.sleep(0.02)  # 20ms per chunk
            self.logger.debug(f"Flushed {chunk_count} output chunks for {connection_id[:8]}")
        except Exception as e:
            self.logger.error(f"Error flushing output buffer for {connection_id[:8]}: {e}")
        finally:
            self._output_playing[connection_id] = False
