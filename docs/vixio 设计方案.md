## 文档说明
**本文档版本：v3.0** - 已与实际代码实现完全同步（2025-11-25）

本设计方案文档描述了 Vixio 语音对话框架的完整架构设计，所有示例代码均基于实际实现。如果您发现文档与代码不一致，请优先参考实际代码实现。

## 设计理念
参考 Pipecat 的优秀设计，采用工业流水线的比喻：
- **Pipeline**：流水线，串联多个工站形成完整的处理流程（**异步并行实现**）
- **Station**：工站，负责特定的处理任务（VAD/ASR/Agent/TTS）
- **Chunk**：流水线上传递的载体，分为两类：
  - **Data Chunk（产品）**：Audio/Vision/Text，经过工站加工转换
  - **Signal Chunk（消息）**：Control/Event，立即透传并触发工站状态变化
- **Transport**：流水线的输入输出接口，完全解耦传输细节
- **ControlBus**：中心化控制总线，管理中断和 turn 转换
- **Turn-aware**：基于 turn_id 的流程控制，自动丢弃旧数据

## 核心设计原则
1. **数据流（产品）**：Audio/Vision/Text 是待加工的产品，工站处理后 yield 新产品
2. **信号流（消息）**：Control/Event 是控制消息，工站收到后立即透传，同时触发自身状态变化
3. **流式处理**：所有工站都是 async generator，实时处理不阻塞
4. **职责单一**：每个工站只负责一件事，通过组合实现复杂功能
5. **Turn-aware**：基于 turn_id 的流程控制，中断时自动丢弃旧数据
6. **中心化控制**：ControlBus 管理中断和 turn 转换，解耦组件间的控制流

## 音频格式处理原则
**重要约定**：Pipeline 内所有 AudioChunk 都是 **PCM 格式**（16-bit signed, little-endian, 16kHz, mono）

- **Transport 层职责**：负责音频格式转换（Opus ↔ PCM）
- **Station 层职责**：只处理 PCM 格式，无需关心编解码
- **优势**：
  - Station 实现简化，只需处理统一格式
  - 格式转换集中在 Transport 层，易于优化
  - 支持不同客户端使用不同编码格式

```
客户端 (Opus) ──> Transport (Opus→PCM) ──> Pipeline (PCM only) ──> Transport (PCM→Opus) ──> 客户端 (Opus)
              格式转换                     统一处理                  格式转换
```

## 核心架构
### 1. Chunk 数据结构
Chunk 分为两大类：**Data Chunk（产品）** 和 **Signal Chunk（消息）**

```python
# vixio/core/chunk.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import time

class ChunkType(str, Enum):
    """Chunk types - divided into Data (products) and Signals (messages)"""
    
    # ============ Data Chunks (Products - to be processed/transformed) ============
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
    - Data chunks (AUDIO/TEXT/VIDEO): Products to be transformed by stations
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
        """Check if this is a data chunk (product)"""
        return self.type.value.startswith(("audio.", "text.", "video."))
    
    def is_signal(self) -> bool:
        """Check if this is a signal chunk (message)"""
        return self.type.value.startswith(("control.", "event."))
    
    def __str__(self) -> str:
        return f"Chunk({self.type.value}, session={self.session_id[:8] if self.session_id else 'N/A'})"

# Specialized data chunks
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
    """Text data chunk - output from user or ASR, input for Agent"""
    type: ChunkType = ChunkType.TEXT
    content: str = ""
    # data field can store additional info (language, confidence, etc.)

@dataclass
class TextDeltaChunk(Chunk):
    """Text delta chunk - streaming fragment from Agent"""
    type: ChunkType = ChunkType.TEXT_DELTA
    delta: str = ""  # Text fragment
    # data field can store cumulative text

@dataclass
class VideoChunk(Chunk):
    """Video data chunk - video frame or image for vision processing"""
    type: ChunkType = ChunkType.VIDEO_FRAME
    width: int = 0
    height: int = 0
    format: str = "JPEG"  # JPEG, PNG, RAW, etc.
    # data: bytes (image/frame data)

# Specialized signal chunks
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
    """
    type: ChunkType = ChunkType.CONTROL_START
    command: str = ""  # Command name (optional, can use type instead)
    params: Dict[str, Any] = field(default_factory=dict)  # Command parameters
    # data field can store additional control data

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
    
    Note:
        Use the inherited 'source' field to specify which station generated this event
    """
    type: ChunkType = ChunkType.EVENT_VAD_START
    event_data: Any = None  # Event-specific data
    # data field can store additional event payload

# ============ Usage Examples ============

# Example 1: Creating data chunks
audio = AudioChunk(
    type=ChunkType.AUDIO_RAW,
    data=audio_bytes,
    sample_rate=16000,
    channels=1,
    session_id="session_123"
)

text = TextChunk(
    type=ChunkType.TEXT,
    content="Hello, world!",
    session_id="session_123"
)

video = VideoChunk(
    type=ChunkType.VIDEO_FRAME,
    data=frame_bytes,
    width=1920,
    height=1080,
    format="JPEG",
    session_id="session_123"
)

# Example 2: Creating control chunks
interrupt = ControlChunk(
    type=ChunkType.CONTROL_INTERRUPT,
    command="interrupt",
    params={"reason": "user_pressed_button"},
    session_id="session_123"
)

config_update = ControlChunk(
    type=ChunkType.CONTROL_CONFIG,
    command="update_config",
    params={
        "vad_threshold": 0.6,
        "silence_threshold_ms": 1000
    },
    session_id="session_123"
)

# Example 3: Creating event chunks
vad_event = EventChunk(
    type=ChunkType.EVENT_VAD_START,
    event_data={"confidence": 0.95},
    source="VADStation",
    session_id="session_123"
)

error_event = EventChunk(
    type=ChunkType.EVENT_ERROR,
    event_data={
        "error": "Timeout waiting for ASR response",
        "duration_ms": 5000
    },
    source="ASRStation",
    session_id="session_123"
)

# Example 4: Type checking
chunk = get_chunk_from_pipeline()

if chunk.is_data():
    # Handle data chunks
    if isinstance(chunk, AudioChunk):
        print(f"Audio: {len(chunk.data)} bytes, {chunk.sample_rate}Hz")
    elif isinstance(chunk, TextChunk):
        print(f"Text: {chunk.content}")
    elif isinstance(chunk, VideoChunk):
        print(f"Video: {chunk.width}x{chunk.height}, {chunk.format}")

elif chunk.is_signal():
    # Handle signal chunks
    if isinstance(chunk, ControlChunk):
        print(f"Control: {chunk.command}, params={chunk.params}")
    elif isinstance(chunk, EventChunk):
        print(f"Event: {chunk.event_type} from {chunk.source}")
```
### 2. Transport 层抽象（流水线接口）
Transport 是流水线的输入输出接口，**同时包含协议处理逻辑**，负责：
1. **输入缓存与控制**：缓存输入音频，VAD 检测后才向 Pipeline 输出
2. **协议转换**：将特定协议消息转换为 Chunk 流
3. **输出缓存与播放控制**：缓存输出音频，控制播放节奏
4. **连接管理**：处理客户端连接生命周期

