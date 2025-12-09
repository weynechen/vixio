"""
DAG Monitor - monitoring interface for DAG visualization

Provides:
- DAG structure export
- Runtime statistics collection
- Real-time event subscription
"""

import time
from typing import Any, Callable, Dict, Optional, Union
from loguru import logger

from vixio.core.dag import CompiledDAG
from vixio.core.dag_events import (
    DAGEvent,
    DAGEventEmitter,
    EventListener,
    AsyncEventListener,
)


class DAGMonitor:
    """
    Monitor interface for DAG visualization.

    Provides methods to:
    - Get DAG structure for initial rendering
    - Get runtime statistics (periodic snapshots)
    - Subscribe to real-time events
    """

    def __init__(self, dag: CompiledDAG, event_buffer_size: int = 1000):
        """
        Initialize DAG monitor.

        Args:
            dag: Compiled DAG to monitor
            event_buffer_size: Max events to buffer
        """
        self.dag = dag
        self.event_emitter = DAGEventEmitter(buffer_size=event_buffer_size)
        self.logger = logger.bind(component=f"DAGMonitor:{dag.name}")

    async def start(self) -> None:
        """Start the monitor (event dispatch loop)"""
        await self.event_emitter.start()
        self.logger.info(f"Monitor started for DAG: {self.dag.name}")

    async def stop(self) -> None:
        """Stop the monitor"""
        await self.event_emitter.stop()
        self.logger.info(f"Monitor stopped for DAG: {self.dag.name}")

    def get_structure(self) -> Dict[str, Any]:
        """
        Get DAG structure for initial rendering.

        Returns:
            Dictionary with nodes and edges for visualization
        """
        return self.dag.to_dict()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get runtime statistics for all nodes.

        Returns:
            Dictionary with per-node statistics
        """
        return self.dag.get_stats()

    def subscribe(
        self, listener: Union[EventListener, AsyncEventListener]
    ) -> None:
        """
        Subscribe to real-time events.

        Args:
            listener: Callback function (sync or async)
        """
        self.event_emitter.subscribe(listener)

    def unsubscribe(
        self, listener: Union[EventListener, AsyncEventListener]
    ) -> None:
        """
        Unsubscribe from events.

        Args:
            listener: Previously subscribed callback
        """
        self.event_emitter.unsubscribe(listener)

    def get_node_stats(self, node_name: str) -> Optional[Dict[str, Any]]:
        """
        Get statistics for a specific node.

        Args:
            node_name: Name of the node

        Returns:
            Node statistics or None if node doesn't exist
        """
        node = self.dag.nodes.get(node_name)
        if not node:
            return None

        stats = node.stats
        return {
            "name": node.name,
            "status": stats.status,
            "chunks_received": stats.chunks_received,
            "chunks_processed": stats.chunks_processed,
            "chunks_forwarded": stats.chunks_forwarded,
            "queue_size": stats.queue_size,
            "avg_latency_ms": stats.avg_latency_ms,
            "last_input_text": stats.last_input_text,
            "last_output_text": stats.last_output_text,
            "error_count": stats.error_count,
            "last_error": stats.last_error,
            "last_chunk_time": stats.last_chunk_time,
        }

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics for the entire DAG.

        Returns:
            Summary statistics
        """
        total_received = 0
        total_processed = 0
        total_errors = 0
        active_nodes = 0

        for node in self.dag.nodes.values():
            total_received += node.stats.chunks_received
            total_processed += node.stats.chunks_processed
            total_errors += node.stats.error_count
            if node.stats.status == "processing":
                active_nodes += 1

        return {
            "dag_name": self.dag.name,
            "total_nodes": len(self.dag.nodes),
            "active_nodes": active_nodes,
            "total_chunks_received": total_received,
            "total_chunks_processed": total_processed,
            "total_errors": total_errors,
            "timestamp": time.time(),
        }

    def reset_stats(self) -> None:
        """Reset all node statistics"""
        for node in self.dag.nodes.values():
            node.stats.chunks_received = 0
            node.stats.chunks_processed = 0
            node.stats.chunks_forwarded = 0
            node.stats.error_count = 0
            node.stats.last_error = None
            node.stats.last_input_text = ""
            node.stats.last_output_text = ""
            node.stats._latency_samples.clear()
            node.stats.avg_latency_ms = 0.0

        self.logger.info("Statistics reset")


class DAGMonitorRegistry:
    """
    Registry for managing multiple DAG monitors.

    Used when there are multiple sessions, each with its own DAG.
    """

    def __init__(self):
        """Initialize monitor registry"""
        self._monitors: Dict[str, DAGMonitor] = {}
        self.logger = logger.bind(component="DAGMonitorRegistry")

    def register(self, session_id: str, dag: CompiledDAG) -> DAGMonitor:
        """
        Register a DAG for monitoring.

        Args:
            session_id: Session identifier
            dag: Compiled DAG to monitor

        Returns:
            DAGMonitor instance
        """
        if session_id in self._monitors:
            self.logger.warning(f"Replacing existing monitor for session: {session_id}")

        monitor = DAGMonitor(dag)
        self._monitors[session_id] = monitor
        self.logger.debug(f"Registered monitor for session: {session_id}")
        return monitor

    def unregister(self, session_id: str) -> None:
        """
        Unregister a DAG monitor.

        Args:
            session_id: Session identifier
        """
        if session_id in self._monitors:
            del self._monitors[session_id]
            self.logger.debug(f"Unregistered monitor for session: {session_id}")

    def get(self, session_id: str) -> Optional[DAGMonitor]:
        """
        Get monitor for a session.

        Args:
            session_id: Session identifier

        Returns:
            DAGMonitor or None
        """
        return self._monitors.get(session_id)

    def list_sessions(self) -> list:
        """List all monitored session IDs"""
        return list(self._monitors.keys())

    def get_all_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Get summaries for all monitored DAGs"""
        return {
            session_id: monitor.get_summary()
            for session_id, monitor in self._monitors.items()
        }


# Global monitor registry (singleton)
_monitor_registry: Optional[DAGMonitorRegistry] = None


def get_monitor_registry() -> DAGMonitorRegistry:
    """Get the global monitor registry"""
    global _monitor_registry
    if _monitor_registry is None:
        _monitor_registry = DAGMonitorRegistry()
    return _monitor_registry


def get_dag_monitor(session_id: str) -> Optional[DAGMonitor]:
    """
    Convenience function to get a DAG monitor by session ID.

    Args:
        session_id: Session identifier

    Returns:
        DAGMonitor or None
    """
    return get_monitor_registry().get(session_id)
