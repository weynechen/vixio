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
    HIGH_PRIORITY_TYPES,
)
from vixio.core.station import Station, PassthroughStation
from vixio.core.pipeline import Pipeline
from vixio.core.transport import TransportBase
from vixio.core.session import SessionManager
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
    "HIGH_PRIORITY_TYPES",
    # Core classes
    "Station",
    "PassthroughStation",
    "Pipeline",
    "TransportBase",
    "ProtocolBase",
    "SessionManager",
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