```python
# vixio/core/transport.py

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable, Optional
from .chunk import Chunk
import asyncio
from collections import deque

class TransportBase(ABC):
    """
    Transport base - interface between external world and pipeline.
    
    Responsibilities:
    1. Accept connections from clients
    2. Protocol conversion (specific to each transport implementation)
    3. Input buffering: Cache audio before VAD detection
    4. Output buffering: Cache and control audio playback
    5. Connection lifecycle management
    
    Design principle:
    - Each Transport implementation includes its own protocol logic
    - User only needs to choose a Transport (e.g., XiaozhiTransport)
    - No need to separately configure protocol handlers
    """
    
    @abstractmethod
    async def start(self) -> None:
        """Start the transport server"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport server"""
        pass
    
    @abstractmethod
    async def input_stream(self, connection_id: str) -> AsyncIterator[Chunk]:
        """
        Get input chunk stream for a connection.
        
        This method should:
        1. Receive raw protocol messages
        2. Convert to Chunks
        3. Buffer audio chunks until VAD detection
        4. Yield chunks to pipeline
        
        Args:
            connection_id: Unique connection identifier
            
        Yields:
            Chunks for pipeline processing
        """
        pass
    
    @abstractmethod
    async def output_chunk(self, connection_id: str, chunk: Chunk) -> None:
        """
        Send a chunk to the client.
        
        This method should:
        1. Buffer audio chunks for smooth playback
        2. Convert chunks to protocol messages
        3. Send to client
        
        Args:
            connection_id: Target connection
            chunk: Chunk from pipeline
        """
        pass
    
    @abstractmethod
    async def on_new_connection(
        self,
        handler: Callable[[str], Awaitable[None]]
    ) -> None:
        """
        Register callback for new connections.
        
        Args:
            handler: Async function called with connection_id when client connects
        """
        pass

class TransportBufferMixin:
    """
    Mixin providing input/output buffering capabilities.
    
    This can be used by Transport implementations to handle buffering.
    """
    
    def __init__(self):
        # Input buffer: Store audio before VAD detection
        self._input_buffers = {}  # connection_id -> deque[AudioChunk]
        self._input_buffer_enabled = {}  # connection_id -> bool
        
        # Output buffer: Store audio for playback control
        self._output_buffers = {}  # connection_id -> deque[AudioChunk]
        self._output_playing = {}  # connection_id -> bool
    
    def _init_buffers(self, connection_id: str):
        """Initialize buffers for a new connection"""
        self._input_buffers[connection_id] = deque(maxlen=1000)  # ~20s at 20ms chunks
        self._input_buffer_enabled[connection_id] = True  # Buffer by default
        self._output_buffers[connection_id] = deque()
        self._output_playing[connection_id] = False
    
    def _cleanup_buffers(self, connection_id: str):
        """Cleanup buffers for disconnected connection"""
        self._input_buffers.pop(connection_id, None)
        self._input_buffer_enabled.pop(connection_id, None)
        self._output_buffers.pop(connection_id, None)
        self._output_playing.pop(connection_id, None)
    
    def _should_buffer_input(self, connection_id: str) -> bool:
        """Check if input should be buffered (before VAD detection)"""
        return self._input_buffer_enabled.get(connection_id, True)
    
    def _enable_input_passthrough(self, connection_id: str):
        """
        Enable input passthrough (after VAD detection).
        
        This should be called when VAD detects voice.
        Also flushes buffered chunks.
        """
        self._input_buffer_enabled[connection_id] = False
    
    def _disable_input_passthrough(self, connection_id: str):
        """
        Disable input passthrough (back to buffering).
        
        This should be called when VAD stops detecting voice.
        """
        self._input_buffer_enabled[connection_id] = True
        # Clear old buffer
        if connection_id in self._input_buffers:
            self._input_buffers[connection_id].clear()
    
    def _add_to_output_buffer(self, connection_id: str, chunk: Chunk):
        """Add chunk to output buffer"""
        if connection_id in self._output_buffers:
            self._output_buffers[connection_id].append(chunk)
    
    async def _flush_output_buffer(self, connection_id: str, send_func):
        """
        Flush output buffer to client.
        
        Args:
            send_func: Async function to send raw data to client
        """
        if connection_id not in self._output_buffers:
            return
        
        self._output_playing[connection_id] = True
        
        try:
            while self._output_buffers[connection_id]:
                chunk = self._output_buffers[connection_id].popleft()
                await send_func(chunk.data)
                # Add small delay for playback pacing
                await asyncio.sleep(0.02)  # 20ms per chunk
        finally:
            self._output_playing[connection_id] = False

# ============ Example Implementation: XiaozhiTransport ============

class XiaozhiTransport(TransportBase, TransportBufferMixin):
    """
    Xiaozhi WebSocket Transport.
    
    This implementation includes:
    1. Xiaozhi protocol handling (audio + control messages)
    2. Input buffering before VAD
    3. Output buffering for smooth playback
    4. WebSocket server management
    
    User only needs to instantiate this class - protocol is built-in.
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        TransportBufferMixin.__init__(self)
        self._host = host
        self._port = port
        self._connections = {}  # connection_id -> websocket
        self._connection_handlers = []
        self._server = None
    
    async def start(self) -> None:
        """Start Xiaozhi WebSocket server"""
        import websockets
        import uuid
        
        async def handle_client(websocket, path):
            connection_id = str(uuid.uuid4())
            self._connections[connection_id] = websocket
            self._init_buffers(connection_id)
            
            # Notify handlers
            for handler in self._connection_handlers:
                await handler(connection_id)
        
        self._server = await websockets.serve(
            handle_client,
            self._host,
            self._port
        )
        logger.info(f"Xiaozhi server started on ws://{self._host}:{self._port}")
    
    async def stop(self) -> None:
        """Stop WebSocket server"""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
    
    async def input_stream(self, connection_id: str) -> AsyncIterator[Chunk]:
        """
        Convert Xiaozhi WebSocket messages to Chunk stream.
        
        Xiaozhi protocol:
        - Binary data: PCM audio (16kHz, mono, 16-bit)
        - JSON: Control messages (interrupt, hello, etc.)
        
        Buffering strategy:
        - Buffer audio chunks initially
        - When VAD detects voice (EVENT_VAD_START), flush buffer and passthrough
        - When VAD stops (EVENT_VAD_END), resume buffering
        """
        websocket = self._connections.get(connection_id)
        if not websocket:
            return
        
        try:
            async for raw_message in websocket:
                # Decode Xiaozhi message to Chunk
                chunk = self._decode_xiaozhi_message(raw_message, connection_id)
                if not chunk:
                    continue
                
                # Handle VAD events to control buffering
                if chunk.type == ChunkType.EVENT_VAD_START:
                    self._enable_input_passthrough(connection_id)
                    # Flush buffered audio first
                    if connection_id in self._input_buffers:
                        for buffered_chunk in self._input_buffers[connection_id]:
                            yield buffered_chunk
                        self._input_buffers[connection_id].clear()
                    yield chunk
                
                elif chunk.type == ChunkType.EVENT_VAD_END:
                    yield chunk
                    self._disable_input_passthrough(connection_id)
                
                # Audio chunks: buffer or passthrough based on VAD state
                elif chunk.type == ChunkType.AUDIO_RAW:
                    if self._should_buffer_input(connection_id):
                        # Buffer audio (before VAD detection)
                        self._input_buffers[connection_id].append(chunk)
                    else:
                        # Passthrough audio (after VAD detection)
                        yield chunk
                
                # Other chunks: always passthrough
                else:
                    yield chunk
        
        except Exception as e:
            logger.error(f"Input stream error for {connection_id}: {e}")
            yield Chunk(
                type=ChunkType.EVENT_ERROR,
                data=str(e),
                session_id=connection_id
            )
        finally:
            self._cleanup_buffers(connection_id)
            if connection_id in self._connections:
                del self._connections[connection_id]
    
    async def output_chunk(self, connection_id: str, chunk: Chunk) -> None:
        """
        Convert Chunk to Xiaozhi message and send.
        
        Output buffering strategy:
        - Buffer audio chunks
        - Play at controlled rate (20ms per chunk)
        - This prevents overwhelming the client
        """
        websocket = self._connections.get(connection_id)
        if not websocket:
            return
        
        try:
            # Convert chunk to Xiaozhi message
            raw_message = self._encode_xiaozhi_message(chunk)
            if not raw_message:
                return
            
            # Audio chunks: convert PCM to Opus and send
            if chunk.type == ChunkType.AUDIO_RAW:
                # Convert PCM to Opus
                opus_data = self._encode_audio(chunk.data)
                # Buffer for smooth playback
                self._add_to_output_buffer(connection_id, opus_data)
                
                # Start playback if not already playing
                if not self._output_playing.get(connection_id):
                    await self._flush_output_buffer(
                        connection_id,
                        lambda data: websocket.send(data)
                    )
            else:
                # Non-audio: send immediately
                await websocket.send(raw_message)
        
        except Exception as e:
            logger.error(f"Output error for {connection_id}: {e}")
    
    async def on_new_connection(
        self,
        handler: Callable[[str], Awaitable[None]]
    ) -> None:
        """Register connection handler"""
        self._connection_handlers.append(handler)
    
    # ============ Xiaozhi Protocol Methods ============
    
    def _decode_xiaozhi_message(self, raw_message: bytes, connection_id: str) -> Optional[Chunk]:
        """
        Decode Xiaozhi message to Chunk.
        
        Xiaozhi protocol:
        - JSON: {"type": "interrupt"} -> CONTROL_INTERRUPT
        - JSON: {"type": "hello", "data": {...}} -> CONTROL_HELLO
        - Binary: PCM audio data -> AUDIO_RAW
        """
        # Try parse as JSON (control message)
        try:
            import json
            msg = json.loads(raw_message)
            
            if msg.get("type") == "interrupt":
                return ControlChunk(
                    type=ChunkType.CONTROL_INTERRUPT,
                    command="interrupt",
                    session_id=connection_id
                )
            elif msg.get("type") == "hello":
                return ControlChunk(
                    type=ChunkType.CONTROL_HELLO,
                    command="hello",
                    params=msg.get("data", {}),
                    session_id=connection_id
                )
            elif msg.get("type") == "stop":
                return ControlChunk(
                    type=ChunkType.CONTROL_STOP,
                    command="stop",
                    session_id=connection_id
                )
        except:
            pass
        
        # Treat as binary audio data (PCM, 16kHz, mono, 16-bit)
        return AudioChunk(
            type=ChunkType.AUDIO_RAW,
            data=raw_message,
            sample_rate=16000,
            channels=1,
            session_id=connection_id
        )
    
    def _encode_xiaozhi_message(self, chunk: Chunk) -> Optional[bytes]:
        """
        Encode Chunk to Xiaozhi message.
        
        Xiaozhi protocol:
        - AUDIO_RAW (PCM) -> convert to Opus -> binary data
        - Events -> JSON: {"type": "event.xxx", "data": ..., "timestamp": ...}
        """
        import json
        
        # Audio: convert PCM to Opus and send as binary
        if chunk.type == ChunkType.AUDIO_RAW:
            # Convert PCM to Opus (done by transport layer)
            opus_data = self.opus_codec.encode(chunk.data) if self.opus_codec else chunk.data
            return opus_data
        
        # Signals: send as JSON
        elif chunk.is_signal():
            return json.dumps({
                "type": chunk.type.value,
                "data": chunk.data,
                "timestamp": chunk.timestamp
            }).encode('utf-8')
        
        return None

# ============ Example Implementation: StandardWebSocketTransport ============

class StandardWebSocketTransport(TransportBase, TransportBufferMixin):
    """
    Standard WebSocket Transport with JSON protocol.
    
    This is a generic WebSocket transport using standard JSON format.
    Suitable for web clients, testing, etc.
    
    Message format:
    - Input: {"type": "audio", "data": "base64..."} or {"type": "control", "command": "interrupt"}
    - Output: {"type": "audio", "data": "base64..."} or {"type": "event", "name": "tts.start"}
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        TransportBufferMixin.__init__(self)
        self._host = host
        self._port = port
        self._connections = {}
        self._connection_handlers = []
        self._server = None
    
    # Implementation similar to XiaozhiTransport, but with different protocol encoding/decoding
    # ... (省略具体实现，与 XiaozhiTransport 类似)
```

### 3. ControlBus 中断管理（控制总线）
ControlBus 是中心化的中断管理系统，负责：
1. **中断信号传递**：任何组件都可以发送中断信号
2. **Turn ID 管理**：每次中断时递增 turn_id，标记新的对话回合
3. **解耦控制流**：组件间无需直接通信，通过 ControlBus 协调

```python
# vixio/core/control_bus.py

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time

@dataclass
class InterruptSignal:
    """
    Interrupt signal sent through ControlBus.
    
    Attributes:
        source: Component that sent the interrupt (e.g., "vad", "turn_detector", "transport")
        reason: Human-readable reason for interrupt
        turn_id: Turn ID when interrupt was sent
        timestamp: When the interrupt was sent
        metadata: Additional context (optional)
    """
    source: str
    reason: str
    turn_id: int
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

class ControlBus:
    """
    Centralized control bus for managing interrupts and turn transitions.
    
    Features:
    - Any component can send interrupt signals
    - Monitor task coordinates turn transitions
    - Provides current turn_id for all components
    - Thread-safe and async-friendly
    
    Usage:
        bus = ControlBus()
        
        # Send interrupt
        await bus.send_interrupt(source="vad", reason="user_speaking")
        
        # Wait for interrupt (used by Session)
        signal = await bus.wait_for_interrupt()
        
        # Get current turn ID (used by Stations)
        turn_id = bus.get_current_turn_id()
    """
    
    def __init__(self):
        """Initialize control bus."""
        self._current_turn_id = 0
        self._interrupt_queue = asyncio.Queue()
        self._interrupt_event = asyncio.Event()
        self._latest_interrupt: Optional[InterruptSignal] = None
        self._lock = asyncio.Lock()
    
    def get_current_turn_id(self) -> int:
        """Get current turn ID."""
        return self._current_turn_id
    
    async def send_interrupt(
        self,
        source: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Send an interrupt signal."""
        signal = InterruptSignal(
            source=source,
            reason=reason,
            turn_id=self._current_turn_id,
            metadata=metadata or {}
        )
        
        # Queue the interrupt for processing
        await self._interrupt_queue.put(signal)
        
        # Set event flag for immediate notification
        self._interrupt_event.set()
    
    async def wait_for_interrupt(self) -> InterruptSignal:
        """
        Wait for the next interrupt signal.
        
        This should be called by Session to handle interrupts.
        """
        signal = await self._interrupt_queue.get()
        
        # Increment turn ID for new turn
        async with self._lock:
            self._current_turn_id += 1
            signal.turn_id = self._current_turn_id
            self._latest_interrupt = signal
        
        return signal
```

### 4. Station 处理抽象（工站）
Station 是流水线上的工站，处理逻辑遵循三大原则：
1. **Data Chunk（产品）**：加工转换，输出新产品
2. **Signal Chunk（消息）**：立即透传 + 触发自身状态变化
3. **Turn-aware**：自动丢弃旧 turn 的数据，新 turn 时重置状态

