"""
Chunk data structures - carriers for both data and signals in the pipeline

Design principle:
- Data chunks (AUDIO/TEXT/VIDEO): Core content to be transformed by stations
- Control signals (CONTROL_*): Global commands via ControlBus, all stations respond
- Event signals (EVENT_*): State notifications and completion signals
  - Broadcast events (VAD_*, BOT_*): Transparent passthrough
  - Completion events (STREAM_COMPLETE): Consumed by downstream, triggers on_completion
  - Client events (TTS_*, STATE_*): Sent to client via OutputStation
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import time


class ChunkType(str, Enum):
    """
    Chunk types - divided into Data and Signals
    
    Design:
    - Data types: Core content transformed by stations
    - Control signals: Global commands via ControlBus
    - Event signals: State notifications and completion signals
    
    Event signal behavior:
    - Broadcast events (VAD_*, BOT_*): Passthrough to all downstream
    - Completion events (STREAM_COMPLETE): Consumed by stations with AWAITS_COMPLETION=True
    - Client events (TTS_*, STATE_*): Sent to client via OutputStation
    """
    
    # ============ Data Chunks (Core content - to be processed/transformed) ============
    # Audio data
    AUDIO_RAW = "audio.raw"           # PCM audio (16-bit signed, little-endian)
    
    # Text data
    TEXT = "text"                      # Complete text (e.g. ASR result, Agent input)
    TEXT_DELTA = "text.delta"         # Streaming text fragment (e.g. Agent output)
    
    # Vision data
    VIDEO_FRAME = "video.frame"       # Video frame
    IMAGE = "image"                    # Single image
    
    # ============ Control Signals (Global - via ControlBus) ============
    # These affect all stations and go through ControlBus
    CONTROL_HANDSHAKE = "control.handshake"      # Handshake with client
    CONTROL_STATE_RESET = "control.state_reset"  # Interrupt bot, reset all stations
    CONTROL_TURN_SWITCH = "control.turn_switch"  # Abort current turn, start new turn
    CONTROL_TERMINATE = "control.terminate"      # Stop TTS and terminate session
    
    # ============ Broadcast Events (passthrough to all downstream) ============
    # VAD events - for TurnDetector state machine
    EVENT_VAD_START = "event.vad.start"    # Voice detected
    EVENT_VAD_END = "event.vad.end"        # Voice ended
    
    # Bot speaking state - for TurnDetector interrupt detection
    EVENT_BOT_STARTED_SPEAKING = "event.bot.speaking.start"
    EVENT_BOT_STOPPED_SPEAKING = "event.bot.speaking.stop"
    
    # ============ Completion Event (consumed by downstream, triggers on_completion) ============
    EVENT_STREAM_COMPLETE = "event.stream.complete"  # Upstream finished, trigger downstream flush
    
    # ============ Client Events (for OutputStation -> Client display) ============
    # State events - notify client of state changes
    EVENT_STATE_IDLE = "event.state.idle"
    EVENT_STATE_LISTENING = "event.state.listening"
    EVENT_STATE_PROCESSING = "event.state.processing"
    EVENT_STATE_SPEAKING = "event.state.speaking"
    
    # TTS events - for client audio sync and subtitle display
    EVENT_TTS_START = "event.tts.start"
    EVENT_TTS_SENTENCE_START = "event.tts.sentence.start"
    EVENT_TTS_SENTENCE_END = "event.tts.sentence.end"
    EVENT_TTS_STOP = "event.tts.stop"
    
    # Error events
    EVENT_ERROR = "event.error"
    EVENT_TIMEOUT = "event.timeout"
    
    # Metrics events
    EVENT_METRICS = "event.metrics"
    
    def is_high_priority(self) -> bool:
        """
        Check if this chunk type should be sent with high priority.
        
        High priority chunks are sent immediately via priority_queue,
        not blocked by audio data in send_queue.
        
        Returns:
            True if this is a high priority chunk type
        """
        return self in HIGH_PRIORITY_TYPES
    
    def is_completion_event(self) -> bool:
        """
        Check if this is a completion event (consumed by downstream).
        
        Completion events trigger on_completion() in stations with AWAITS_COMPLETION=True.
        """
        return self == ChunkType.EVENT_STREAM_COMPLETE


# High priority chunk types (for immediate sending, not blocked by audio queue)
# These are typically control commands and state changes that need immediate delivery
HIGH_PRIORITY_TYPES = {
    # Control signals - always high priority
    ChunkType.CONTROL_HANDSHAKE,
    ChunkType.CONTROL_STATE_RESET,
    ChunkType.CONTROL_TURN_SWITCH,
    
    # State events - client needs immediate feedback
    ChunkType.EVENT_STATE_LISTENING,
    ChunkType.EVENT_STATE_IDLE,
    ChunkType.EVENT_STATE_SPEAKING,
}


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
        data: str - Text content (unified with base Chunk.data)
    """
    type: ChunkType = ChunkType.TEXT
    # Note: data is inherited from Chunk base class (Any type)
    # For TextChunk, data should be str
    
    def __post_init__(self):
        """Ensure data is a string"""
        if self.data is None:
            self.data = ""
        elif not isinstance(self.data, str):
            self.data = str(self.data)
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        text = self.data if isinstance(self.data, str) else str(self.data) if self.data else ""
        content_preview = text[:50] + "..." if len(text) > 50 else text
        return f"TextChunk('{content_preview}', session={session_short})"


