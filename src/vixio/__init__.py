"""
Vixio - A streaming audio processing framework

Design philosophy:
- Pipeline: Assembly line connecting multiple stations
- Station: Workstation handling specific tasks (VAD/ASR/Agent/TTS)
- Chunk: Carrier on the pipeline (Data or Signal)
- Transport: Input/output interface, completely decoupled from transport details

Supported Providers:
- VAD: Silero VAD
- ASR: Sherpa-ONNX (local)
- TTS: Edge TTS
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
from vixio.core.pipeline import Pipeline
from vixio.core.transport import TransportBase
from vixio.core.session import SessionManager

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
    "Pipeline",
    "TransportBase",
    "SessionManager",
]