```python
# vixio/core/station.py

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, TYPE_CHECKING
from .chunk import Chunk, ChunkType
import logging

if TYPE_CHECKING:
    from core.control_bus import ControlBus

class Station(ABC):
    """
    Base station - workstation in the pipeline.
    
    Processing rules:
    1. Data chunks: Transform and yield processed results
    2. Signal chunks: Immediately passthrough + optionally yield response chunks
    3. Turn-aware: Discard chunks from old turns, reset state on new turns
    
    Subclasses override process_chunk() to implement custom logic.
    """
    
    def __init__(self, name: Optional[str] = None):
        """
        Initialize station.
        
        Args:
            name: Station name for logging (defaults to class name)
        """
        self.name = name or self.__class__.__name__
        self.logger = logging.getLogger(f"station.{self.name}")
        
        # Turn tracking
        self.current_turn_id = 0
        self.control_bus: Optional['ControlBus'] = None
    
    async def process(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
        Main processing loop with turn-awareness.
        
        Turn handling:
        - Discard chunks from old turns (turn_id < current_turn_id)
        - Reset state when new turn starts (turn_id > current_turn_id)
        - Process chunks from current turn normally
        
        This method should NOT be overridden by subclasses.
        """
        async for chunk in input_stream:
            # Check turn ID
            if chunk.turn_id < self.current_turn_id:
                # Old turn, discard
                self.logger.debug(f"Discarding old chunk: turn {chunk.turn_id} < {self.current_turn_id}")
                continue
            
            if chunk.turn_id > self.current_turn_id:
                # New turn started, reset state
                self.logger.info(f"New turn detected: {chunk.turn_id} (was {self.current_turn_id})")
                self.current_turn_id = chunk.turn_id
                await self.reset_state()
            
            # Process chunk
            try:
                async for output_chunk in self.process_chunk(chunk):
                    # Propagate turn ID
                    output_chunk.turn_id = self.current_turn_id
                    yield output_chunk
            except Exception as e:
                self.logger.error(f"Error processing {chunk}: {e}", exc_info=True)
    
    async def reset_state(self) -> None:
        """
        Reset station state for new turn.
        
        Called automatically when a new turn starts.
        Subclasses can override to clear accumulated state.
        
        Example: ASR station clears audio buffer, Agent clears conversation history.
        """
        self.logger.debug(f"State reset for turn {self.current_turn_id}")
    
    @abstractmethod
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process a single chunk.
        
        For data chunks:
        - Transform the data and yield new chunks
        - Or passthrough unchanged (yield chunk)
        
        For signal chunks:
        - Must yield the signal to pass it through (yield chunk)
        - Can update internal state
        - Can optionally yield additional chunks (e.g., ASR yields TEXT on EVENT_TURN_END)
        
        Args:
            chunk: Input chunk (data or signal)
            
        Yields:
            Output chunks (for signals, at minimum yield the signal itself to pass it through)
        """
        pass

# ============ Example Stations ============

# Example 1: VAD Station - monitors audio, emits events
class VADStation(Station):
    """
    VAD workstation: Detects voice activity in PCM audio stream.
    
    Input: AUDIO_RAW (PCM format)
    Output: AUDIO_RAW (passthrough) + EVENT_VAD_START/END
    
    Note: Expects PCM audio data. Transport layers handle format conversion.
    """
    
    def __init__(self, vad_provider, name: str = "VAD"):
        super().__init__(name=name)
        self.vad = vad_provider
        self._is_speaking = False
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                # Reset VAD state on interrupt
                self._is_speaking = False
                self.vad.reset()
            # Always passthrough signals
            yield chunk
            return
        
        # Only process audio data (PCM)
        if not is_audio_chunk(chunk) or chunk.type != ChunkType.AUDIO_RAW:
            yield chunk
            return
        
        # Detect voice in PCM audio
        audio_data = chunk.data if isinstance(chunk.data, bytes) else b''
        has_voice = self.vad.detect(audio_data)
        
        # Emit VAD events on state change
        if has_voice and not self._is_speaking:
            # Voice activity started
            yield EventChunk(
                type=ChunkType.EVENT_VAD_START,
                event_data={"has_voice": True},
                source=self.name,
                session_id=chunk.session_id
            )
            self._is_speaking = True
        
        elif not has_voice and self._is_speaking:
            # Voice activity ended
            yield EventChunk(
                type=ChunkType.EVENT_VAD_END,
                event_data={"has_voice": False},
                source=self.name,
                session_id=chunk.session_id
            )
            self._is_speaking = False
        
        # Always passthrough audio
        yield chunk

# Example 2: ASR Station - converts audio to text
class ASRStation(Station):
    """
    ASR workstation: Transcribes audio to text.
    
    Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
    Output: TEXT (transcription result)
    """
    
    def __init__(self, asr_provider):
        super().__init__("ASR")
        self.asr = asr_provider
        self._audio_buffer = []
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Transcribe when turn ends
            if chunk.type == ChunkType.EVENT_TURN_END:
                if self._audio_buffer:
                    text = await self.asr.transcribe(self._audio_buffer)
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        content=text,
                        session_id=chunk.session_id
                    )
                    self._audio_buffer.clear()
            # Clear buffer on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._audio_buffer.clear()
            return
        
        # Handle data
        if chunk.type == ChunkType.AUDIO_RAW:
            # Collect audio for later transcription
            self._audio_buffer.append(chunk.data)
            yield chunk  # Passthrough for downstream (e.g., echo)
        else:
            # Passthrough other data types
            yield chunk

# Example 3: Agent Station - Agent conversation
class AgentStation(Station):
    """
    Agent workstation: Processes text through Agent.
    
    Input: TEXT
    Output: TEXT_DELTA (streaming)
    """
    
    def __init__(self, agent_provider):
        super().__init__("Agent")
        self.agent = agent_provider
        self._generation_task = None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Cancel ongoing generation on interrupt
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                if self._generation_task:
                    self._generation_task.cancel()
                    self._generation_task = None
            return
        
        # Only process text data
        if chunk.type != ChunkType.TEXT:
            yield chunk  # Passthrough other data
            return
        
        # Generate streaming response
        async for text_delta in self.agent.chat(chunk.content):
            yield TextDeltaChunk(
                type=ChunkType.TEXT_DELTA,
                delta=text_delta,
                session_id=chunk.session_id
            )

# Example 4: TTS Station - converts text to audio
class TTSStation(Station):
    """
    TTS workstation: Synthesizes text to audio.
    
    Input: TEXT_DELTA
    Output: AUDIO_RAW (PCM, streaming) + EVENT_TTS_START/STOP
    """
    
    def __init__(self, tts_provider):
        super().__init__("TTS")
        self.tts = tts_provider
        self._is_speaking = False
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Stop speaking on interrupt
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                if self._is_speaking:
                    self.tts.cancel()
                    yield EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"reason": "user_interrupt"},
                        source=self.name,
                        session_id=chunk.session_id
                    )
                    self._is_speaking = False
            return
        
        # Only process text deltas
        if chunk.type != ChunkType.TEXT_DELTA:
            yield chunk  # Passthrough other data
            return
        
        # Emit TTS start event (first time)
        if not self._is_speaking:
            yield EventChunk(
                type=ChunkType.EVENT_TTS_START,
                source=self.name,
                session_id=chunk.session_id
            )
            self._is_speaking = True
        
        # Generate streaming audio (PCM format)
        async for audio_data in self.tts.synthesize(chunk.delta):
            yield AudioChunk(
                type=ChunkType.AUDIO_RAW,
                data=audio_data,
                sample_rate=16000,
                channels=1,
                session_id=chunk.session_id
            )
```
### 5. Pipeline 组装（流水线）
Pipeline 是流水线，串联多个工站（Stations）。**实际实现采用异步并行模型**，每个 Station 运行在独立的 asyncio 任务中，通过 Queue 连接：

```python
# vixio/core/pipeline.py

import asyncio
from typing import List, Optional, AsyncIterator, Set
from .station import Station
from .chunk import Chunk
from .control_bus import ControlBus
import logging

class Pipeline:
    """
    Pipeline - assembly line that connects workstations (stations).
    
    Async Design:
    - Each station runs in an independent asyncio task
    - Stations connected via asyncio.Queue for non-blocking parallel processing
    - ControlBus integration for interrupt handling
    - Can clear queues and cancel tasks on interrupt
    """
    
    def __init__(
        self,
        stations: List[Station],
        control_bus: Optional[ControlBus] = None,
        name: Optional[str] = None,
        queue_size: int = 100
    ):
        """
        Initialize pipeline.
        
        Args:
            stations: List of stations to chain together
            control_bus: ControlBus for interrupt handling (optional)
            name: Pipeline name for logging
            queue_size: Maximum size for inter-station queues
        """
        self.stations = stations
        self.control_bus = control_bus
        self.name = name or "Pipeline"
        self.queue_size = queue_size
        
        # Runtime state
        self.queues: List[asyncio.Queue] = []
        self.tasks: List[asyncio.Task] = []
        self.cancellable_task_indices: Set[int] = set()  # Indices of slow tasks (Agent, TTS)
        self._running = False
        
        # Pass ControlBus to all stations
        if self.control_bus:
            for station in self.stations:
                station.control_bus = self.control_bus
    
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
        Run the pipeline - all stations in parallel with queues.
        
        Async Flow:
        1. Create queues between stations
        2. Start each station as independent task
        3. Feed input_stream into first queue
        4. Yield from final queue
        
        Args:
            input_stream: Source of chunks (usually from Transport)
            
        Yields:
            Processed chunks from final station
        """
        self._running = True
        
        try:
            # Create queues: n stations need n+1 queues
            # Queue[0] gets input, Queue[n] provides output
            self.queues = [asyncio.Queue(maxsize=self.queue_size) for _ in range(len(self.stations) + 1)]
            
            # Start all station tasks
            self.tasks = []
            for i, station in enumerate(self.stations):
                task = asyncio.create_task(
                    self._run_station(station, i, self.queues[i], self.queues[i + 1]),
                    name=f"{self.name}-{station.name}"
                )
                self.tasks.append(task)
                
                # Mark slow tasks as cancellable
                if self._is_slow_station(station):
                    self.cancellable_task_indices.add(i)
            
            # Start input feeder task
            input_task = asyncio.create_task(
                self._feed_input(input_stream, self.queues[0])
            )
            
            # Yield from output queue
            while self._running:
                try:
                    chunk = await asyncio.wait_for(self.queues[-1].get(), timeout=0.1)
                    
                    # None is used as end-of-stream marker
                    if chunk is None:
                        break
                    
                    yield chunk
                except asyncio.TimeoutError:
                    # Check if all tasks are done
                    if input_task.done() and self.queues[-1].empty():
                        all_done = all(task.done() for task in self.tasks)
                        if all_done:
                            break
                    continue
        
        finally:
            # Cleanup
            self._running = False
            await self._cleanup_tasks()
    
    async def _feed_input(self, input_stream: AsyncIterator[Chunk], input_queue: asyncio.Queue) -> None:
        """Feed input stream into first queue."""
        try:
            async for chunk in input_stream:
                await input_queue.put(chunk)
        except Exception as e:
            logger.error(f"Error feeding input: {e}")
        finally:
            # Signal end of input
            await input_queue.put(None)
    
    async def _run_station(
        self,
        station: Station,
        index: int,
        input_queue: asyncio.Queue,
        output_queue: asyncio.Queue
    ) -> None:
        """Run a single station task."""
        try:
            async for chunk in self._queue_iterator(input_queue):
                # Process chunk through station
                async for output_chunk in station.process_chunk(chunk):
                    await output_queue.put(output_chunk)
            
            # Signal downstream that this station is done
            await output_queue.put(None)
            
        except asyncio.CancelledError:
            logger.info(f"Station {station.name} cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in station {station.name}: {e}")
            await output_queue.put(None)
    
    async def _queue_iterator(self, queue: asyncio.Queue) -> AsyncIterator[Chunk]:
        """Iterate over queue until None is received."""
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    
    def clear_queues(self, from_stage: int = 1) -> None:
        """
        Clear queues from specified stage onwards.
        
        This is used during interrupt handling to discard pending chunks.
        Stage 0 (input queue) is typically preserved.
        """
        cleared_count = 0
        for i in range(from_stage, len(self.queues)):
            queue = self.queues[i]
            while not queue.empty():
                try:
                    queue.get_nowait()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break
        
        logger.info(f"Cleared {cleared_count} chunks from queues")

# ============ Usage Example ============

# Example 1: Voice conversation pipeline
def create_voice_pipeline(config):
    """Create a typical voice conversation pipeline"""
    return Pipeline([
        # Stage 1: Audio input processing
        VADStation(vad_provider),          # Detect voice activity
        TurnDetectorStation(config),        # Detect turn boundaries
        
        # Stage 2: Speech to text
        ASRStation(asr_provider),           # Transcribe audio -> text
        
        # Stage 3: Language understanding & generation
        AgentStation(agent_provider),       # Generate response (streaming)
        
        # Stage 4: Text to speech
        SentenceSplitterStation(),          # Split into sentences
        TTSStation(tts_provider),           # Synthesize speech
    ], name="VoiceConversation")

# Example 2: Minimal echo pipeline
def create_echo_pipeline():
    """Simple pipeline that echoes audio back"""
    return Pipeline([
        VADStation(vad_provider),
        # Audio passthrough - no transformation
    ], name="Echo")

# Example 3: Custom pipeline with parallel branches (future extension)
# This would require ParallelPipeline implementation
def create_multimodal_pipeline():
    """Pipeline with parallel audio and vision processing"""
    audio_branch = Pipeline([
        VADStation(),
        ASRStation(),
    ])
    
    vision_branch = Pipeline([
        FaceDetectorStation(),
        EmotionAnalyzerStation(),
    ])
    
    return ParallelPipeline([
        audio_branch,
        vision_branch,
        MergeStation(),  # Combine results
        AgentStation(),  # Multimodal agent
        TTSStation(),
    ])
```
### 6. TimeoutMonitor 超时监控（可选）
TimeoutMonitor 监控对话流程的各个阶段，在超时时发送中断信号：

