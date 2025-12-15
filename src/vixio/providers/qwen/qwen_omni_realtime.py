"""
Qwen Omni Realtime Provider

Real-time voice conversation using Alibaba Cloud's Qwen-Omni-Realtime model
via DashScope WebSocket API.

Reference: https://help.aliyun.com/zh/model-studio/realtime

Features:
- Integrated VAD + ASR + LLM + TTS in one model
- Server-side VAD for automatic turn detection
- Streaming audio input and output
- Support for interruption handling

Requires: pip install dashscope>=1.23.9
"""

import asyncio
import base64
import os
import queue
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from loguru import logger as base_logger

from vixio.providers.base import BaseProvider
from vixio.providers.registry import register_provider


@dataclass
class RealtimeEvent:
    """Event from realtime model."""
    event_type: str
    data: Any = None


@register_provider("qwen-omni-realtime")
class QwenOmniRealtimeProvider(BaseProvider):
    """
    Qwen Omni Realtime Provider for end-to-end voice conversation.
    
    This provider integrates VAD + ASR + LLM + TTS in a single model,
    enabling natural real-time voice conversations with automatic
    turn detection and interruption handling.
    
    Features:
    - Server-side VAD for automatic speech detection
    - Streaming audio input and output
    - Automatic turn management
    - Interruption handling
    
    Requirements:
        pip install dashscope>=1.23.9
    """
    
    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service."""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """Realtime model is stateful - maintains conversation state."""
        return True
    
    @property
    def category(self) -> str:
        """Provider category."""
        return "realtime"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen3-omni-flash-realtime",
        voice: str = "Cherry",
        instructions: str = "你是一个友好的AI助手，请用简洁的语言回答问题。",
        base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        vad_threshold: float = 0.5,
        silence_duration_ms: int = 800,
    ):
        """
        Initialize Qwen Omni Realtime provider.
        
        Args:
            api_key: DashScope API key. If None, uses DASHSCOPE_API_KEY env var.
            model: Model name (default: qwen3-omni-flash-realtime)
            voice: Voice name for TTS output (Cherry, Ethan, etc.)
            instructions: System prompt for the model
            base_url: WebSocket API URL
            input_sample_rate: Input audio sample rate (default: 16000)
            output_sample_rate: Output audio sample rate (default: 24000)
            vad_threshold: VAD detection threshold (0.0-1.0)
            silence_duration_ms: Silence duration to trigger response (ms)
        """
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DashScope API key is required. "
                "Set DASHSCOPE_API_KEY environment variable or pass api_key parameter."
            )
        
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self.base_url = base_url
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.vad_threshold = vad_threshold
        self.silence_duration_ms = silence_duration_ms
        
        # Connection state
        self._conversation = None
        self._callback = None
        self._is_connected = False
        self._connection_lock = threading.Lock()
        
        # Output queues
        self._audio_queue: queue.Queue = queue.Queue()
        self._text_queue: queue.Queue = queue.Queue()
        self._event_queue: queue.Queue = queue.Queue()
        
        # State tracking
        self._is_responding = False
        self._current_error: Optional[Exception] = None
        self._session_closed = threading.Event()
        
        # Event callbacks
        self._on_speech_started: Optional[Callable] = None
        self._on_speech_stopped: Optional[Callable] = None
        self._on_response_started: Optional[Callable] = None
        self._on_response_done: Optional[Callable] = None
        
        self.logger = base_logger.bind(component=self.name)
        self.logger.info(
            f"Initialized Qwen Omni Realtime provider "
            f"(model={model}, voice={voice})"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema."""
        return {
            "api_key": {
                "type": "string",
                "default": None,
                "description": "DashScope API key. Uses DASHSCOPE_API_KEY env var if not provided."
            },
            "model": {
                "type": "string",
                "default": "qwen3-omni-flash-realtime",
                "description": "Model name for realtime conversation"
            },
            "voice": {
                "type": "string",
                "default": "Cherry",
                "description": "Voice name for TTS output (Cherry, Ethan, Serena, etc.)"
            },
            "instructions": {
                "type": "string",
                "default": "你是一个友好的AI助手，请用简洁的语言回答问题。",
                "description": "System prompt for the model"
            },
            "base_url": {
                "type": "string",
                "default": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "description": "WebSocket API URL"
            },
            "input_sample_rate": {
                "type": "int",
                "default": 16000,
                "description": "Input audio sample rate in Hz"
            },
            "output_sample_rate": {
                "type": "int",
                "default": 24000,
                "description": "Output audio sample rate in Hz"
            },
            "vad_threshold": {
                "type": "float",
                "default": 0.5,
                "description": "VAD detection threshold (0.0-1.0)"
            },
            "silence_duration_ms": {
                "type": "int",
                "default": 800,
                "description": "Silence duration to trigger response (ms)"
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider and establish WebSocket connection.
        
        Connection is kept alive for the entire session lifetime.
        """
        try:
            import dashscope
            dashscope.api_key = self.api_key
        except ImportError as e:
            raise ImportError(
                "dashscope not installed. "
                "Install with: pip install dashscope>=1.23.9"
            ) from e
        
        # Establish connection
        await self._connect()
        self.logger.info("Qwen Omni Realtime provider initialized with persistent connection")
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._is_connected:
            return
        
        try:
            from dashscope.audio.qwen_omni import (
                OmniRealtimeConversation,
                OmniRealtimeCallback,
                MultiModality,
                AudioFormat,
            )
        except ImportError as e:
            raise ImportError(
                "dashscope.audio.qwen_omni not available. "
                "Please upgrade dashscope: pip install dashscope>=1.23.9"
            ) from e
        
        # Create callback instance
        self._callback = self._create_callback()
        
        # Create conversation
        self._conversation = OmniRealtimeConversation(
            model=self.model,
            url=self.base_url,
            callback=self._callback
        )
        
        # Connect in a thread to avoid blocking
        def connect_sync():
            try:
                self._conversation.connect()
                
                # Configure session for realtime voice conversation (VAD mode)
                self._conversation.update_session(
                    output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
                    voice=self.voice,
                    input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                    output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                    enable_input_audio_transcription=True,
                    input_audio_transcription_model="gummy-realtime-v1",
                    # Enable server-side VAD
                    enable_turn_detection=True,
                    turn_detection_threshold=self.vad_threshold,
                    turn_detection_silence_duration_ms=self.silence_duration_ms,
                    instructions=self.instructions,
                )
                
                with self._connection_lock:
                    self._is_connected = True
                self.logger.info("Realtime WebSocket connection established")
            except Exception as e:
                self.logger.error(f"Failed to establish connection: {e}")
                raise
        
        # Run connection in thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, connect_sync)
    
    def _create_callback(self):
        """Create callback instance for handling WebSocket events."""
        from dashscope.audio.qwen_omni import OmniRealtimeCallback
        
        provider = self  # Capture reference
        
        class RealtimeCallback(OmniRealtimeCallback):
            def on_open(self):
                provider.logger.debug("Realtime WebSocket connection opened")
            
            def on_close(self, close_status_code, close_msg):
                provider.logger.info(f"Realtime WebSocket closed: {close_status_code}, {close_msg}")
                with provider._connection_lock:
                    provider._is_connected = False
                provider._session_closed.set()
            
            def on_event(self, event):
                try:
                    event_type = event.get("type", "")
                    
                    if event_type == "session.created":
                        session_id = event.get("session", {}).get("id", "")
                        provider.logger.debug(f"Session created: {session_id}")
                    
                    elif event_type == "session.updated":
                        provider.logger.debug("Session updated")
                    
                    elif event_type == "input_audio_buffer.speech_started":
                        provider.logger.info("Speech started detected")
                        provider._event_queue.put(RealtimeEvent("speech_started"))
                        if provider._on_speech_started:
                            provider._on_speech_started()
                    
                    elif event_type == "input_audio_buffer.speech_stopped":
                        provider.logger.info("Speech stopped detected")
                        provider._event_queue.put(RealtimeEvent("speech_stopped"))
                        if provider._on_speech_stopped:
                            provider._on_speech_stopped()
                    
                    elif event_type == "input_audio_buffer.committed":
                        provider.logger.debug("Audio buffer committed")
                    
                    elif event_type == "response.created":
                        provider._is_responding = True
                        provider._event_queue.put(RealtimeEvent("response_started"))
                        if provider._on_response_started:
                            provider._on_response_started()
                    
                    elif event_type == "response.audio.delta":
                        # Output audio data
                        audio_b64 = event.get("delta", "")
                        if audio_b64:
                            audio_data = base64.b64decode(audio_b64)
                            provider._audio_queue.put(audio_data)
                    
                    elif event_type == "response.audio_transcript.delta":
                        # Output text (transcription of model response)
                        text_delta = event.get("delta", "")
                        if text_delta:
                            provider._text_queue.put(("delta", text_delta))
                    
                    elif event_type == "response.audio_transcript.done":
                        # Complete transcription
                        transcript = event.get("transcript", "")
                        if transcript:
                            provider._text_queue.put(("complete", transcript))
                            provider.logger.info(f"Model response: {transcript}")
                    
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        # User's speech transcription
                        transcript = event.get("transcript", "")
                        if transcript:
                            provider._event_queue.put(RealtimeEvent("user_transcript", transcript))
                            provider.logger.info(f"User said: {transcript}")
                    
                    elif event_type == "response.done":
                        provider._is_responding = False
                        provider._event_queue.put(RealtimeEvent("response_done"))
                        if provider._on_response_done:
                            provider._on_response_done()
                        provider.logger.debug("Response complete")
                    
                    elif event_type == "error":
                        error_msg = event.get("error", {}).get("message", "Unknown error")
                        provider._current_error = Exception(f"Realtime error: {error_msg}")
                        provider.logger.error(f"Realtime error: {error_msg}")
                        provider._event_queue.put(RealtimeEvent("error", error_msg))
                    
                except Exception as e:
                    provider.logger.error(f"Error processing event: {e}")
                    provider._current_error = e
            
            def on_error(self, error):
                provider.logger.error(f"Realtime WebSocket error: {error}")
                provider._current_error = Exception(str(error))
                with provider._connection_lock:
                    provider._is_connected = False
        
        return RealtimeCallback()
    
    async def cleanup(self) -> None:
        """Cleanup provider resources and close connection."""
        if self._conversation:
            try:
                # Close in thread to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._conversation.close)
            except Exception as e:
                self.logger.warning(f"Error closing connection: {e}")
        
        self._conversation = None
        self._callback = None
        self._is_connected = False
        self.logger.info("Qwen Omni Realtime provider cleaned up")
    
    def send_audio(self, audio_data: bytes) -> None:
        """
        Send audio data to the realtime model.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
        """
        if not self._is_connected or not self._conversation:
            self.logger.warning("Cannot send audio: not connected")
            return
        
        try:
            audio_b64 = base64.b64encode(audio_data).decode('ascii')
            self._conversation.append_audio(audio_b64)
        except Exception as e:
            self.logger.error(f"Error sending audio: {e}")
    
    async def send_audio_async(self, audio_data: bytes) -> None:
        """
        Send audio data asynchronously.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.send_audio, audio_data)
    
    def get_audio(self, timeout: float = 0.1) -> Optional[bytes]:
        """
        Get output audio data from queue.
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            Audio bytes or None if queue is empty
        """
        try:
            return self._audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    async def get_audio_async(self, timeout: float = 0.1) -> Optional[bytes]:
        """
        Get output audio data asynchronously.
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            Audio bytes or None if queue is empty
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_audio, timeout)
    
    def get_text(self, timeout: float = 0.1) -> Optional[Tuple[str, str]]:
        """
        Get output text from queue.
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (type, text) where type is "delta" or "complete",
            or None if queue is empty
        """
        try:
            return self._text_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_event(self, timeout: float = 0.1) -> Optional[RealtimeEvent]:
        """
        Get event from queue.
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            RealtimeEvent or None if queue is empty
        """
        try:
            return self._event_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    async def stream_audio_output(self) -> AsyncIterator[bytes]:
        """
        Stream audio output from the model.
        
        Yields:
            Audio bytes (24kHz, mono, 16-bit PCM)
        """
        while self._is_connected:
            audio = await self.get_audio_async(timeout=0.05)
            if audio:
                yield audio
            elif not self._is_responding and self._audio_queue.empty():
                # No more audio expected
                break
            await asyncio.sleep(0.01)
    
    def cancel_response(self) -> None:
        """Cancel the current response (for interruption handling)."""
        if self._conversation and self._is_responding:
            try:
                # Clear audio queue
                while not self._audio_queue.empty():
                    try:
                        self._audio_queue.get_nowait()
                    except queue.Empty:
                        break
                
                # Note: The DashScope SDK may not have a direct cancel method
                # In VAD mode, user speaking will automatically cancel the response
                self.logger.info("Response cancelled")
            except Exception as e:
                self.logger.warning(f"Error cancelling response: {e}")
    
    def set_callbacks(
        self,
        on_speech_started: Optional[Callable] = None,
        on_speech_stopped: Optional[Callable] = None,
        on_response_started: Optional[Callable] = None,
        on_response_done: Optional[Callable] = None,
    ) -> None:
        """
        Set event callbacks.
        
        Args:
            on_speech_started: Called when user starts speaking
            on_speech_stopped: Called when user stops speaking
            on_response_started: Called when model starts responding
            on_response_done: Called when model finishes responding
        """
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_response_started = on_response_started
        self._on_response_done = on_response_done
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._is_connected
    
    @property
    def is_responding(self) -> bool:
        """Check if model is currently responding."""
        return self._is_responding
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration."""
        config = {
            "provider": self.__class__.__name__,
            "name": self.name,
            "model": self.model,
            "voice": self.voice,
            "base_url": self.base_url,
            "input_sample_rate": self.input_sample_rate,
            "output_sample_rate": self.output_sample_rate,
            "vad_threshold": self.vad_threshold,
            "silence_duration_ms": self.silence_duration_ms,
            "is_connected": self._is_connected,
        }
        return config
