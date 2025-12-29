"""
Doubao TTS Bidirectional Provider

Volcengine (火山引擎) bidirectional TTS via WebSocket.
Features:
- Streaming text input (character by character)
- Multiple audio formats (mp3, pcm, ogg_opus)
- Various voice types

Reference: https://www.volcengine.com/docs/6561/1329505

Requires: websockets>=14.0
"""

import asyncio
import io
import json
import struct
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

import websockets
from loguru import logger as base_logger

from vixio.providers.registry import register_provider
from vixio.providers.tts import TTSProvider


# ==============================================================================
# Protocol Implementation (Binary WebSocket Protocol)
# ==============================================================================


class MsgType(IntEnum):
    """Message type enumeration"""

    Invalid = 0
    FullClientRequest = 0b1
    AudioOnlyClient = 0b10
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    FrontEndResultServer = 0b1100
    Error = 0b1111

    # Alias
    ServerACK = AudioOnlyServer

    def __str__(self) -> str:
        return self.name if self.name else f"MsgType({self.value})"


class MsgTypeFlagBits(IntEnum):
    """Message type flag bits"""

    NoSeq = 0  # Non-terminal packet with no sequence
    PositiveSeq = 0b1  # Non-terminal packet with sequence > 0
    LastNoSeq = 0b10  # Last packet with no sequence
    NegativeSeq = 0b11  # Last packet with sequence < 0
    WithEvent = 0b100  # Payload contains event number (int32)


class VersionBits(IntEnum):
    """Version bits"""

    Version1 = 1
    Version2 = 2
    Version3 = 3
    Version4 = 4


class HeaderSizeBits(IntEnum):
    """Header size bits"""

    HeaderSize4 = 1
    HeaderSize8 = 2
    HeaderSize12 = 3
    HeaderSize16 = 4


class SerializationBits(IntEnum):
    """Serialization method bits"""

    Raw = 0
    JSON = 0b1
    Thrift = 0b11
    Custom = 0b1111


class CompressionBits(IntEnum):
    """Compression method bits"""

    None_ = 0
    Gzip = 0b1
    Custom = 0b1111


class EventType(IntEnum):
    """Event type enumeration"""

    None_ = 0  # Default event

    # 1 ~ 49 Upstream Connection events
    StartConnection = 1
    StartTask = 1  # Alias of StartConnection
    FinishConnection = 2
    FinishTask = 2  # Alias of FinishConnection

    # 50 ~ 99 Downstream Connection events
    ConnectionStarted = 50  # Connection established successfully
    TaskStarted = 50  # Alias of ConnectionStarted
    ConnectionFailed = 51  # Connection failed (possibly due to authentication failure)
    TaskFailed = 51  # Alias of ConnectionFailed
    ConnectionFinished = 52  # Connection ended
    TaskFinished = 52  # Alias of ConnectionFinished

    # 100 ~ 149 Upstream Session events
    StartSession = 100
    CancelSession = 101
    FinishSession = 102

    # 150 ~ 199 Downstream Session events
    SessionStarted = 150
    SessionCanceled = 151
    SessionFinished = 152
    SessionFailed = 153
    UsageResponse = 154  # Usage response

    # 200 ~ 249 Upstream general events
    TaskRequest = 200
    UpdateConfig = 201

    # 250 ~ 299 Downstream general events
    AudioMuted = 250

    # 350 ~ 399 Downstream TTS events
    TTSSentenceStart = 350
    TTSSentenceEnd = 351
    TTSResponse = 352
    TTSEnded = 359

    def __str__(self) -> str:
        return self.name if self.name else f"EventType({self.value})"