```python
# vixio/core/timeout_monitor.py

import asyncio
import logging
from typing import Optional
from dataclasses import dataclass
import time

@dataclass
class TimeoutConfig:
    """
    Configuration for timeout monitoring.
    
    Attributes:
        agent_timeout: Agent processing timeout in seconds
        tts_timeout: TTS generation timeout in seconds
        turn_timeout: Overall turn timeout in seconds
    """
    agent_timeout: float = 30.0
    tts_timeout: float = 60.0
    turn_timeout: float = 120.0

class TimeoutMonitor:
    """
    Monitor conversation flow timeouts and send interrupt signals.
    
    This monitor tracks various stages of conversation processing and sends
    interrupt signals via ControlBus when timeouts occur.
    
    Usage:
        monitor = TimeoutMonitor(control_bus, config)
        
        # Start monitoring a turn
        await monitor.start_turn(session_id)
        
        # Mark stage starts
        await monitor.mark_agent_start(session_id)
        await monitor.mark_tts_start(session_id)
        
        # Mark stage ends
        await monitor.mark_agent_end(session_id)
        await monitor.mark_tts_end(session_id)
        
        # End turn
        await monitor.end_turn(session_id)
    """
    
    def __init__(self, control_bus, config: Optional[TimeoutConfig] = None):
        """
        Initialize timeout monitor.
        
        Args:
            control_bus: ControlBus instance for sending interrupt signals
            config: Timeout configuration
        """
        self.control_bus = control_bus
        self.config = config or TimeoutConfig()
        
        # Track active timeouts
        self._turn_tasks = {}  # session_id -> turn timeout task
        self._agent_tasks = {}  # session_id -> agent timeout task
        self._tts_tasks = {}  # session_id -> tts timeout task
    
    async def start_turn(self, session_id: str) -> None:
        """Start monitoring a turn."""
        # Start turn timeout task
        task = asyncio.create_task(
            self._monitor_turn_timeout(session_id),
            name=f"turn-timeout-{session_id[:8]}"
        )
        self._turn_tasks[session_id] = task
    
    async def _monitor_turn_timeout(self, session_id: str) -> None:
        """Monitor overall turn timeout."""
        try:
            await asyncio.sleep(self.config.turn_timeout)
            # Timeout reached, send interrupt
            await self.control_bus.send_interrupt(
                source="TimeoutMonitor",
                reason=f"turn_timeout ({self.config.turn_timeout}s)",
                metadata={"session_id": session_id}
            )
        except asyncio.CancelledError:
            pass
```

### 7. Session 管理（连接 Transport 和 Pipeline）
SessionManager 为每个客户端连接创建独立的 Pipeline 和 ControlBus 实例，处理中断机制：

```python
# vixio/core/session.py

import asyncio
from typing import Callable, Dict, Optional, AsyncIterator
from .transport import TransportBase
from .pipeline import Pipeline
from .control_bus import ControlBus
from .chunk import Chunk, ChunkType
import logging

class SessionManager:
    """
    Session manager - connects Transport to Pipelines.
    
    Responsibilities:
    1. Listen for new connections from Transport
    2. Create a Pipeline instance for each connection
    3. Route chunks: Transport input -> Pipeline -> Transport output
    4. Manage pipeline lifecycle
    5. Handle interrupts via ControlBus
    
    Design:
    - Each connection gets its own Pipeline and ControlBus instance
    - Pipeline runs as long as connection is alive
    - ControlBus handles interrupts and turn transitions
    - Automatically cleanup on disconnect
    """
    
    def __init__(
        self,
        transport: TransportBase,
        pipeline_factory: Callable[[], Pipeline]
    ):
        self.transport = transport
        self.pipeline_factory = pipeline_factory
        self._sessions: Dict[str, asyncio.Task] = {}  # connection_id -> pipeline task
        self._control_buses: Dict[str, ControlBus] = {}  # connection_id -> control bus
        self._pipelines: Dict[str, Pipeline] = {}  # connection_id -> pipeline
        self._interrupt_tasks: Dict[str, asyncio.Task] = {}  # connection_id -> interrupt handler task
    
    async def start(self) -> None:
        """
        Start the session manager.
        
        This registers connection handler and starts the transport.
        """
        # Register for new connections
        await self.transport.on_new_connection(self._handle_connection)
        
        # Start transport server
        await self.transport.start()
    
    async def stop(self) -> None:
        """
        Stop the session manager.
        
        This cancels all active pipelines and stops transport.
        """
        # Cancel all pipeline tasks
        for task in self._sessions.values():
            task.cancel()
        
        # Wait for all to finish
        if self._sessions:
            await asyncio.gather(*self._sessions.values(), return_exceptions=True)
        
        # Stop transport
        await self.transport.stop()
    
    async def _handle_connection(self, connection_id: str) -> None:
        """
        Handle new client connection.
        
        Creates pipeline, control bus, and interrupt handler for this connection.
        """
        logger.info(f"New connection: {connection_id[:8]}")
        
        # Create ControlBus for this session
        control_bus = ControlBus()
        self._control_buses[connection_id] = control_bus
        
        # Create fresh pipeline with ControlBus
        pipeline = self.pipeline_factory()
        pipeline.control_bus = control_bus
        self._pipelines[connection_id] = pipeline
        
        # Start interrupt handler task
        interrupt_task = asyncio.create_task(
            self._handle_interrupts(connection_id, pipeline, control_bus),
            name=f"interrupt-handler-{connection_id[:8]}"
        )
        self._interrupt_tasks[connection_id] = interrupt_task
        
        # Run pipeline in background task
        task = asyncio.create_task(
            self._run_pipeline(connection_id, pipeline),
            name=f"pipeline-{connection_id[:8]}"
        )
        
        self._sessions[connection_id] = task
        
        # Cleanup when done
        task.add_done_callback(lambda _: self._cleanup_session(connection_id))
    
    async def _handle_interrupts(
        self,
        connection_id: str,
        pipeline: Pipeline,
        control_bus: ControlBus
    ) -> None:
        """
        Handle interrupt signals for a connection.
        
        Listens for interrupts on ControlBus and:
        1. Clears pipeline queues (except input queue)
        2. Cancels slow tasks (Agent, TTS) [已移除，改为基于 turn_id 丢弃]
        3. Logs the interrupt
        4. Sends control events to client
        """
        try:
            while True:
                # Wait for interrupt signal
                interrupt = await control_bus.wait_for_interrupt()
                
                logger.warning(f"[{connection_id[:8]}] Interrupt: {interrupt.source} - {interrupt.reason}")
                
                # Clear pipeline queues (keep input queue)
                pipeline.clear_queues(from_stage=1)
                
                # Note: We do NOT cancel tasks anymore!
                # Tasks continue running and will automatically discard old chunks
                # based on turn_id. This ensures they can process new chunks.
                
                # Clear transport send queue to stop pending audio
                if hasattr(self.transport, 'clear_send_queue'):
                    self.transport.clear_send_queue(connection_id)
                
                # Send control events immediately to client
                try:
                    from .chunk import EventChunk, ChunkType
                    
                    # Send TTS stop event - IMMEDIATE delivery to stop client playback
                    stop_event = EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"reason": "interrupted"},
                        source="SessionManager",
                        session_id=connection_id
                    )
                    
                    # Send state transition to LISTENING
                    state_event = EventChunk(
                        type=ChunkType.EVENT_STATE_LISTENING,
                        event_data={"reason": "interrupted"},
                        source="SessionManager",
                        session_id=connection_id
                    )
                    
                    # Use send_immediate if available, otherwise fallback
                    if hasattr(self.transport, 'send_immediate'):
                        await self.transport.send_immediate(connection_id, stop_event)
                        await self.transport.send_immediate(connection_id, state_event)
                    else:
                        await self.transport.output_chunk(connection_id, stop_event)
                        await self.transport.output_chunk(connection_id, state_event)
                        
                except Exception as e:
                    logger.warning(f"Failed to send interrupt events: {e}")
                
                # Clear interrupt event flag
                control_bus.clear_interrupt_event()
                
                logger.info(f"[{connection_id[:8]}] Interrupt handled, new turn_id={control_bus.get_current_turn_id()}")
        
        except asyncio.CancelledError:
            logger.info(f"Interrupt handler cancelled for {connection_id[:8]}")
    
    async def _run_pipeline(self, connection_id: str, pipeline: Pipeline) -> None:
        """
        Run pipeline for a connection.
        
        Flow:
        1. Get input stream from transport
        2. Wrap input stream to detect CONTROL_INTERRUPT chunks
        3. Run pipeline (yields output chunks)
        4. Send output chunks back to transport
        """
        try:
            # Get input stream from transport
            raw_input_stream = self.transport.input_stream(connection_id)
            
            # Get ControlBus for this connection
            control_bus = self._control_buses.get(connection_id)
            
            # Wrap input stream to detect CONTROL_INTERRUPT
            input_stream = self._wrap_input_stream(raw_input_stream, control_bus, connection_id)
            
            # Run pipeline and send outputs
            async for output_chunk in pipeline.run(input_stream):
                await self.transport.output_chunk(connection_id, output_chunk)
        
        except asyncio.CancelledError:
            # Session cancelled (normal shutdown)
            pass
        
        except Exception as e:
            # Pipeline error
            logger.error(f"Pipeline error for {connection_id}: {e}")
        
        finally:
            # Ensure cleanup
            self._cleanup_session(connection_id)
    
    async def _wrap_input_stream(
        self,
        input_stream: AsyncIterator[Chunk],
        control_bus: Optional[ControlBus],
        connection_id: str
    ) -> AsyncIterator[Chunk]:
        """
        Wrap input stream to detect CONTROL_INTERRUPT chunks and send to ControlBus.
        
        This allows client-initiated interrupts to trigger the interrupt mechanism.
        """
        async for chunk in input_stream:
            # Detect CONTROL_INTERRUPT chunk and send to ControlBus
            if chunk.type == ChunkType.CONTROL_INTERRUPT and control_bus:
                logger.info(f"[{connection_id[:8]}] Client interrupt detected")
                
                # Send interrupt signal to ControlBus
                await control_bus.send_interrupt(
                    source="Transport",
                    reason=chunk.params.get("reason", "client_interrupt"),
                    metadata={
                        "connection_id": connection_id,
                        "command": chunk.command
                    }
                )
            
            # Always yield the chunk
            yield chunk
    
    def _cleanup_session(self, connection_id: str) -> None:
        """Cleanup session resources"""
        if connection_id in self._sessions:
            del self._sessions[connection_id]

# ============ Usage Example ============

async def main():
    """Example: Run a voice conversation server"""
    
    # 1. Setup transport (protocol is built-in)
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000
    )
    
    # 3. Setup pipeline factory
    def create_pipeline():
        return Pipeline([
            VADStation(vad_provider),
            TurnDetectorStation(silence_threshold=1000),
            ASRStation(asr_provider),
            AgentStation(agent_provider),
            TTSStation(tts_provider),
        ], name="VoiceChat")
    
    # 4. Setup session manager
    manager = SessionManager(
        transport=transport,
        pipeline_factory=create_pipeline
    )
    
    # 5. Start server
    await manager.start()
    
    # Keep running
    try:
        await asyncio.Event().wait()  # Wait forever
    except KeyboardInterrupt:
        pass
    finally:
        await manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## 设计优势

### 1. 清晰的职责分离
- **Transport**：只负责协议转换（WebSocket/HTTP ↔ Chunk），对业务逻辑零感知
- **Station**：只负责单一任务（VAD/ASR/Agent/TTS），对传输协议零感知
- **Pipeline**：只负责串联 Station，像搭积木一样组装
- **SessionManager**：只负责连接生命周期管理

### 2. 真正的流式处理
- 所有 Station 都是 `AsyncIterator[Chunk]`，天然流式
- **Data Chunk** 在流水线上实时转换，无需等待完整数据
- **Signal Chunk** 立即透传，实现实时控制
- 例如：Agent 输出第一个字就可以开始 TTS，无需等待完整句子

### 3. 信号机制的优雅设计
- **Data Chunk（产品）**：被加工转换，如 Audio → Text → Audio
- **Signal Chunk（消息）**：透传 + 触发状态变化，如 INTERRUPT 停止 TTS
- 工站比喻形象且易理解：消息立即传递，产品需要加工

### 4. 极高的扩展性
- **新增 Station**：实现 `process_chunk()` 即可，无需改动其他代码
- **新增协议**：实现 `TransportBase` + Protocol 即可，Pipeline 无需修改
- **新增功能**：组合现有 Station 即可，如 `[VAD, Echo]` 就是回声服务器

### 5. 易于测试和调试
```python
# 单元测试：测试单个 Station
async def test_vad_station():
    station = VADStation(mock_vad)
    input_chunks = [AudioChunk(...), AudioChunk(...)]
    outputs = [chunk async for chunk in station.process(input_chunks)]
    assert outputs[1].type == ChunkType.EVENT_VAD_START

