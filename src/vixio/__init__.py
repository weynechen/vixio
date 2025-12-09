"""
Vixio - A streaming audio processing framework

Design philosophy:
- DAG: Directed Acyclic Graph connecting multiple stations with flexible routing
- Station: Workstation handling specific tasks (VAD/ASR/Agent/TTS)
- Chunk: Carrier on the DAG (Data or Signal)
- Transport: Input/output interface, completely decoupled from transport details

Supported Providers:
- VAD: Silero VAD
- ASR: Sherpa-ONNX (local)
- TTS: Edge TTS, Kokoro TTS
"""

__version__ = "0.1.0"

# Configure logger on import (auto-configuration in utils.logger_config)
# This import triggers auto-configuration with default settings
import vixio.utils.logger_config  # noqa: F401

# Core abstractions
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
)
from vixio.core.station import Station, PassthroughStation
from vixio.core.transport import TransportBase
from vixio.core.session import SessionManager
from vixio.core.dag import DAG, CompiledDAG

__all__ = [
    # Version
    "__version__",
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
    "TransportBase",
    "SessionManager",
    # DAG
    "DAG",
    "CompiledDAG",
]

