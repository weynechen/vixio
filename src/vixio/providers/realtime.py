"""
Base class for Realtime Multimodal Providers.

Defines the interface for realtime voice/multimodal providers (e.g. Qwen Omni, OpenAI Realtime).
Manages WebSocket connection, audio streaming, event handling, and tool usage.

Two usage patterns:
1. Callback-based (legacy): set_event_handler() + _emit_event()
2. AsyncIterator (recommended): stream_response() returns async generator
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Optional, List, Callable, Any, Dict, Union
from enum import Enum
from dataclasses import dataclass

from vixio.providers.base import BaseProvider
from vixio.core.tools.types import ToolDefinition


class RealtimeEventType(Enum):
    """Standardized Realtime Event Types."""
    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    ERROR = "error"
    
    # VAD Events
    SPEECH_START = "speech.start"
    SPEECH_STOP = "speech.stop"
    
    # Content Events
    TEXT_DELTA = "text.delta"           # Streaming text delta (from bot)
    AUDIO_DELTA = "audio.delta"         # Streaming audio delta (from bot)
    TRANSCRIPT_COMPLETE = "transcript.done" # Complete transcript (from user)
    
    # Lifecycle
    RESPONSE_START = "response.start"
    RESPONSE_DONE = "response.done"
    
    # Tool Use
    TOOL_CALL = "tool.call"             # Model requests tool execution
    TOOL_OUTPUT = "tool.output"         # Tool result sent back to model


@dataclass
class RealtimeEvent:
    """Standardized Realtime Event."""
    type: RealtimeEventType
    data: Any = None            # Raw data (audio bytes, etc.)
    text: Optional[str] = None  # Text content
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None


class BaseRealtimeProvider(BaseProvider, ABC):
    """
    Abstract base class for Realtime Providers.
    
    Responsibilities:
    - Manage WebSocket connection lifecycle
    - Stream audio input/output
    - Handle bi-directional events (VAD, Tool calls, etc.)
    - Manage session state (Tools, Instructions)
    """
    
    def __init__(self, name: str):
        super().__init__(name=name)
        self._event_handler: Optional[Callable[[RealtimeEvent], None]] = None

    def set_event_handler(self, handler: Callable[[RealtimeEvent], None]):
        """Set callback to handle events from the provider."""
        self._event_handler = handler

    def _emit_event(self, event: RealtimeEvent):
        """
        Emit an event to the registered handler.
        
        Note: Subclasses can override this to also enqueue events
        for async iterator pattern (stream_response).
        """
        if self._event_handler:
            self._event_handler(event)

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the realtime service."""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the realtime service."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        pass

    @abstractmethod
    async def update_session(self, 
                           instructions: Optional[str] = None, 
                           tools: Optional[List[ToolDefinition]] = None,
                           voice: Optional[str] = None) -> None:
        """
        Update session configuration.
        
        Args:
            instructions: System prompt / instructions
            tools: List of available tools
            voice: Voice ID/name
        """
        pass

    @abstractmethod
    async def send_audio(self, audio_data: bytes) -> None:
        """
        Send audio chunk to the model.
        
        Args:
            audio_data: PCM audio bytes
        """
        pass

    @abstractmethod
    async def send_tool_output(self, tool_call_id: str, output: str) -> None:
        """
        Send tool execution result back to the model.
        
        Args:
            tool_call_id: ID of the tool call
            output: Tool execution result (stringified JSON usually)
        """
        pass

    @abstractmethod
    async def interrupt(self) -> None:
        """Interrupt current generation/response."""
        pass
    
    @abstractmethod
    async def stream_response(self) -> AsyncIterator[RealtimeEvent]:
        """
        Stream response events as async iterator (recommended pattern).
        
        This method should be called after sending audio input.
        It yields events until response is complete (RESPONSE_DONE).
        
        This is the preferred pattern over callback-based event handling,
        as it allows natural async/await flow like AgentStation.
        
        Example:
            await provider.send_audio(audio_chunk)
            async for event in provider.stream_response():
                if event.type == RealtimeEventType.AUDIO_DELTA:
                    yield AudioChunk(data=event.data)
                elif event.type == RealtimeEventType.RESPONSE_DONE:
                    break
        
        Yields:
            RealtimeEvent: Events from the provider
        """
        pass