# 集成测试：测试 Pipeline
async def test_pipeline():
    pipeline = Pipeline([VADStation(), ASRStation()])
    outputs = [chunk async for chunk in pipeline.run(audio_stream)]
    text_chunks = [c for c in outputs if c.type == ChunkType.TEXT]
    assert len(text_chunks) > 0
```

### 6. 协议无关性
同一套 Pipeline 可以轻松支持多种 Transport（协议内置）：
```python
# Xiaozhi WebSocket (for Xiaozhi devices)
xiaozhi_transport = XiaozhiTransport(host="0.0.0.0", port=8000)
xiaozhi_manager = SessionManager(xiaozhi_transport, create_pipeline)

# Standard WebSocket (for web clients)
web_transport = StandardWebSocketTransport(host="0.0.0.0", port=8081)
web_manager = SessionManager(web_transport, create_pipeline)

# HTTP REST API (for mobile apps)
http_transport = HTTPTransport(host="0.0.0.0", port=8082)
http_manager = SessionManager(http_transport, create_pipeline)

# 用户只需要选择 Transport，不需要关心协议细节
```

### 7. Turn-aware 机制的优雅设计
- **自动丢弃旧数据**：基于 turn_id，Station 无需手动处理中断
- **无需任务取消**：任务持续运行，只丢弃旧 turn 的数据
- **避免竞态条件**：turn_id 自增，保证顺序性
- **简化 Station 实现**：Station 只需检查 turn_id，无需复杂的中断逻辑

### 8. ControlBus 的中心化控制
- **解耦组件**：组件间无需直接通信，通过 ControlBus 协调
- **统一中断管理**：所有中断信号都经过 ControlBus
- **灵活的中断源**：VAD、Turn Detector、Timeout Monitor、客户端都可发送中断
- **便于调试**：所有中断信号集中记录和追踪

### 9. 异步并行 Pipeline
- **真正并行**：每个 Station 在独立任务中运行，无阻塞
- **队列缓冲**：asyncio.Queue 提供天然的背压控制
- **弹性清理**：中断时可清理队列，快速响应
- **性能优化**：标记慢任务（Agent、TTS），可选择性处理

### 10. 性能优化空间
- **并行处理**：不同 Session 的 Pipeline 天然并行
- **Station 并行**：同一 Pipeline 内的 Station 也并行运行
- **零拷贝**：Chunk 只传递引用，不复制数据
- **背压控制**：AsyncIterator 和 Queue 天然支持背压
- **音频格式转换**：Transport 层统一处理，Station 只处理 PCM
## 辅助 Station 示例

### TurnDetectorStation - 检测用户说话结束
```python
class TurnDetectorStation(Station):
    """
    Turn detector: Detects when user finishes speaking.
    
    Input: EVENT_VAD_END
    Output: EVENT_TURN_END (after silence threshold)
    """
    
    def __init__(self, silence_threshold_ms: int = 800):
        super().__init__("TurnDetector")
        self.silence_threshold = silence_threshold_ms / 1000.0  # Convert to seconds
        self._silence_start = None
        self._pending_task = None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Start silence timer when voice ends
            if chunk.type == ChunkType.EVENT_VAD_END:
                self._silence_start = time.time()
                # Wait for silence threshold
                await asyncio.sleep(self.silence_threshold)
                # If still silent (not interrupted), emit TURN_END
                if self._silence_start:
                    yield EventChunk(
                        type=ChunkType.EVENT_TURN_END,
                        event_data={"silence_duration": self.silence_threshold},
                        source=self.name,
                        session_id=chunk.session_id
                    )
                    self._silence_start = None
            
            # Cancel silence timer when voice starts again
            elif chunk.type == ChunkType.EVENT_VAD_START:
                self._silence_start = None
            
            # Reset on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._silence_start = None
            
            return
        
        # Passthrough all data
        yield chunk
```
### TextAggregatorStation - 文本聚合

```python
class TextAggregatorStation(Station):
    """
    Text aggregator: Aggregates TEXT_DELTA into complete TEXT.
    
    Input: TEXT_DELTA (streaming)
    Output: TEXT (complete aggregated text)
    
    Use case: Aggregate ASR streaming output before sending to Agent.
    
    Workflow:
    1. Accumulate TEXT_DELTA chunks
    2. On EVENT_TEXT_COMPLETE: Emit complete text as TEXT
    3. Pass through all other chunks
    """
    
    def __init__(self, name: str = "TextAggregator"):
        super().__init__(name=name)
        self._text_buffer = ""
        self._source = ""  # Remember the source of accumulated text
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Emit complete text when text input is complete
            if chunk.type == ChunkType.EVENT_TEXT_COMPLETE:
                if self._text_buffer.strip():
                    # Emit complete text as TEXT for Agent
                    yield TextChunk(
                        type=ChunkType.TEXT,
                        content=self._text_buffer,
                        source=self._source or "aggregator",
                        session_id=chunk.session_id
                    )
                    
                    # Clear buffer
                    self._text_buffer = ""
                    self._source = ""
            
            # Clear buffer on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._text_buffer = ""
                self._source = ""
            
            # Passthrough signals
            yield chunk
            return
        
        # Accumulate TEXT_DELTA chunks
        if chunk.type == ChunkType.TEXT_DELTA:
            delta = chunk.delta if hasattr(chunk, 'delta') else str(chunk.data or "")
            
            if delta:
                self._text_buffer += delta
                
                # Remember the source of the first chunk
                if not self._source and chunk.source:
                    self._source = chunk.source
            
            # Passthrough TEXT_DELTA
            yield chunk
        else:
            # Passthrough all other chunks
            yield chunk
```

### SentenceSplitterStation - 分句处理
```python
class SentenceSplitterStation(Station):
    """
    Sentence splitter: Splits streaming text into sentences.
    
    Input: TEXT_DELTA (streaming fragments)
    Output: TEXT (complete sentences)
    
    This is useful for TTS - we want to synthesize complete sentences
    rather than tiny fragments.
    """
    
    def __init__(self, sentence_endings: str = ".!?。!?"):
        super().__init__("SentenceSplitter")
        self.endings = set(sentence_endings)
        self._buffer = ""
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Flush buffer on interrupt
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._buffer = ""
            return
        
        # Only process text deltas
        if chunk.type != ChunkType.TEXT_DELTA:
            yield chunk
            return
        
        # Accumulate text
        self._buffer += chunk.delta
        
        # Split by sentence endings
        while any(ending in self._buffer for ending in self.endings):
            for i, char in enumerate(self._buffer):
                if char in self.endings:
                    # Found sentence ending
                    sentence = self._buffer[:i+1].strip()
                    self._buffer = self._buffer[i+1:]
                    
                    if sentence:
                        yield TextChunk(
                            type=ChunkType.TEXT,
                            content=sentence,
                            session_id=chunk.session_id
                        )
                    break
```
### VisionProvider 接口定义
```python
# vixio/providers/vision.py

from abc import ABC, abstractmethod
from typing import List, Dict, Any

class VisionProvider(ABC):
    """
    Vision provider interface for vision/image processing.
    
    Implementations can use various vision models:
    - Qwen-VL
    - GPT-4 Vision
    - Claude Vision
    - Custom CV models
    """
    
    @abstractmethod
    async def detect_objects(self, image_data: bytes) -> List[Dict[str, Any]]:
        """
        Detect objects in image.
        
        Args:
            image_data: Image bytes (JPEG/PNG)
            
        Returns:
            List of detected objects with bounding boxes and labels
            Example: [{"label": "cat", "confidence": 0.95, "bbox": [x, y, w, h]}]
        """
        pass
    
    @abstractmethod
    async def describe_image(self, image_data: bytes) -> str:
        """
        Generate textual description of image.
        
        Args:
            image_data: Image bytes
            
        Returns:
            Text description of the image
        """
        pass
    
    @abstractmethod
    async def answer_question(self, image_data: bytes, question: str) -> str:
        """
        Answer question about an image.
        
        Args:
            image_data: Image bytes
            question: Question about the image
            
        Returns:
            Answer text
        """
        pass
```
### VisionProcessorStation - 视觉处理节点
```python
class VisionProcessorStation(Station):
    """
    Vision processor: Processes vision frames.
    
    Input: VIDEO_FRAME, VIDEO_IMAGE
    Output: Processed vision data or extracted features
    
    This demonstrates how to handle specific data types while
    passing through everything else.
    """
    
    def __init__(self, vision_provider: VisionProvider):
        super().__init__("VisionProcessor")
        self.vision = vision_provider
        self._frame_count = 0
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Handle signals
        if chunk.is_signal():
            # Reset on interrupt
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._frame_count = 0
            return
        
        # Only process vision data
        if chunk.type == ChunkType.VIDEO_FRAME:
            # Process vision frame
            self._frame_count += 1
            
            # Example: Extract objects from frame
            objects = await self.vision.detect_objects(chunk.data)
            
            # Passthrough original frame
            yield chunk
            
            # Yield detection results as metadata
            if objects:
                yield Chunk(
                    type=ChunkType.TEXT,
                    data={
                        "source": "vision_detection",
                        "objects": objects,
                        "frame_number": self._frame_count
                    },
                    metadata={"content_type": "vision_analysis"},
                    session_id=chunk.session_id
                )
        
        elif chunk.type == ChunkType.VIDEO_IMAGE:
            # Process static image
            description = await self.vision.describe_image(chunk.data)
            
            # Passthrough original image
            yield chunk
            
            # Yield description as text
            if description:
                yield TextChunk(
                    type=ChunkType.TEXT,
                    content=f"Image description: {description}",
                    session_id=chunk.session_id
                )
        
        else:
            # Passthrough all non-vision data
            yield chunk
