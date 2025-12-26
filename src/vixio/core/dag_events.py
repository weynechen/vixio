"""
DAG Event System - real-time monitoring and visualization support

Provides event emission for:
- Chunk processing events
- Edge activation events
- Node status changes
- Error events
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from loguru import logger

from vixio.core.chunk import Chunk, ChunkType


class DAGEventType(str, Enum):
    """Types of DAG events for monitoring"""

    # Chunk lifecycle events
    CHUNK_RECEIVED = "chunk_received"
    CHUNK_PROCESSED = "chunk_processed"
    CHUNK_FORWARDED = "chunk_forwarded"

    # Node events
    NODE_STARTED = "node_started"
    NODE_STOPPED = "node_stopped"
    NODE_STATUS_CHANGED = "node_status_changed"
    NODE_ERROR = "node_error"

    # Edge events
    EDGE_ACTIVE = "edge_active"  # Data flowing through edge

    # DAG lifecycle events
    DAG_STARTED = "dag_started"
    DAG_STOPPED = "dag_stopped"


@dataclass
class DAGEvent:
    """Event data structure for DAG monitoring"""

    type: DAGEventType
    node_name: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "type": self.type.value,
            "node": self.node_name,
            "timestamp": self.timestamp,
            **self.data,
        }


# Type for event listeners
EventListener = Callable[[DAGEvent], Union[None, Any]]
AsyncEventListener = Callable[[DAGEvent], Any]


class DAGEventEmitter:
    """
    Event emitter for DAG monitoring.

    Supports both sync and async listeners.
    Events are emitted asynchronously to avoid blocking the main data flow.
    """

    def __init__(self, buffer_size: int = 1000):
        """
        Initialize event emitter.

        Args:
            buffer_size: Max events to buffer if listeners are slow
        """
        self._listeners: List[Union[EventListener, AsyncEventListener]] = []
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._running = False
        self._dispatch_task: Optional[asyncio.Task] = None
        self.logger = logger.bind(component="DAGEventEmitter")

    def subscribe(self, listener: Union[EventListener, AsyncEventListener]) -> None:
        """
        Subscribe to events.

        Args:
            listener: Callback function (sync or async)
        """
        self._listeners.append(listener)
        self.logger.debug(f"Listener subscribed, total: {len(self._listeners)}")

    def unsubscribe(self, listener: Union[EventListener, AsyncEventListener]) -> None:
        """
        Unsubscribe from events.

        Args:
            listener: Previously subscribed callback
        """
        if listener in self._listeners:
            self._listeners.remove(listener)
            self.logger.debug(f"Listener unsubscribed, total: {len(self._listeners)}")

    async def start(self) -> None:
        """Start the event dispatch loop"""
        if self._running:
            return

        self._running = True
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(), name="DAGEventEmitter-dispatch"
        )
        self.logger.debug("Event emitter started")

    async def stop(self) -> None:
        """Stop the event dispatch loop"""
        self._running = False

        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        self.logger.debug("Event emitter stopped")

    async def _dispatch_loop(self) -> None:
        """Background task to dispatch events to listeners"""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
                await self._dispatch_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in dispatch loop: {e}")

    async def _dispatch_event(self, event: DAGEvent) -> None:
        """Dispatch event to all listeners"""
        for listener in self._listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                self.logger.error(f"Error in event listener: {e}")

    async def emit(self, event: DAGEvent) -> None:
        """
        Emit an event (non-blocking).

        If the queue is full, the event is dropped with a warning.

        Args:
            event: Event to emit
        """
        if not self._listeners:
            return  # No listeners, skip

        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            self.logger.warning(f"Event queue full, dropping event: {event.type}")

    # Convenience methods for common events

    async def emit_chunk_received(self, node_name: str, chunk: Chunk) -> None:
        """Emit chunk received event"""
        await self.emit(
            DAGEvent(
                type=DAGEventType.CHUNK_RECEIVED,
                node_name=node_name,
                timestamp=time.time(),
                data=self._chunk_to_event_data(chunk),
            )
        )

    async def emit_chunk_processed(self, node_name: str, chunk: Chunk) -> None:
        """Emit chunk processed event"""
        await self.emit(
            DAGEvent(
                type=DAGEventType.CHUNK_PROCESSED,
                node_name=node_name,
                timestamp=time.time(),
                data=self._chunk_to_event_data(chunk),
            )
        )

    async def emit_edge_active(
        self, from_node: str, to_node: str, chunk: Chunk
    ) -> None:
        """Emit edge activation event (data flowing through edge)"""
        data = self._chunk_to_event_data(chunk)
        data["target_node"] = to_node

        await self.emit(
            DAGEvent(
                type=DAGEventType.EDGE_ACTIVE,
                node_name=from_node,
                timestamp=time.time(),
                data=data,
            )
        )

    async def emit_node_status_changed(
        self, node_name: str, status: str, error: Optional[str] = None
    ) -> None:
        """Emit node status change event"""
        data = {"status": status}
        if error:
            data["error"] = error

        await self.emit(
            DAGEvent(
                type=DAGEventType.NODE_STATUS_CHANGED,
                node_name=node_name,
                timestamp=time.time(),
                data=data,
            )
        )

    async def emit_node_error(self, node_name: str, error: str) -> None:
        """Emit node error event"""
        await self.emit(
            DAGEvent(
                type=DAGEventType.NODE_ERROR,
                node_name=node_name,
                timestamp=time.time(),
                data={"error": error},
            )
        )

    async def emit_dag_started(self, dag_name: str) -> None:
        """Emit DAG started event"""
        await self.emit(
            DAGEvent(
                type=DAGEventType.DAG_STARTED,
                node_name=dag_name,
                timestamp=time.time(),
                data={},
            )
        )

    async def emit_dag_stopped(self, dag_name: str) -> None:
        """Emit DAG stopped event"""
        await self.emit(
            DAGEvent(
                type=DAGEventType.DAG_STOPPED,
                node_name=dag_name,
                timestamp=time.time(),
                data={},
            )
        )

    def _chunk_to_event_data(self, chunk: Chunk) -> Dict[str, Any]:
        """Convert chunk to event data dictionary"""
        data = {
            "chunk_type": chunk.type.value,
            "chunk_source": chunk.source,
            "turn_id": chunk.turn_id,
        }

        # Add text preview for text chunks
        if chunk.type in [ChunkType.TEXT, ChunkType.TEXT_DELTA]:
            text = str(chunk.data) if chunk.data else ""
            data["preview"] = text[:100]

        # Add size for audio chunks
        if chunk.type in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO) and chunk.data:
            if isinstance(chunk.data, bytes):
                data["size_bytes"] = len(chunk.data)

        return data