@dataclass
class Message:
    """
    Binary message format for Volcengine WebSocket protocol.

    Message format:
    0                 1                 2                 3
    | 0 1 2 3 4 5 6 7 | 0 1 2 3 4 5 6 7 | 0 1 2 3 4 5 6 7 | 0 1 2 3 4 5 6 7 |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |    Version      |   Header Size   |     Msg Type    |      Flags      |
    |   (4 bits)      |    (4 bits)     |     (4 bits)    |     (4 bits)    |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    | Serialization   |   Compression   |           Reserved                |
    |   (4 bits)      |    (4 bits)     |           (8 bits)                |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    """

    version: VersionBits = VersionBits.Version1
    header_size: HeaderSizeBits = HeaderSizeBits.HeaderSize4
    type: MsgType = MsgType.Invalid
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NoSeq
    serialization: SerializationBits = SerializationBits.JSON
    compression: CompressionBits = CompressionBits.None_

    event: EventType = EventType.None_
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0

    payload: bytes = field(default_factory=bytes)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        """Create message object from bytes"""
        if len(data) < 3:
            raise ValueError(f"Data too short: expected at least 3 bytes, got {len(data)}")

        type_and_flag = data[1]
        msg_type = MsgType(type_and_flag >> 4)
        flag = MsgTypeFlagBits(type_and_flag & 0b00001111)

        msg = cls(type=msg_type, flag=flag)
        msg.unmarshal(data)
        return msg

    def marshal(self) -> bytes:
        """Serialize message to bytes"""
        buffer = io.BytesIO()

        # Write header
        header = [
            (self.version << 4) | self.header_size,
            (self.type << 4) | self.flag,
            (self.serialization << 4) | self.compression,
        ]

        header_size = 4 * self.header_size
        if padding := header_size - len(header):
            header.extend([0] * padding)

        buffer.write(bytes(header))

        # Write other fields based on type and flag
        if self.flag == MsgTypeFlagBits.WithEvent:
            # Write event
            buffer.write(struct.pack(">i", self.event))
            # Write session_id if applicable
            if self.event not in [
                EventType.StartConnection,
                EventType.FinishConnection,
                EventType.ConnectionStarted,
                EventType.ConnectionFailed,
            ]:
                session_id_bytes = self.session_id.encode("utf-8")
                buffer.write(struct.pack(">I", len(session_id_bytes)))
                if session_id_bytes:
                    buffer.write(session_id_bytes)

        if self.type in [
            MsgType.FullClientRequest,
            MsgType.FullServerResponse,
            MsgType.FrontEndResultServer,
            MsgType.AudioOnlyClient,
            MsgType.AudioOnlyServer,
        ]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                buffer.write(struct.pack(">i", self.sequence))
        elif self.type == MsgType.Error:
            buffer.write(struct.pack(">I", self.error_code))

        # Write payload
        buffer.write(struct.pack(">I", len(self.payload)))
        buffer.write(self.payload)

        return buffer.getvalue()

    def unmarshal(self, data: bytes) -> None:
        """Deserialize message from bytes"""
        buffer = io.BytesIO(data)

        # Read version and header size
        version_and_header_size = buffer.read(1)[0]
        self.version = VersionBits(version_and_header_size >> 4)
        self.header_size = HeaderSizeBits(version_and_header_size & 0b00001111)

        # Skip second byte (already parsed type and flag)
        buffer.read(1)

        # Read serialization and compression methods
        serialization_compression = buffer.read(1)[0]
        self.serialization = SerializationBits(serialization_compression >> 4)
        self.compression = CompressionBits(serialization_compression & 0b00001111)

        # Skip header padding
        header_size = 4 * self.header_size
        read_size = 3
        if padding_size := header_size - read_size:
            buffer.read(padding_size)

        # Read fields based on type
        if self.type in [
            MsgType.FullClientRequest,
            MsgType.FullServerResponse,
            MsgType.FrontEndResultServer,
            MsgType.AudioOnlyClient,
            MsgType.AudioOnlyServer,
        ]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                self.sequence = struct.unpack(">i", buffer.read(4))[0]
        elif self.type == MsgType.Error:
            self.error_code = struct.unpack(">I", buffer.read(4))[0]

        # Read event and session_id if WithEvent flag
        if self.flag == MsgTypeFlagBits.WithEvent:
            self.event = EventType(struct.unpack(">i", buffer.read(4))[0])

            # Read session_id
            if self.event not in [
                EventType.StartConnection,
                EventType.FinishConnection,
                EventType.ConnectionStarted,
                EventType.ConnectionFailed,
                EventType.ConnectionFinished,
            ]:
                size = struct.unpack(">I", buffer.read(4))[0]
                if size > 0:
                    self.session_id = buffer.read(size).decode("utf-8")

            # Read connect_id for connection events
            if self.event in [
                EventType.ConnectionStarted,
                EventType.ConnectionFailed,
                EventType.ConnectionFinished,
            ]:
                size = struct.unpack(">I", buffer.read(4))[0]
                if size > 0:
                    self.connect_id = buffer.read(size).decode("utf-8")

        # Read payload
        size = struct.unpack(">I", buffer.read(4))[0]
        if size > 0:
            self.payload = buffer.read(size)

    def __str__(self) -> str:
        """String representation"""
        if self.type in [MsgType.AudioOnlyServer, MsgType.AudioOnlyClient]:
            return f"MsgType: {self.type}, Event: {self.event}, PayloadSize: {len(self.payload)}"
        elif self.type == MsgType.Error:
            return f"MsgType: {self.type}, ErrorCode: {self.error_code}, Payload: {self.payload.decode('utf-8', 'ignore')}"
        else:
            payload_str = self.payload.decode("utf-8", "ignore")[:100]
            return f"MsgType: {self.type}, Event: {self.event}, Payload: {payload_str}"