```
### PassthroughStation - 透传节点
```python
class PassthroughStation(Station):
    """
    Passthrough station: Simply passes all chunks through unchanged.
    
    Useful for:
    - Testing pipeline structure
    - Placeholder in pipeline
    - Adding logging without transformation
    """
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Simply passthrough
        yield chunk
```
### FilterStation - 过滤特定 Chunk
```python
class FilterStation(Station):
    """
    Filter station: Only passes through chunks matching criteria.
    
    Useful for debugging or selective processing.
    """
    
    def __init__(self, chunk_types: Set[ChunkType]):
        super().__init__("Filter")
        self.allowed_types = chunk_types
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Signals always passthrough (critical for control flow)
        if chunk.is_signal():
            return
        
        # Filter data chunks
        if chunk.type in self.allowed_types:
            yield chunk
```
### LoggerStation - 日志记录
```python
class LoggerStation(Station):
    """
    Logger station: Logs all chunks passing through.
    
    Useful for debugging pipelines.
    """
    
    def __init__(self, logger_name: str = "pipeline"):
        super().__init__("Logger")
        self.logger = logging.getLogger(logger_name)
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Log chunk
        self.logger.debug(f"[{self.name}] {chunk}")
        
        # Log data size
        if chunk.data:
            if isinstance(chunk.data, bytes):
                self.logger.debug(f"  Data size: {len(chunk.data)} bytes")
            elif isinstance(chunk.data, str):
                self.logger.debug(f"  Data: {chunk.data[:100]}")
        
        # Passthrough everything (no transformation)
        yield chunk
```

## Pipeline 组合模式

### 模式 1：线性流水线
```python
# 最常见：顺序处理
# Note: 音频缓存由 Transport 层处理，Station 只负责业务逻辑
pipeline = Pipeline([
    VADStation(),              # Detect voice activity
    TurnDetectorStation(),     # Detect turn end
    ASRStation(),              # Audio -> Text
    AgentStation(),            # Text -> Response
    SentenceSplitterStation(), # Split sentences for TTS
    TTSStation(),              # Text -> Audio
])
```

### 模式 2：带调试的流水线
```python
# 添加日志节点方便调试
pipeline = Pipeline([
    LoggerStation("input"),
    VADStation(),
    LoggerStation("after_vad"),
    ASRStation(),
    LoggerStation("after_asr"),
    AgentStation(),
    TTSStation(),
    LoggerStation("output"),
])
```

### 模式 3：多模态流水线（音频+视觉）
```python
# 同时处理音频和视觉数据
# 每个 Station 只处理自己关心的数据类型，其他数据透传
pipeline = Pipeline([
    # Audio path
    VADStation(),              # Only processes AUDIO_RAW
    TurnDetectorStation(),     # Only processes VAD events
    ASRStation(),              # Only processes AUDIO_RAW + TURN_END
    
    # Vision path (runs in parallel via passthrough)
    VisionProcessorStation(),   # Only processes VIDEO_FRAME/VIDEO_IMAGE
    
    # Fusion
    AgentStation(),            # Processes TEXT from both ASR and VisionProcessor
    
    # Output
    SentenceSplitterStation(),
    TTSStation(),
])

# 数据流示例：
# 1. AUDIO_RAW -> VAD -> TurnDetector -> ASR -> TEXT ("用户说：这是什么?")
# 2. VIDEO_FRAME -> VisionProcessor -> TEXT ("检测到：猫、沙发")
# 3. 两个 TEXT -> Agent -> 综合回答 -> TTS
```

### 模式 4：条件分支（高级用法）
```python
# 如果需要更复杂的路由逻辑，可以实现 BranchStation
class BranchStation(Station):
    """
    Branch station: Routes chunks to different sub-pipelines based on type.
    
    This is useful when different data types need completely different processing.
    """
    
    def __init__(self, branches: Dict[ChunkType, Pipeline]):
        self.branches = branches
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Signals: broadcast to all branches
        if chunk.is_signal():
            for pipeline in self.branches.values():
                async for out in pipeline.run([chunk]):
                    yield out
            return
        
        # Data: route to specific branch
        if chunk.type in self.branches:
            pipeline = self.branches[chunk.type]
            async for out in pipeline.run([chunk]):
                yield out
        else:
            yield chunk  # Passthrough if no matching branch

# Usage: Different processing paths for different inputs
pipeline = Pipeline([
    BranchStation({
        # Text input path
        ChunkType.TEXT: Pipeline([
            AgentStation(),
            TTSStation(),
        ]),
        
        # Vision input path
        ChunkType.VIDEO_IMAGE: Pipeline([
            VisionProcessorStation(),
            AgentStation(),
            TTSStation(),
        ]),
        
        # Audio input path
        ChunkType.AUDIO_RAW: Pipeline([
            VADStation(),
            ASRStation(),
            AgentStation(),
            TTSStation(),
        ]),
    }),
])
```

## 最佳实践

### 1. Station 设计原则
- **单一职责**：每个 Station 只做一件事
- **无状态优先**：尽量避免跨 Chunk 的状态，除非必要（如 ASR 的 buffer）
- **快速透传 Signal**：Signal Chunk 应该立即 return，避免阻塞
- **优雅处理 INTERRUPT**：所有有状态的 Station 都应处理 INTERRUPT 信号

### 2. Pipeline 设计原则
- **从简单开始**：先用最少的 Station 跑通，再逐步添加
- **避免冗余缓存**：音频缓存由 Transport 层统一处理，Station 不要重复缓存
- **调试时加 Logger**：LoggerStation 非常有用，但生产环境应移除
- **考虑延迟**：每个 Station 都会增加延迟，权衡功能和性能

### 3. Transport 设计原则
- **协议内置**：每个 Transport 实现包含自己的协议逻辑，用户无需关心
- **输入缓存**：在 VAD 检测前缓存音频，检测到语音后才向 Pipeline 输出
- **输出缓存**：缓存输出音频，控制播放节奏，避免overwhelming客户端
- **错误处理**：连接断开应该优雅处理，不影响其他会话
- **资源清理**：使用 try/finally 确保资源释放

### 4. 性能优化
```python
# Transport 层已经处理了音频缓存和批处理
# Pipeline 只需要关注业务逻辑

# 简洁高效的 Pipeline
pipeline = Pipeline([
    VADStation(),           # Transport 已缓存，这里只处理检测
    TurnDetectorStation(),  # 检测静音
    ASRStation(),           # 转录
    AgentStation(),         # 对话
    TTSStation(),           # 合成
])

# 如需优化延迟，减少不必要的 Station
pipeline = Pipeline([
    VADStation(),           # 只保留必要的 VAD
    ASRStation(),           # 直接识别（省略 TurnDetector，在 ASR 内部处理）
    AgentStation(),         
    TTSStation(),           
])
```

### 5. 视觉处理最佳实践
```python
# 视觉处理通常计算密集，需要注意性能

# 策略 1：帧率控制（只处理关键帧）
class VisionProcessorStation(Station):
    def __init__(self, vision_provider, process_every_n_frames=10):
        super().__init__("VisionProcessor")
        self.vision = vision_provider
        self.frame_skip = process_every_n_frames
        self.frame_count = 0
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        if chunk.type == ChunkType.VIDEO_FRAME:
            self.frame_count += 1
            
            # Skip frames to reduce processing load
            if self.frame_count % self.frame_skip != 0:
                yield chunk  # Just passthrough
                return
            
            # Process this frame
            result = await self.vision.detect_objects(chunk.data)
            yield chunk
            # ... yield results

# 策略 2：按需处理（只在收到问题时处理视觉）
class SmartVisionProcessor(Station):
    def __init__(self, vision_provider):
        super().__init__("SmartVisionProcessor")
        self.vision = vision_provider
        self.pending_question = None
        self.latest_frame = None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Store latest vision frame
        if chunk.type == ChunkType.VIDEO_FRAME:
            self.latest_frame = chunk
            yield chunk  # Passthrough
        
        # When user asks question, analyze stored frame
        elif chunk.type == ChunkType.TEXT and "看" in chunk.content or "什么" in chunk.content:
            if self.latest_frame:
                # Now process the vision
                answer = await self.vision.answer_question(
                    self.latest_frame.data,
                    chunk.content
                )
                yield TextChunk(content=f"[视觉分析] {answer}")
            yield chunk  # Also passthrough the question
        
        else:
            yield chunk  # Passthrough everything else

# 策略 3：异步处理（不阻塞主流）
class AsyncVisionProcessor(Station):
    def __init__(self, vision_provider):
        super().__init__("AsyncVisionProcessor")
        self.vision = vision_provider
        self._processing_queue = asyncio.Queue()
        self._result_queue = asyncio.Queue()
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        # Start background processor if not running
        if not hasattr(self, '_processor_task'):
            self._processor_task = asyncio.create_task(self._background_processor())
        
        # Check for results (non-blocking)
        try:
            result = self._result_queue.get_nowait()
            yield result
        except asyncio.QueueEmpty:
            pass
        
        # Queue vision for processing
        if chunk.type == ChunkType.VIDEO_FRAME:
            await self._processing_queue.put(chunk)
        
        # Always passthrough immediately
        yield chunk
    
    async def _background_processor(self):
        """Process vision frames in background"""
        while True:
            frame = await self._processing_queue.get()
            result = await self.vision.detect_objects(frame.data)
            await self._result_queue.put(
                TextChunk(content=f"Objects: {result}")
            )
