"""
Core abstractions for Vixio framework
"""

from core.chunk import (
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
)
from core.station import Station, PassthroughStation
from core.pipeline import Pipeline
from core.transport import TransportBase, TransportBufferMixin
from core.session import SessionManager

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
    # Core classes
    "Station",
    "PassthroughStation",
    "Pipeline",
    "TransportBase",
    "TransportBufferMixin",
    "SessionManager",
]

