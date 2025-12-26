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
    # Audio data (layered: streaming → complete)
    AUDIO = "audio"  # Complete audio segment (from VAD/TurnDetector)
    AUDIO_DELTA = "audio.delta"       # Streaming audio fragment (from Transport, ~0.06-0.12s)
    
    # Text data (layered: streaming → complete)
    TEXT = "text"                      # Complete text (e.g. ASR result, Agent input)
    TEXT_DELTA = "text.delta"         # Streaming text fragment (e.g. Agent output)
    
    # Vision data
    VIDEO_FRAME = "video.frame"       # Video frame
    IMAGE = "image"                    # Single image
    
    # Realtime events
    EVENT_REALTIME_CONNECTED = "event.realtime.connected"
    EVENT_REALTIME_SESSION_UPDATED = "event.realtime.session_updated"
    
    # Tool call related (Data Chunks)
    TOOL_CALL = "tool.call"       # Model tool call request
    TOOL_OUTPUT = "tool.output"   # Tool execution result

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
    
    # Bot processing state - sent when ASR starts processing, device can switch to speaker early
    EVENT_BOT_THINKING = "event.bot.thinking"
    
    # ============ Completion Event (consumed by downstream, triggers on_completion) ============
    EVENT_STREAM_COMPLETE = "event.stream.complete"  # Upstream finished, trigger downstream flush
    
    # ============ Client Events (for OutputStation -> Client display) ============
    # TTS events - for client audio sync and subtitle display
    # Per protocol: tts state: start/stop controls client speaking/listening state
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
# These are typically control commands that need immediate delivery
HIGH_PRIORITY_TYPES = {
    ChunkType.CONTROL_HANDSHAKE,
    ChunkType.CONTROL_STATE_RESET,
    ChunkType.CONTROL_TURN_SWITCH,
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
        role: Role of the content owner ("user" or "bot")
        metadata: Additional metadata
        timestamp: Creation timestamp
        session_id: Session identifier
        turn_id: Conversation turn ID (incremented on interrupt)
        sequence: Sequence number within the turn
    """
    type: ChunkType
    data: Any = None
    source: str = ""  # Source station name (asr, agent, user, etc.)
    role: str = "user"  # Content owner: "user" or "bot"
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
    Audio data chunk - PCM audio for processing
    
    Types:
    - AUDIO_DELTA: Streaming audio fragment from Transport (~0.06-0.12s, ~1920 bytes)
    - AUDIO: Complete audio segment from VAD/TurnDetector (merged)
    
    Important: AudioChunk ALWAYS contains PCM audio data (16-bit signed, little-endian).
    Transport layers are responsible for format conversion (e.g., Opus -> PCM).
    
    Attributes:
        type: ChunkType - Audio type (DELTA or COMPLETE)
        data: bytes - PCM audio bytes
        sample_rate: int - Sample rate in Hz (default: 16000)
        channels: int - Number of audio channels (default: 1)
    """
    type: ChunkType = ChunkType.AUDIO  # Default to COMPLETE for backward compatibility
    data: bytes = b""
    sample_rate: int = 16000
    channels: int = 1


@dataclass
class TextChunk(Chunk):
    """
    Text data chunk - output from user or ASR, input for Agent
    
    Attributes:
        data: str - Text content (unified with base Chunk.data)
    """
    type: ChunkType = ChunkType.TEXT
    data: str = ""
    
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
    data: str = ""
    
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


@dataclass
class ToolCallChunk(Chunk):
    """
    Tool call request from the model.
    """
    type: ChunkType = ChunkType.TOOL_CALL
    call_id: str = ""           # Unique call ID
    tool_name: str = ""         # Tool name
    arguments: Dict[str, Any] = field(default_factory=dict) # Tool arguments
    
    def __repr__(self) -> str:
        return f"ToolCallChunk({self.tool_name}, id={self.call_id})"


@dataclass
class ToolOutputChunk(Chunk):
    """
    Tool execution result, to be sent back to the model.
    """
    type: ChunkType = ChunkType.TOOL_OUTPUT
    call_id: str = ""           # Corresponding call ID
    output: str = ""            # Execution result (usually JSON string)
    is_error: bool = False      # Whether execution failed


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