```

## 关键文件结构
```
vixio/
  # ============ 核心抽象层 ============
  core/                          # 架构核心（最底层抽象）
    __init__.py
    chunk.py                    # Chunk 数据结构（Data/Signal 分类）
    station.py                  # Station 基类（工站抽象）
    pipeline.py                 # Pipeline 实现（异步并行流水线）
    transport.py                # Transport 基类（传输层抽象）
    session.py                  # SessionManager（连接管理）
    control_bus.py              # ControlBus（中心化中断管理）
    timeout_monitor.py          # TimeoutMonitor（超时监控）
  
  # ============ Station 实现层 ============
  stations/                        # 各种 Station 实现
    __init__.py
    
    # Audio processing stations
    vad.py                     # VADStation - Voice Activity Detection
    turn_detector.py           # TurnDetectorStation - Detect turn boundaries
    
    # Text processing stations
    asr.py                     # ASRStation - Speech to Text
    agent.py                   # AgentStation - Agent conversation
    tts.py                     # TTSStation - Text to Speech
    text_aggregator.py         # TextAggregatorStation - Aggregate TEXT_DELTA to TEXT
    sentence_splitter.py       # SentenceSplitterStation - Split sentences (alias: splitter.py)
    splitter.py                # Alias for sentence_splitter.py
    
    # Vision processing stations
    vision.py                  # VisionProcessorStation - Vision frame processing
    
    # Utility stations
    filter.py                  # FilterStation - Filter chunks by type
    logger.py                  # LoggerStation - Debug logging
    passthrough.py             # PassthroughStation - Simple passthrough
    
  # ============ Provider 层（接口 + 实现） ============
  providers/                    # Provider 接口和实现
    __init__.py
    
    # Provider interfaces (抽象基类)
    base.py                    # BaseProvider
    vad.py                     # VADProvider interface
    asr.py                     # ASRProvider interface
    agent.py                   # AgentProvider interface
    tts.py                     # TTSProvider interface
    vision.py                  # VisionProvider interface
    
    # Provider implementations (具体实现)
    silero_vad/                # Silero VAD implementation
      __init__.py
      provider.py
    
    sherpa_onnx_local/         # Sherpa-ONNX local ASR implementation
      __init__.py
      provider.py
    
    openai_agent/              # OpenAI Agent implementation
      __init__.py
      provider.py
    
    edge_tts/                  # Edge TTS implementation
      __init__.py
      provider.py
  
  # ============ Transport 实现层 ============
  # Note: Each transport includes its own protocol logic
  transports/                   # Transport 实现
    __init__.py
    
    xiaozhi/                   # Xiaozhi WebSocket transport (FastAPI-based)
      __init__.py
      transport.py             # XiaozhiTransport (WebSocket + HTTP endpoints)
      protocol.py              # XiaozhiProtocol (message encoding/decoding)
      
    # 注意：实际实现中只有 xiaozhi transport
    # 未来可以添加其他 transport 实现：
    # websocket/               # Standard WebSocket transport
    # http/                    # HTTP REST API transport
  
  # ============ 工具和配置 ============
  utils/                        # 工具函数
    __init__.py
    audio.py                   # Audio utilities
    text.py                    # Text utilities
    
  config/                       # 配置管理
    __init__.py
    loader.py                  # Config loader
    schema.py                  # Config schema
  
  # ============ 示例和测试 ============
  examples/                     # 使用示例
    simple_echo.py             # Example 1: Echo server
    voice_chat.py              # Example 2: Voice conversation
    xiaozhi_server.py          # Example 3: Xiaozhi protocol server
    multimodal_chat.py         # Example 4: Multimodal (audio + vision) chat
    custom_pipeline.py         # Example 5: Custom pipeline
  
  tests/                        # 测试用例（pytest）
    __init__.py
    
    # Unit tests
    test_chunk.py              # Test Chunk classes
    test_stations.py              # Test individual stations
    test_pipeline.py           # Test pipeline
    test_transport.py          # Test transport
    
    # Integration tests
    test_integration.py        # End-to-end tests
    test_xiaozhi.py            # Xiaozhi protocol tests
    
    # Fixtures
    conftest.py                # Pytest fixtures
    fixtures/                  # Test data
      test.wav
      test_config.yaml
```

## 完整示例：构建 Xiaozhi 语音服务器

```python
# examples/xiaozhi_server.py

import asyncio
import logging
from core.chunk import Chunk, ChunkType
from core.pipeline import Pipeline
from core.session import SessionManager
from transports.xiaozhi import XiaozhiTransport  # Includes protocol
from stations.vad import VADStation
from stations.turn_detector import TurnDetectorStation
from stations.asr import ASRStation
from stations.agent import AgentStation
from stations.splitter import SentenceSplitterStation
from stations.tts import TTSStation
from stations.logger import LoggerStation
from providers.silero_vad.provider import SileroVADProvider
from providers.sherpa_onnx_local.provider import SherpaOnnxLocalProvider
from providers.edge_tts.provider import EdgeTTSProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """
    Complete example: Xiaozhi voice conversation server
    
    Pipeline flow:
    1. Client sends audio via WebSocket
    2. VAD detects voice activity
    3. TurnDetector waits for silence
    4. ASR transcribes to text
    5. Agent generates response (streaming)
    6. TTS synthesizes audio (streaming)
    7. Audio sent back to client via WebSocket
    """
    
    # ============ Step 1: Initialize Providers ============
    logger.info("Initializing providers...")
    
    vad_provider = SileroVADProvider(
        threshold=0.5,
        min_speech_duration_ms=250,
    )
    
    asr_provider = SherpaOnnxLocalProvider(
        model_path="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        tokens_path="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt",
        device="cpu",
    )
    
    tts_provider = EdgeTTSProvider(
        voice="zh-CN-XiaoxiaoNeural",
        rate="+0%",
        volume="+0%",
    )
    
    # ============ Step 2: Define Pipeline Factory ============
    def create_pipeline() -> Pipeline:
        """
        Factory function to create a fresh pipeline for each connection.
        
        This ensures each client has isolated state.
        
        Note: Audio buffering is handled by XiaozhiTransport, so pipeline
        only needs business logic stations.
        """
        return Pipeline([
            # Stage 1: Voice detection
            VADStation(vad_provider),                 # Detect voice activity
            TurnDetectorStation(silence_threshold_ms=800),  # Detect turn end
            
            # Stage 2: Speech recognition
            ASRStation(asr_provider),                 # Audio -> Text
            
            # Stage 3: Speech synthesis (Note: Agent removed, direct TTS)
            SentenceSplitterStation(),                # Split into sentences
            TTSStation(tts_provider),                 # Text -> Audio (streaming)
            
            # Optional: Add logger for debugging
            # LoggerStation("output"),
        ], name="XiaozhiVoiceChat")
    
    # ============ Step 3: Setup Transport ============
    # Note: XiaozhiTransport includes protocol logic, no need to configure separately
    logger.info("Setting up Xiaozhi transport...")
    
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000,
    )
    
    # ============ Step 4: Setup Session Manager ============
    logger.info("Setting up session manager...")
    
    manager = SessionManager(
        transport=transport,
        pipeline_factory=create_pipeline,
    )
    
    # ============ Step 5: Start Server ============
    logger.info("Starting Xiaozhi server on ws://0.0.0.0:8000")
    await manager.start()
    
    # ============ Step 6: Run Forever ============
    try:
        # Keep server running
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await manager.stop()
        logger.info("Server stopped")

if __name__ == "__main__":
    asyncio.run(main())
```

### 多模态示例：音频+视觉聊天

```python
# examples/multimodal_chat.py

import asyncio
from core.pipeline import Pipeline
from core.session import SessionManager
from transports.xiaozhi import XiaozhiTransport
from stations.vad import VADStation
from stations.asr import ASRStation
from stations.vision import VisionProcessorStation
from stations.agent import AgentStation
from stations.tts import TTSStation
from providers.silero_vad.provider import SileroVADProvider
from providers.sherpa_onnx_local.provider import SherpaOnnxLocalProvider
from providers.edge_tts.provider import EdgeTTSProvider