# ==============================================================================
# Protocol Helper Functions
# ==============================================================================


async def send_message(
    websocket: websockets.WebSocketClientProtocol,
    msg_type: MsgType,
    event: EventType,
    payload: bytes = b"{}",
    session_id: str = "",
) -> None:
    """Send a protocol message"""
    msg = Message(
        type=msg_type,
        flag=MsgTypeFlagBits.WithEvent,
        event=event,
        session_id=session_id,
        payload=payload,
    )
    await websocket.send(msg.marshal())


async def receive_message(websocket: websockets.WebSocketClientProtocol) -> Message:
    """Receive and parse a protocol message"""
    data = await websocket.recv()
    if isinstance(data, str):
        raise ValueError(f"Unexpected text message: {data}")
    return Message.from_bytes(data)


# ==============================================================================
# TTS Provider Implementation
# ==============================================================================


def get_resource_id(voice_type: str) -> str:
    """
    Get resource ID based on voice type.
    
    MegaTTS voices start with "S_", regular TTS uses a different resource ID.
    """
    if voice_type.startswith("S_"):
        return "volc.megatts.default"
    return "volc.service_type.10029"


@register_provider("doubao-tts-bidirectional")
class DoubaoTTSBidirectionalProvider(TTSProvider):
    """
    Doubao TTS Bidirectional Provider.

    Uses Volcengine's bidirectional WebSocket protocol for TTS.
    Supports streaming text input (character by character).

    Features:
    - Streaming text input with 5ms character delay
    - Multiple audio formats (mp3, pcm, ogg_opus)
    - Various voice types (MegaTTS and standard)
    - Session-based synthesis

    Reference: https://www.volcengine.com/docs/6561/1329505
    """

    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service."""
        return False

    @property
    def is_stateful(self) -> bool:
        """This provider maintains WebSocket connection state."""
        return True

    @property
    def category(self) -> str:
        """Provider category."""
        return "tts"

    @property
    def supports_streaming_input(self) -> bool:
        """This provider supports streaming text input."""
        return True

    @classmethod
    def get_config_schema(cls):
        """Return configuration schema."""
        return {
            "app_id": {
                "type": "string",
                "required": True,
                "description": "Volcengine App ID (X-Api-App-Key)",
            },
            "access_token": {
                "type": "string",
                "required": True,
                "description": "Volcengine Access Token (X-Api-Access-Key)",
            },
            "voice_type": {
                "type": "string",
                "default": "zh_female_tianmei",
                "description": "Voice type (e.g., zh_female_tianmei, S_xxx for MegaTTS)",
            },
            "resource_id": {
                "type": "string",
                "default": "",
                "description": "Resource ID (auto-detected if empty)",
            },
            "encoding": {
                "type": "string",
                "default": "mp3",
                "description": "Audio format (mp3, pcm, ogg_opus)",
            },
            "sample_rate": {
                "type": "int",
                "default": 24000,
                "description": "Output sample rate (8000, 16000, 24000, 48000)",
            },
            "enable_timestamp": {
                "type": "bool",
                "default": False,
                "description": "Enable word timestamp in response",
            },
            "endpoint": {
                "type": "string",
                "default": "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
                "description": "WebSocket endpoint URL",
            },
            "char_delay_ms": {
                "type": "int",
                "default": 5,
                "description": "Delay between characters in streaming mode (ms)",
            },
        }

    def __init__(
        self,
        app_id: str,
        access_token: str,
        voice_type: str = "zh_female_tianmei",
        resource_id: str = "",
        encoding: str = "mp3",
        sample_rate: int = 24000,
        enable_timestamp: bool = False,
        endpoint: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        char_delay_ms: int = 5,
    ):
        """
        Initialize Doubao TTS provider.

        Args:
            app_id: Volcengine App ID
            access_token: Volcengine Access Token
            voice_type: Voice type (e.g., zh_female_tianmei)
            resource_id: Resource ID (auto-detected if empty)
            encoding: Audio format (mp3, pcm, ogg_opus)
            sample_rate: Output sample rate
            enable_timestamp: Enable word timestamp
            endpoint: WebSocket endpoint URL
            char_delay_ms: Delay between characters in streaming mode
        """
        super().__init__(name="doubao-tts-bidirectional")

        self.app_id = app_id
        self.access_token = access_token
        self.voice_type = voice_type
        self.resource_id = resource_id or get_resource_id(voice_type)
        self.encoding = encoding
        self._sample_rate = sample_rate
        self.enable_timestamp = enable_timestamp
        self.endpoint = endpoint
        self.char_delay_ms = char_delay_ms

        # Connection state
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._is_connected = False
        self._connect_id: str = ""

        # Audio queue for receiving audio data
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._current_session_id: str = ""
        self._session_finished = asyncio.Event()
        self._cancel_requested = False

        # Receive task for background audio reception
        self._receive_task: Optional[asyncio.Task] = None
        
        # Streaming session state (for append_text_stream / finish_stream)
        self._streaming_session_active = False
        self._streaming_session_id: str = ""

        self.logger = base_logger.bind(component=self.name)
        self.logger.info(
            f"Initialized Doubao TTS provider (voice={voice_type}, encoding={encoding})"
        )

    async def initialize(self) -> None:
        """Initialize provider and establish connection."""
        await self._connect()

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._is_connected:
            return

        self._connect_id = str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": self._connect_id,
        }

        self.logger.info(f"Connecting to {self.endpoint}")

        try:
            self._websocket = await websockets.connect(
                self.endpoint,
                additional_headers=headers,
                max_size=10 * 1024 * 1024,  # 10MB max message size
            )

            # Log response headers
            if hasattr(self._websocket, "response") and self._websocket.response:
                logid = self._websocket.response.headers.get("x-tt-logid", "unknown")
                self.logger.info(f"Connected, logid: {logid}")

            # Send StartConnection
            await send_message(
                self._websocket, MsgType.FullClientRequest, EventType.StartConnection
            )

            # Wait for ConnectionStarted
            msg = await receive_message(self._websocket)
            if msg.type != MsgType.FullServerResponse or msg.event != EventType.ConnectionStarted:
                raise RuntimeError(f"Unexpected response: {msg}")

            self._is_connected = True
            self.logger.info("TTS WebSocket connection established")

        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            self._websocket = None
            raise

    async def _ensure_connected(self) -> None:
        """Ensure WebSocket is connected, reconnect if needed."""
        if not self._is_connected or self._websocket is None:
            await self._connect()

    def _build_session_request(self) -> dict:
        """Build session request payload."""
        return {
            "user": {
                "uid": str(uuid.uuid4()),
            },
            "namespace": "BidirectionalTTS",
            "req_params": {
                "speaker": self.voice_type,
                "audio_params": {
                    "format": self.encoding,
                    "sample_rate": self._sample_rate,
                    "enable_timestamp": self.enable_timestamp,
                },
            },
        }

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize complete text to audio.

        Args:
            text: Complete text to synthesize

        Yields:
            Audio bytes in configured format
        """
        await self._ensure_connected()

        # Start session
        session_id = str(uuid.uuid4())
        self._current_session_id = session_id
        self._session_finished.clear()
        self._cancel_requested = False

        # Clear audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Build and send StartSession request
        base_request = self._build_session_request()
        base_request["event"] = EventType.StartSession

        await send_message(
            self._websocket,
            MsgType.FullClientRequest,
            EventType.StartSession,
            json.dumps(base_request).encode(),
            session_id,
        )

        # Wait for SessionStarted
        msg = await receive_message(self._websocket)
        if msg.type != MsgType.FullServerResponse or msg.event != EventType.SessionStarted:
            raise RuntimeError(f"Session start failed: {msg}")

        self.logger.debug(f"Session started: {session_id}")

        # Send text character by character (for better streaming)
        async def send_text():
            for char in text:
                if self._cancel_requested:
                    break

                synthesis_request = self._build_session_request()
                synthesis_request["event"] = EventType.TaskRequest
                synthesis_request["req_params"]["text"] = char

                await send_message(
                    self._websocket,
                    MsgType.FullClientRequest,
                    EventType.TaskRequest,
                    json.dumps(synthesis_request).encode(),
                    session_id,
                )
                await asyncio.sleep(self.char_delay_ms / 1000.0)

            # Finish session
            await send_message(
                self._websocket,
                MsgType.FullClientRequest,
                EventType.FinishSession,
                b"{}",
                session_id,
            )

        # Start sending in background
        send_task = asyncio.create_task(send_text())

        # Receive audio data
        try:
            while not self._cancel_requested:
                try:
                    msg = await asyncio.wait_for(
                        receive_message(self._websocket), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("Receive timeout")
                    break

                if msg.type == MsgType.FullServerResponse:
                    if msg.event == EventType.SessionFinished:
                        self.logger.debug(f"Session finished: {session_id}")
                        break
                    elif msg.event == EventType.SessionFailed:
                        raise RuntimeError(f"Session failed: {msg.payload.decode()}")
                elif msg.type == MsgType.AudioOnlyServer:
                    if msg.payload:
                        yield msg.payload
                elif msg.type == MsgType.Error:
                    raise RuntimeError(f"TTS error: {msg}")

        finally:
            await send_task
            self._session_finished.set()

    async def synthesize_stream(self, text_stream: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """
        Synthesize streaming text to audio.

        This method supports streaming text input (e.g., from LLM output).
        Text chunks are sent character by character with configurable delay.

        Args:
            text_stream: Streaming text chunks

        Yields:
            Audio bytes as synthesis progresses
        """
        await self._ensure_connected()

        # Start session
        session_id = str(uuid.uuid4())
        self._current_session_id = session_id
        self._session_finished.clear()
        self._cancel_requested = False

        # Clear audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Build and send StartSession request
        base_request = self._build_session_request()
        base_request["event"] = EventType.StartSession

        await send_message(
            self._websocket,
            MsgType.FullClientRequest,
            EventType.StartSession,
            json.dumps(base_request).encode(),
            session_id,
        )

        # Wait for SessionStarted
        msg = await receive_message(self._websocket)
        if msg.type != MsgType.FullServerResponse or msg.event != EventType.SessionStarted:
            raise RuntimeError(f"Session start failed: {msg}")

        self.logger.debug(f"Streaming session started: {session_id}")

        # Send text stream character by character
        async def send_stream():
            async for text_chunk in text_stream:
                if self._cancel_requested:
                    break

                for char in text_chunk:
                    if self._cancel_requested:
                        break

                    synthesis_request = self._build_session_request()
                    synthesis_request["event"] = EventType.TaskRequest
                    synthesis_request["req_params"]["text"] = char

                    await send_message(
                        self._websocket,
                        MsgType.FullClientRequest,
                        EventType.TaskRequest,
                        json.dumps(synthesis_request).encode(),
                        session_id,
                    )
                    await asyncio.sleep(self.char_delay_ms / 1000.0)

            # Finish session
            await send_message(
                self._websocket,
                MsgType.FullClientRequest,
                EventType.FinishSession,
                b"{}",
                session_id,
            )

        # Start sending in background
        send_task = asyncio.create_task(send_stream())

        # Receive audio data
        try:
            while not self._cancel_requested:
                try:
                    msg = await asyncio.wait_for(
                        receive_message(self._websocket), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("Receive timeout")
                    break

                if msg.type == MsgType.FullServerResponse:
                    if msg.event == EventType.SessionFinished:
                        self.logger.debug(f"Session finished: {session_id}")
                        break
                    elif msg.event == EventType.SessionFailed:
                        raise RuntimeError(f"Session failed: {msg.payload.decode()}")
                elif msg.type == MsgType.AudioOnlyServer:
                    if msg.payload:
                        yield msg.payload
                elif msg.type == MsgType.Error:
                    raise RuntimeError(f"TTS error: {msg}")

        finally:
            await send_task
            self._session_finished.set()

    async def _start_streaming_session(self) -> None:
        """
        Start a new streaming session and background receive task.
        
        Called on first append_text_stream() call.
        """
        await self._ensure_connected()
        
        # Generate new session ID
        self._streaming_session_id = str(uuid.uuid4())
        self._session_finished.clear()
        self._cancel_requested = False
        
        # Clear audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        # Build and send StartSession request
        base_request = self._build_session_request()
        base_request["event"] = EventType.StartSession
        
        await send_message(
            self._websocket,
            MsgType.FullClientRequest,
            EventType.StartSession,
            json.dumps(base_request).encode(),
            self._streaming_session_id,
        )
        
        # Wait for SessionStarted
        msg = await receive_message(self._websocket)
        if msg.type != MsgType.FullServerResponse or msg.event != EventType.SessionStarted:
            raise RuntimeError(f"Session start failed: {msg}")
        
        self.logger.debug(f"Streaming session started: {self._streaming_session_id}")
        
        # Start background receive task
        self._receive_task = asyncio.create_task(self._receive_audio_loop())
        self._streaming_session_active = True
    
    async def _receive_audio_loop(self) -> None:
        """
        Background task to receive audio data and put into queue.
        
        Runs until session is finished or cancelled.
        """
        try:
            while not self._cancel_requested:
                try:
                    msg = await asyncio.wait_for(
                        receive_message(self._websocket), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("Receive timeout in background task")
                    continue
                
                if msg.type == MsgType.FullServerResponse:
                    if msg.event == EventType.SessionFinished:
                        self.logger.debug(f"Session finished: {self._streaming_session_id}")
                        self._session_finished.set()
                        break
                    elif msg.event == EventType.SessionFailed:
                        self.logger.error(f"Session failed: {msg.payload.decode()}")
                        self._session_finished.set()
                        break
                elif msg.type == MsgType.AudioOnlyServer:
                    if msg.payload:
                        await self._audio_queue.put(msg.payload)
                elif msg.type == MsgType.Error:
                    self.logger.error(f"TTS error: {msg}")
                    self._session_finished.set()
                    break
        except asyncio.CancelledError:
            self.logger.debug("Receive task cancelled")
        except Exception as e:
            self.logger.error(f"Error in receive loop: {e}")
            self._session_finished.set()
    
    async def append_text_stream(self, text_chunk: str) -> AsyncIterator[bytes]:
        """
        Append text chunk to streaming TTS session.

        This method is called repeatedly for each TEXT_DELTA from StreamingTTSStation.
        Maintains a persistent session for smooth audio output.

        Args:
            text_chunk: Text delta to append

        Yields:
            Audio bytes as they become available
        """
        # Start session on first call
        if not self._streaming_session_active:
            await self._start_streaming_session()
        
        # Send text character by character (for better streaming)
        for char in text_chunk:
            if self._cancel_requested:
                break
            
            synthesis_request = self._build_session_request()
            synthesis_request["event"] = EventType.TaskRequest
            synthesis_request["req_params"]["text"] = char
            
            await send_message(
                self._websocket,
                MsgType.FullClientRequest,
                EventType.TaskRequest,
                json.dumps(synthesis_request).encode(),
                self._streaming_session_id,
            )
            await asyncio.sleep(self.char_delay_ms / 1000.0)
        
        # Yield any audio that has arrived (non-blocking drain)
        while not self._audio_queue.empty():
            try:
                audio = self._audio_queue.get_nowait()
                yield audio
            except asyncio.QueueEmpty:
                break

    async def finish_stream(self) -> AsyncIterator[bytes]:
        """
        Finish streaming TTS session and flush remaining audio.

        Called by StreamingTTSStation when text stream is complete.

        Yields:
            Remaining audio bytes
        """
        if not self._streaming_session_active:
            return
        
        self.logger.info("Finishing TTS streaming session")
        
        # Send FinishSession
        await send_message(
            self._websocket,
            MsgType.FullClientRequest,
            EventType.FinishSession,
            b"{}",
            self._streaming_session_id,
        )
        
        # Drain remaining audio from queue until session finishes
        while not self._session_finished.is_set():
            try:
                audio = await asyncio.wait_for(
                    self._audio_queue.get(),
                    timeout=0.1
                )
                yield audio
            except asyncio.TimeoutError:
                continue
        
        # Drain any remaining audio in queue
        while not self._audio_queue.empty():
            try:
                audio = self._audio_queue.get_nowait()
                yield audio
            except asyncio.QueueEmpty:
                break
        
        # Cleanup streaming session state
        self._streaming_session_active = False
        self._streaming_session_id = ""
        
        # Cancel receive task if still running
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        
        self.logger.debug("Streaming session finished")

    async def reset_state(self) -> None:
        """
        Reset provider state for new turn.

        Clears audio queue, cancels streaming session, and resets state.
        CRITICAL: Must be called when turn changes.
        """
        self._cancel_requested = True
        
        # Cancel streaming session if active
        if self._streaming_session_active:
            self.logger.info("Cancelling active streaming session on reset")
            
            # Try to send CancelSession (best effort)
            if self._websocket and self._is_connected and self._streaming_session_id:
                try:
                    await send_message(
                        self._websocket,
                        MsgType.FullClientRequest,
                        EventType.CancelSession,
                        b"{}",
                        self._streaming_session_id,
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to send CancelSession: {e}")
            
            self._streaming_session_active = False
            self._streaming_session_id = ""
        
        # Cancel receive task
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        # Clear audio queue
        cleared_count = 0
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                cleared_count += 1
            except asyncio.QueueEmpty:
                break
        
        # Reset completion event
        was_complete = self._session_finished.is_set()
        self._session_finished.clear()

        self._cancel_requested = False
        self.logger.info(
            f"TTS provider state reset: cleared {cleared_count} audio chunks, "
            f"completion_was_set={was_complete}"
        )

    def cancel(self) -> None:
        """Cancel ongoing synthesis."""
        self._cancel_requested = True
        self.logger.debug("TTS synthesis cancelled")

    async def cleanup(self) -> None:
        """Cleanup resources and close connection."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._websocket and self._is_connected:
            try:
                # Send FinishConnection
                await send_message(
                    self._websocket, MsgType.FullClientRequest, EventType.FinishConnection
                )

                # Wait for ConnectionFinished
                try:
                    msg = await asyncio.wait_for(receive_message(self._websocket), timeout=5.0)
                    if msg.event != EventType.ConnectionFinished:
                        self.logger.warning(f"Unexpected close response: {msg}")
                except asyncio.TimeoutError:
                    self.logger.warning("Connection close timeout")

                await self._websocket.close()
            except Exception as e:
                self.logger.warning(f"Error closing connection: {e}")

        self._websocket = None
        self._is_connected = False
        self.logger.info("TTS provider cleaned up")