@dataclass
class TextDeltaChunk(Chunk):
    """
    Text delta chunk - streaming fragment from Agent
    
    Attributes:
        data: str - Text fragment (unified with base Chunk.data)
    """
    type: ChunkType = ChunkType.TEXT_DELTA
    # Note: data is inherited from Chunk base class (Any type)
    # For TextDeltaChunk, data should be str
    
    def __post_init__(self):
        """Ensure data is a string"""
        if self.data is None:
            self.data = ""
        elif not isinstance(self.data, str):
            self.data = str(self.data)
    
    def __str__(self) -> str:
        session_short = self.session_id[:8] if self.session_id else 'N/A'
        text = self.data if isinstance(self.data, str) else str(self.data) if self.data else ""
        delta_preview = text[:30] + "..." if len(text) > 30 else text
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
    - CONTROL_TURN_SWITCH: Abort current turn, immediately send to client
    
    Attributes:
        command: str - Command name (optional, can use type instead)
        params: Dict - Command parameters
        data: Optional - Additional control data
    """
    type: ChunkType = ChunkType.CONTROL_TURN_SWITCH
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
    Event chunk - state notifications and completion signals.
    
    Event types:
    - Broadcast events (VAD_*, BOT_*): Passthrough to all downstream
    - Completion events (STREAM_COMPLETE): Consumed by downstream, triggers on_completion
    - Client events (TTS_*, STATE_*): Sent to client via OutputStation
    
    Examples:
    - EVENT_VAD_START/END: VAD state changes (broadcast)
    - EVENT_STREAM_COMPLETE: Stream finished, trigger downstream flush (completion)
    - EVENT_TTS_START/STOP: TTS events for client (client event)
    - EVENT_STATE_*: State changes for client UI (client event)
    
    Attributes:
        event_data: Any - Event-specific data
        data: Optional - Additional event payload
    """
    type: ChunkType = ChunkType.EVENT_TTS_START
    event_data: Any = None
    
    def is_completion_event(self) -> bool:
        """Check if this is a completion event"""
        return self.type == ChunkType.EVENT_STREAM_COMPLETE
    
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
    return isinstance(chunk, EventChunk) or (chunk.type and chunk.type.value.startswith("event."))


def is_completion_event(chunk: Chunk) -> bool:
    """Check if chunk is a completion event (triggers on_completion in downstream)"""
    return chunk.type == ChunkType.EVENT_STREAM_COMPLETE