async def main():
    """
    Multimodal chat server: Handles both audio and vision inputs.
    
    Use case:
    - User asks "What do you see?" (audio)
    - Camera sends vision frame
    - System analyzes vision and responds via TTS
    
    Pipeline flow:
    1. Audio: VAD -> ASR -> TEXT
    2. Vision: VisionProcessor -> TEXT (with vision analysis)
    3. Both TEXT inputs -> Agent (context aware) -> TTS -> Audio response
    """
    
    # Initialize providers
    vad_provider = SileroVADProvider()
    asr_provider = SherpaOnnxLocalProvider(
        model_path="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    )
    
    tts_provider = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")
    
    # Note: Vision and Agent features removed in this version
    
    # Create multimodal pipeline
    def create_pipeline():
        return Pipeline([
            # Audio processing path
            VADStation(vad_provider),           # Detect voice in audio
            ASRStation(asr_provider),           # Audio -> Text
            
            # Vision processing path (runs in parallel via passthrough)
            VisionProcessorStation(vision_provider),  # Vision -> Text (scene description)
            
            # Fusion: Agent receives text from both audio and vision
            AgentStation(agent_provider),       # Generate contextual response
            
            # Output
            TTSStation(tts_provider),           # Text -> Audio
        ], name="MultimodalChat")
    
    # Setup transport
    transport = XiaozhiTransport(host="0.0.0.0", port=8000)
    
    # Setup session manager
    manager = SessionManager(transport, create_pipeline)
    
    # Start server
    print("🎥 Multimodal chat server started on ws://0.0.0.0:8000")
    print("📹 Supports: Audio input + Vision input")
    print("🔊 Output: Audio response via TTS")
    
    await manager.start()
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### 使用场景说明

**场景 1：看图说话**
```
用户输入（音频）："这是什么？"
用户输入（视觉）：[一张猫的图片]

Pipeline 处理：
1. AUDIO_RAW -> VAD -> ASR -> TEXT("这是什么？")
2. VIDEO_IMAGE -> VisionProcessor -> TEXT("检测到：一只橙色的猫趴在沙发上")
3. Agent 收到两个 TEXT，综合回答 -> TEXT("这是一只可爱的橙色猫咪，它正舒服地趴在沙发上休息呢。")
4. TTS -> AUDIO_ENCODED -> 客户端播放
```

**场景 2：视觉监控问答**
```
用户输入（音频）："现在外面有人吗？"
用户输入（视觉）：[监控画面的视觉帧]

Pipeline 处理：
1. AUDIO_RAW -> ASR -> TEXT("现在外面有人吗？")
2. VIDEO_FRAME -> VisionProcessor -> TEXT("检测到2个人，1辆汽车")
3. Agent -> TEXT("是的，监控画面中检测到两个人和一辆汽车。")
4. TTS -> 语音回答
```

**场景 3：智能家居控制**
```
用户输入（音频）："帮我看看灯开了没有"
用户输入（视觉）：[客厅摄像头画面]

Pipeline 处理：
1. AUDIO -> ASR -> TEXT("帮我看看灯开了没有")
2. VIDEO -> VisionProcessor -> TEXT("场景分析：客厅，灯光状态：关闭")
3. Agent -> TEXT("客厅的灯目前是关闭状态，需要我帮你打开吗？")
4. TTS -> 语音反馈
```

### 配置文件示例

```yaml
# config.yaml

server:
  host: "0.0.0.0"
  port: 8000
  transport: "xiaozhi"  # Transport type (includes protocol)

pipeline:
  # VAD settings
  vad:
    provider: "silero"
    threshold: 0.5
    min_speech_duration_ms: 250
  
  # Turn detection
  turn_detector:
    silence_threshold_ms: 800
  
  # ASR settings
  asr:
    provider: "sherpa_onnx_local"
    model_path: "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    tokens_path: "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt"
    device: "cpu"
    language: "auto"
  
  # TTS settings
  tts:
    provider: "edge_tts"
    voice: "zh-CN-XiaoxiaoNeural"
    rate: "+0%"
    volume: "+0%"

logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

### 使用配置文件

```python
# examples/xiaozhi_server_with_config.py

import asyncio
import yaml
from config.loader import load_config
from core.pipeline import Pipeline
from core.session import SessionManager

async def main():
    # Load configuration
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    
    # Initialize providers from config
    providers = load_config(config)
    
    # Create pipeline from config
    def create_pipeline():
        stations = []
        
        # Add stations based on config
        if config["pipeline"].get("vad"):
            stations.append(VADStation(providers["vad"]))
        
        if config["pipeline"].get("turn_detector"):
            stations.append(TurnDetectorStation(**config["pipeline"]["turn_detector"]))
        
        if config["pipeline"].get("asr"):
            stations.append(ASRStation(providers["asr"]))
        
        if config["pipeline"].get("tts"):
            stations.append(SentenceSplitterStation())
            stations.append(TTSStation(providers["tts"]))
        
        return Pipeline(stations, name="ConfiguredPipeline")
    
    # Setup transport (protocol is built-in)
    transport_type = config["server"]["transport"]
    
    if transport_type == "xiaozhi":
        transport = XiaozhiTransport(
            host=config["server"]["host"],
            port=config["server"]["port"],
        )
    elif transport_type == "websocket":
        transport = StandardWebSocketTransport(
            host=config["server"]["host"],
            port=config["server"]["port"],
        )
    else:
        raise ValueError(f"Unknown transport type: {transport_type}")
    
    # Start server
    manager = SessionManager(transport, create_pipeline)
    await manager.start()
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## 依赖关系图
```
┌─────────────────────────────────────────────────────┐
│                   Application                        │
│              (examples/xiaozhi_server.py)           │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│              SessionManager                          │
│           (core/session.py)                         │
└──────┬──────────────────────────┬───────────────────┘
       │                          │
┌──────▼────────────┐    ┌───────▼─────────────────┐
│   Transport       │    │     Pipeline            │
│ (transports/)     │    │  (core/pipeline.py)     │
│                   │    │                         │
│ ┌───────────────┐ │    │   ┌─────────────┐       │
│ │ Input Buffer  │ │    │   │   Stations     │       │
│ │ (VAD control) │ │    │   │ (stations/)    │       │
│ └───────┬───────┘ │    │   │             │       │
│         │         │    │   │  ┌────────┐ │       │
│ ┌───────▼───────┐ │    │   │  │Provider│ │       │
│ │   Protocol    │ │    │   │  │(plugins)│ │       │
│ │   (built-in)  │ │    │   │  └────────┘ │       │
│ └───────┬───────┘ │    │   └─────────────┘       │
│         │         │    └─────────────────────────┘
│ ┌───────▼───────┐ │              │
│ │ Output Buffer │ │    ┌─────────▼─────────┐
│ │ (playback)    │ │    │   Chunk Stream    │
│ └───────────────┘ │    │  (core/chunk.py)  │
└───────────────────┘    └───────────────────┘

Data Flow:
  Client Audio ──> Input Buffer ──> VAD Detection ──> Pipeline ──> Output Buffer ──> Client
       ↑                                  │                              ↑
       └──────────────────────────────────┴──────────────────────────────┘
              (Buffer before VAD, Passthrough after VAD)
```

## Transport 缓存机制详解
```
Input Buffer (输入缓存):
  ┌─────────────────────────────────────────────┐
  │ Client Audio Stream                         │
  │   ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓                  │
  │ ┌─────────────────────────────────────────┐ │
  │ │ Transport Input Buffer (ring buffer)    │ │
  │ │ [chunk][chunk][chunk][chunk][chunk]     │ │
  │ └──────────────┬──────────────────────────┘ │
  │                │                             │
  │       ┌────────▼────────┐                    │
  │       │ VAD Detection?  │                    │
  │       └────┬───────┬────┘                    │
  │            │       │                          │
  │       No   │       │ Yes                      │
  │     Buffer │       │ Flush + Passthrough      │
  │            │       │                          │
  │            ↓       ↓                          │
  │         [Hold]  [Pipeline Input]             │
  └─────────────────────────────────────────────┘

Output Buffer (输出缓存):
  ┌─────────────────────────────────────────────┐
  │ Pipeline Output Stream                      │
  │   ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓                  │
  │ ┌─────────────────────────────────────────┐ │
  │ │ Transport Output Buffer (queue)         │ │
  │ │ [audio][audio][audio][audio]            │ │
  │ └──────────────┬──────────────────────────┘ │
  │                │                             │
  │       ┌────────▼────────┐                    │
  │       │ Playback Timer  │                    │
  │       │ (20ms interval) │                    │
  │       └────────┬────────┘                    │
  │                ↓                              │
  │         [Client WebSocket]                   │
  │      (Controlled Rate)                       │
  └─────────────────────────────────────────────┘
```


## 实施路线图

### 阶段 0：准备工作（1-2 天）
- [ ] 创建新的 `vixio` 目录结构
- [ ] 设置开发环境和测试框架
- [ ] 编写核心数据结构文档

### 阶段 1：核心基础（3-5 天）
- [ ] 实现 `Chunk` 及其子类 (`core/chunk.py`)
- [ ] 实现 `Station` 基类 (`core/station.py`)
- [ ] 实现 `Pipeline` (`core/pipeline.py`)
- [ ] 编写单元测试
- [ ] 验证基本流式处理逻辑

**验收标准**：能够创建简单的 Pipeline 并传递 Chunks

### 阶段 2：Transport 层（4-6 天）
- [ ] 实现 `TransportBase` 抽象 (`core/transport.py`)
- [ ] 实现 `TransportBufferMixin` (输入/输出缓存)
- [ ] 实现 `XiaozhiTransport` (`transports/xiaozhi/`)（包含协议逻辑）
- [ ] 实现 `StandardWebSocketTransport` (`transports/websocket/`)
- [ ] 编写 Transport 集成测试
- [ ] 验证缓存机制和协议编解码

**验收标准**：
- 能够通过 WebSocket 收发 Chunks
- 输入缓存正常工作（VAD 前缓存，VAD 后透传）
- 输出缓存正常工作（控制播放节奏）

### 阶段 3：基础 Stations（5-7 天）
- [ ] 实现 `VADStation` (使用现有 Silero VAD)
- [ ] 实现 `TurnDetectorStation`
- [ ] 实现 `ASRStation` (使用现有 SenseVoice)
- [ ] 实现 `AgentStation` (使用现有 Agent)
- [ ] 实现 `TTSStation` (使用现有 TTS)
- [ ] 实现 `VisionProcessorStation` (可选，用于多模态支持)
- [ ] 编写 Station 单元测试

**验收标准**：每个 Station 可独立处理对应的 Chunks

**可选**：VisionProcessorStation 用于多模态场景，如果暂时不需要可跳过

### 阶段 4：辅助 Stations（2-3 天）
- [ ] 实现 `SentenceSplitterStation`
- [ ] 实现 `LoggerStation`
- [ ] 实现 `FilterStation`
- [ ] 实现 `PassthroughStation`

**验收标准**：辅助 Stations 能够提升可调试性和灵活性

### 阶段 5：Session 管理（2-3 天）
- [ ] 实现 `SessionManager` (`core/session.py`)
- [ ] 实现连接生命周期管理
- [ ] 实现优雅关闭
- [ ] 编写集成测试

**验收标准**：能够同时处理多个客户端连接

### 阶段 6：端到端测试（3-5 天）
- [ ] 搭建完整的测试 Pipeline
- [ ] 使用真实客户端测试（Xiaozhi 设备或模拟器）
- [ ] 性能测试和优化
- [ ] 压力测试（并发连接数）
- [ ] 延迟测试（TTFB）

**验收标准**：
- 能够稳定处理 100+ 并发连接
- TTFB < 500ms
- 无内存泄漏

### 阶段 7：文档和示例（2-3 天）
- [ ] 编写 API 文档
- [ ] 编写使用指南
- [ ] 编写示例代码 (`examples/`)
- [ ] 编写迁移指南（从旧代码迁移）

### 阶段 8：生产部署（1-2 天）
- [ ] 配置管理优化
- [ ] 日志和监控集成
- [ ] Docker 部署配置
- [ ] 平滑迁移计划

**总计时间**：约 **23-37 天**（3-5 周）

**说明**：
- Transport 层增加了输入/输出缓存机制，时间略有增加
- 但移除了 AudioBundlerStation，Pipeline 更简洁
- 总体工作量相当，但架构更合理

## 风险和缓解措施

### 风险 1：性能不达预期
- **缓解**：阶段 6 提前压测，发现问题及时优化
- **备选方案**：保留旧代码作为回退方案

### 风险 2：与现有系统集成问题
- **缓解**：Protocol 层充分抽象，支持多种协议共存
- **备选方案**：实现适配器模式兼容旧接口

### 风险 3：开发时间超预期
- **缓解**：采用增量开发，每个阶段都有可验收的产物
- **备选方案**：分阶段上线，先上线核心功能

### 风险 4：Bug 和稳定性问题
- **缓解**：充分的单元测试和集成测试（测试覆盖率 > 80%）
- **备选方案**：金丝雀发布，逐步替换旧系统

## 成功指标

### 功能指标
- ✅ 支持 Xiaozhi 协议完整功能
- ✅ 支持流式 ASR/Agent/TTS
- ✅ 支持多客户端并发

### 性能指标
- ✅ TTFB < 500ms
- ✅ 并发连接数 > 100
- ✅ CPU 使用率 < 70%
- ✅ 内存占用 < 2GB

### 质量指标
- ✅ 测试覆盖率 > 80%
- ✅ 代码行数减少 30%+
- ✅ 核心模块圈复杂度 < 10

### 可维护性指标
- ✅ 新增 Station 无需修改核心代码
- ✅ 新增 Protocol 无需修改 Pipeline
- ✅ 单元测试运行时间 < 30s

## 下一步行动

1. **评审设计方案**：团队评审本设计文档，确认技术方向
2. **创建项目分支**：`feature/vixio-refactor`
3. **搭建开发环境**：配置 pytest, mypy, black 等工具
4. **开始阶段 1**：实现 Chunk 和 Station 基础抽象

---

**设计文档版本**: v3.0  
**最后更新**: 2025-11-25  
**状态**: ✅ 设计完成，代码已实现，文档已同步实际实现

**重大更新 (v3.0 - 与实际实现同步)**:
1. ✅ **ControlBus 中心化中断管理**：发布-订阅模式，解耦组件间控制流
2. ✅ **Turn-aware System**：基于 turn_id 的流程控制，自动丢弃旧数据
3. ✅ **异步并行 Pipeline**：每个 Station 在独立任务中运行，通过 Queue 连接
4. ✅ **TimeoutMonitor**：监控 Agent、TTS、整体会话的超时
5. ✅ **Chunk 增强字段**：source, turn_id, sequence
6. ✅ **Station reset_state()**：新 turn 时自动重置状态
7. ✅ **TextAggregatorStation**：聚合 TEXT_DELTA 为完整 TEXT
8. ✅ **XiaozhiTransport 完整实现**：FastAPI + WebSocket + HTTP + 认证 + 音频编解码
9. ✅ **SessionManager 中断处理**：集成 ControlBus，处理客户端和内部中断

**架构核心改进 (v3.0)**:
- 🎯 **中心化控制**：ControlBus 管理所有中断和 turn 转换
- 🔄 **Turn-aware 处理**：Station 自动丢弃旧 turn 数据，无需任务取消
- ⚡ **真正并行**：Pipeline 内所有 Station 并行运行，性能显著提升
- 🛡️ **优雅中断**：清理队列 + turn_id 丢弃，避免任务取消导致的问题
- 📊 **超时保护**：TimeoutMonitor 防止无限等待

**Provider 实现 (v3.0)**:
- ✅ VAD: Silero VAD（本地模型）
- ✅ ASR: Sherpa-ONNX（本地推理）
- ✅ Agent: OpenAI Agent（API 调用）
- ✅ TTS: Edge TTS（微软服务）
- 📦 providers/ 包含：
  - 接口定义：base.py, vad.py, asr.py, tts.py, agent.py, vision.py
  - 具体实现：silero_vad/, sherpa_onnx_local/, openai_agent/, edge_tts/

**Transport 实现 (v3.0)**:
- ✅ XiaozhiTransport：FastAPI-based WebSocket + HTTP 服务器
- ✅ XiaozhiProtocol：消息编解码（独立模块）
- ✅ 音频格式转换：Opus ↔ PCM（Transport 层统一处理）
- ✅ 认证系统：设备白名单 + token 验证
- ✅ OTA 接口：设备配置和更新

**新增事件类型 (v3.0)**:
- 📢 EVENT_TEXT_COMPLETE：文本输入完成（触发聚合）
- 📢 EVENT_AGENT_START/STOP：Agent 处理开始/结束

**文档修正说明**:
本次更新将设计方案文档与实际代码实现完全同步，所有示例代码均基于真实实现。主要修正内容：
1. 补充了 ControlBus 和 Turn-aware 机制的详细说明
2. 更新了 Pipeline 为异步并行模型
3. 添加了 TimeoutMonitor 和 TextAggregatorStation
4. 完善了 SessionManager 的中断处理逻辑
5. 更新了 Chunk 字段和事件类型
6. 修正了文件结构，反映实际目录布局

