"""
Chunk data structures - carriers for both data and signals in the pipeline

Design principle:
- Data chunks (AUDIO/TEXT/VIDEO): Core content to be transformed by stations
- Signal chunks (CONTROL/EVENT): Messages to passthrough + trigger station state changes
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import time


class ChunkType(str, Enum):
    """Chunk types - divided into Data (core content) and Signals (messages)"""
    
    # ============ Data Chunks (Core content - to be processed/transformed) ============
    # Audio data - raw material for ASR
    AUDIO_RAW = "audio.raw"           # PCM audio (16-bit signed, little-endian)
    
    # Text data - raw material for Agent, output from ASR
    TEXT = "text"                      # Complete text (ASR result, Agent input)
    TEXT_DELTA = "text.delta"         # Streaming text fragment (Agent output)
    
    # Vision data - raw material for vision models
    VIDEO_FRAME = "vision.frame"       # Vision frame
    VIDEO_IMAGE = "vision.image"       # Static image
    
    # ============ Signal Chunks (Messages - passthrough + trigger state change) ============
    
    # --- Control Signals (from client, change pipeline behavior) ---
    CONTROL_START = "control.start"         # Start session
    CONTROL_STOP = "control.stop"           # Stop session
    CONTROL_HELLO = "control.hello"         # Handshake
    CONTROL_INTERRUPT = "control.interrupt" # Interrupt bot (stop TTS, start listening)
    CONTROL_PAUSE = "control.pause"         # Pause processing
    CONTROL_RESUME = "control.resume"       # Resume processing
    CONTROL_CONFIG = "control.config"       # Update configuration
    
    # --- Event Signals (internal state notifications) ---
    # VAD events (from VAD station)
    EVENT_VAD_START = "event.vad.start"    # Voice detected
    EVENT_VAD_END = "event.vad.end"        # Voice ended
    
    # Turn events (from TurnDetector station)
    EVENT_USER_STARTED_SPEAKING = "event.user.speaking.start"
    EVENT_USER_STOPPED_SPEAKING = "event.user.speaking.stop"
    EVENT_TURN_END = "event.turn.end"      # User turn complete, ready for ASR
    
    # Text events (from ASR/input sources)
    EVENT_TEXT_COMPLETE = "event.text.complete"  # Text input complete, ready for aggregation
    
    # Bot events (from TTS station)
    EVENT_BOT_STARTED_SPEAKING = "event.bot.speaking.start"
    EVENT_BOT_STOPPED_SPEAKING = "event.bot.speaking.stop"
    
    # State events (for client UI sync)
    EVENT_STATE_IDLE = "event.state.idle"
    EVENT_STATE_LISTENING = "event.state.listening"
    EVENT_STATE_PROCESSING = "event.state.processing"
    EVENT_STATE_SPEAKING = "event.state.speaking"
    
    # Agent events (from Agent station)
    EVENT_AGENT_START = "event.agent.start"
    EVENT_AGENT_STOP = "event.agent.stop"
    
    # TTS events (for client sync)
    EVENT_TTS_START = "event.tts.start"
    EVENT_TTS_SENTENCE_START = "event.tts.sentence.start"
    EVENT_TTS_SENTENCE_END = "event.tts.sentence.end"
    EVENT_TTS_STOP = "event.tts.stop"
    
    # Error events
    EVENT_ERROR = "event.error"
    EVENT_TIMEOUT = "event.timeout"
    
    # Metrics events
    EVENT_METRICS = "event.metrics"


@dataclass
class Chunk:
    """
    Base chunk - carrier for both data and signals in the pipeline.
    
    Design principle:
    - Data chunks (AUDIO/TEXT/VIDEO): Core content to be transformed by stations
    - Signal chunks (CONTROL/EVENT): Messages to passthrough + trigger station state changes
    
    Attributes:
        type: Chunk type
        data: Data payload
        source: Source station name (e.g., "asr", "agent", "user")
        metadata: Additional metadata
        timestamp: Creation timestamp
        session_id: Session identifier
        turn_id: Conversation turn ID (incremented on interrupt)
        sequence: Sequence number within the turn
    """
    type: ChunkType
    data: Any = None
    source: str = ""  # Source station name (asr, agent, user, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    session_id: Optional[str] = None
    turn_id: int = 0  # Conversation turn ID, incremented on interrupt
    sequence: int = 0  # Sequence number within the turn
    
    def is_data(self) -> bool:
        """Check if this is a data chunk (core content)"""
        return self.type.value.startswith(("audio.", "text", "vision."))
    
    def is_signal(self) -> bool:
        """Check if this is a signal chunk (message)"""
        return self.type.value.startswith(("control.", "event."))
    
    def __str__(self) -> str:
        # Show first 8 chars of session ID if it's long enough
        if self.session_id:
            session_short = self.session_id[:8] if len(self.session_id) > 8 else self.session_id
        else:
            session_short = 'N/A'
        
        if self.data and isinstance(self.data, bytes):
            data_info = f", {len(self.data)} bytes"
        elif self.data and isinstance(self.data, str):
            data_info = f", '{self.data[:30]}...'" if len(self.data) > 30 else f", '{self.data}'"
        else:
            data_info = ""
        return f"Chunk({self.type.value}, session={session_short}{data_info})"
    
    def __repr__(self) -> str:
        return self.__str__()


# ============ Specialized Data Chunks ============

@dataclass
class AudioChunk(Chunk):
    """
    Audio data chunk - raw material for ASR processing
    
    Important: AudioChunk ALWAYS contains PCM audio data.
    Transport layers are responsible for format conversion (e.g., Opus -> PCM).
    
    Attributes:
        data: bytes - PCM audio bytes (16-bit signed integer, little-endian)
        sample_rate: int - Sample rate in Hz (default: 16000)
        channels: int - Number of audio channels (default: 1)
    """
    type: ChunkType = ChunkType.AUDIO_RAW
    sample_rate: int = 16000
    channels: int = 1
    
    def duration_ms(self) -> float:
        """Calculate audio duration in milliseconds"""
        if not self.data or not isinstance(self.data, bytes):
            return 0.0
        # Assuming 16-bit PCM
        bytes_per_sample = 2
        num_samples = len(self.data) / (bytes_per_sample * self.channels)
        return (num_samples / self.sample_rate) * 1000


@dataclass
class TextChunk(Chunk):
    """
    Text data chunk - output from user or ASR, input for Agent
    
    Attributes:
        content: str - The text content
        data: Optional - Can store additional info (language, confidence, etc.)
    """
    type: ChunkType = ChunkType.TEXT
    content: str = ""
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"TextChunk('{content_preview}', session={session_short})"


@dataclass
class TextDeltaChunk(Chunk):
    """
    Text delta chunk - streaming fragment from Agent
    
    Attributes:
        delta: str - Text fragment
        data: Optional - Can store cumulative text
    """
    type: ChunkType = ChunkType.TEXT_DELTA
    delta: str = ""
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        delta_preview = self.delta[:30] + "..." if len(self.delta) > 30 else self.delta
        return f"TextDeltaChunk('{delta_preview}', session={session_short})"


@dataclass
class VideoChunk(Chunk):
    """
    Video data chunk - video frame or image for vision processing
    
    Attributes:
        data: bytes - Image/frame data
        width: int - Frame width
        height: int - Frame height
        format: str - Image format (JPEG, PNG, RAW, etc.)
    """
    type: ChunkType = ChunkType.VIDEO_FRAME
    width: int = 0
    height: int = 0
    format: str = "JPEG"  # JPEG, PNG, RAW, etc.
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        size_info = f"{self.width}x{self.height}" if self.width and self.height else "unknown"
        data_size = f", {len(self.data)} bytes" if self.data and isinstance(self.data, bytes) else ""
        return f"VideoChunk({size_info}, {self.format}{data_size}, session={session_short})"


# ============ Specialized Signal Chunks ============

@dataclass
class ControlChunk(Chunk):
    """
    Control chunk - control signals from client.
    
    Examples:
    - CONTROL_START: Start session
    - CONTROL_STOP: Stop session
    - CONTROL_INTERRUPT: Interrupt bot (stop TTS, start listening)
    - CONTROL_PAUSE/RESUME: Pause/resume processing
    - CONTROL_CONFIG: Update configuration
    
    Attributes:
        command: str - Command name (optional, can use type instead)
        params: Dict - Command parameters
        data: Optional - Additional control data
    """
    type: ChunkType = ChunkType.CONTROL_START
    command: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        cmd = self.command or self.type.value.split('.')[-1]
        params_str = f", params={self.params}" if self.params else ""
        return f"ControlChunk({cmd}{params_str}, session={session_short})"


@dataclass
class EventChunk(Chunk):
    """
    Event chunk - internal state notifications.
    
    Examples:
    - EVENT_VAD_START/END: Voice activity events
    - EVENT_USER_STARTED_SPEAKING/STOPPED_SPEAKING: User turn events
    - EVENT_TURN_END: Turn complete
    - EVENT_TTS_START/STOP: TTS generation events
    - EVENT_STATE_*: State change events
    - EVENT_ERROR: Error occurred
    
    Attributes:
        event_data: Any - Event-specific data
        data: Optional - Additional event payload
    
    Note:
        Use the inherited 'source' field to specify which station generated this event
    """
    type: ChunkType = ChunkType.EVENT_VAD_START
    event_data: Any = None
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        event_name = self.type.value.split('.')[-1]
        source_info = f" from {self.source}" if self.source else ""
        data_str = f", data={self.event_data}" if self.event_data else ""
        return f"EventChunk({event_name}{source_info}{data_str}, session={session_short})"


# ============ Type Guards ============

def is_audio_chunk(chunk: Chunk) -> bool:
    """Check if chunk is audio data"""
    return isinstance(chunk, AudioChunk) or chunk.type.value.startswith("audio.")


def is_text_chunk(chunk: Chunk) -> bool:
    """Check if chunk is text data"""
    return isinstance(chunk, (TextChunk, TextDeltaChunk)) or chunk.type.value.startswith("text")


def is_video_chunk(chunk: Chunk) -> bool:
    """Check if chunk is video data"""
    return isinstance(chunk, VideoChunk) or chunk.type.value.startswith("vision.")


def is_control_chunk(chunk: Chunk) -> bool:
    """Check if chunk is control signal"""
    return isinstance(chunk, ControlChunk) or chunk.type.value.startswith("control.")


def is_event_chunk(chunk: Chunk) -> bool:
    """Check if chunk is event signal"""
    return isinstance(chunk, EventChunk) or chunk.type.value.startswith("event.")
