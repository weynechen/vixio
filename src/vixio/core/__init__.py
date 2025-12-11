"""
Core abstractions for Vixio framework
"""

from vixio.core.chunk import (
    Chunk,
    ChunkType,
    AudioChunk,
    TextChunk,
    TextDeltaChunk,
    VideoChunk,
    ControlChunk,
    EventChunk,
    is_audio_chunk,
    is_text_chunk,
    is_video_chunk,
    is_control_chunk,
    is_event_chunk,
    is_completion_event,
    HIGH_PRIORITY_TYPES,
)
from vixio.core.station import Station, PassthroughStation, StationRole
from vixio.core.transport import TransportBase
from vixio.core.session import SessionManager
from vixio.core.session_context import SessionContext, SessionQueues, SessionTasks
from vixio.core.protocol import ProtocolBase
from vixio.core.output_controller import (
    FlowControllerBase,
    PlayoutTrackerBase,
    PlayoutResult,
    SimpleFlowController,
    SimplePlayoutTracker,
)
from vixio.core.tools import (
    ToolDefinition,
    ToolCallRequest,
    ToolCallResult,
    DeviceToolClientBase,
    ToolConverterBase,
    get_current_time,
    get_current_date,
    func_to_tool_definition,
    get_builtin_local_tools,
)
from vixio.core.dag import (
    DAG,
    DAGNode,
    CompiledDAG,
    NodeStats,
    DAGValidationError,
)
from vixio.core.dag_events import (
    DAGEvent,
    DAGEventType,
    DAGEventEmitter,
)
from vixio.core.dag_monitor import (
    DAGMonitor,
    DAGMonitorRegistry,
    get_monitor_registry,
    get_dag_monitor,
)

__all__ = [
    # Chunk types
    "Chunk",
    "ChunkType",
    "AudioChunk",
    "TextChunk",
    "TextDeltaChunk",
    "VideoChunk",
    "ControlChunk",
    "EventChunk",
    # Chunk type guards
    "is_audio_chunk",
    "is_text_chunk",
    "is_video_chunk",
    "is_control_chunk",
    "is_event_chunk",
    "is_completion_event",
    "HIGH_PRIORITY_TYPES",
    # Core classes
    "Station",
    "PassthroughStation",
    "StationRole",
    "TransportBase",
    "ProtocolBase",
    "SessionManager",
    "SessionContext",
    "SessionQueues",
    "SessionTasks",
    # DAG classes
    "DAG",
    "DAGNode",
    "CompiledDAG",
    "NodeStats",
    "DAGValidationError",
    # DAG events
    "DAGEvent",
    "DAGEventType",
    "DAGEventEmitter",
    # DAG monitoring
    "DAGMonitor",
    "DAGMonitorRegistry",
    "get_monitor_registry",
    "get_dag_monitor",
    # Output controllers
    "FlowControllerBase",
    "PlayoutTrackerBase",
    "PlayoutResult",
    "SimpleFlowController",
    "SimplePlayoutTracker",
    # Tools
    "ToolDefinition",
    "ToolCallRequest",
    "ToolCallResult",
    "DeviceToolClientBase",
    "ToolConverterBase",
    "get_current_time",
    "get_current_date",
    "func_to_tool_definition",
    "get_builtin_local_tools",
]

