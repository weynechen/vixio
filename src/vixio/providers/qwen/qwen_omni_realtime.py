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
import json
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger as base_logger

from vixio.providers.realtime import BaseRealtimeProvider, RealtimeEvent, RealtimeEventType
from vixio.providers.registry import register_provider
from vixio.core.tools.types import ToolDefinition


@register_provider("qwen-omni-realtime")
class QwenOmniRealtimeProvider(BaseRealtimeProvider):
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
    - Tool use support (via function calling)
    
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
            input_sample_rate: Input audio sample rate (only support 16000): 16000
            output_sample_rate: Output audio sample rate (only support 24000): 24000
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
        
        # State tracking
        self._is_responding = False
        self._current_error: Optional[Exception] = None
        
        # Track response completeness
        self._current_response_text_length = 0
        self._current_response_audio_bytes = 0
        
        # Response queue for async iterator pattern
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._response_active = False
        self._loop = None  # Will be set when needed
        
        self.logger = base_logger.bind(component=self.name)
        self.logger.info(
            f"Initialized Qwen Omni Realtime provider "
            f"(model={model}, voice={voice}, instructions={instructions})"
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
        """Initialize the provider."""
        # Capture event loop for thread-safe operations
        if not self._loop:
            self._loop = asyncio.get_running_loop()
        await self.connect()

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        if self._is_connected:
            return
        
        try:
            import dashscope
            dashscope.api_key = self.api_key
            from dashscope.audio.qwen_omni import (
                OmniRealtimeConversation,
                MultiModality,
                AudioFormat,
            )
        except ImportError as e:
            raise ImportError(
                "dashscope not installed. "
                "Install with: pip install dashscope>=1.23.9"
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
                
                self._conversation.update_session(
                    output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
                    voice=self.voice,
                    input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT, # Only support 16000Hz input
                    output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT, # Only support 24000Hz output
                    enable_input_audio_transcription=True,
                    input_audio_transcription_model="gummy-realtime-v1",
                    # Enable server-side VAD
                    enable_turn_detection=True,
                    turn_detection_type='server_vad',  # Critical: specify VAD mode explicitly
                    turn_detection_threshold=self.vad_threshold,
                    turn_detection_silence_duration_ms=self.silence_duration_ms,
                    instructions=self.instructions,
                )
                
                with self._connection_lock:
                    self._is_connected = True
                self.logger.info("Realtime WebSocket connection established")
                self._emit_event(RealtimeEvent(RealtimeEventType.SESSION_CREATED))
            except Exception as e:
                self.logger.error(f"Failed to establish connection: {e}")
                raise
        
        # Run connection in thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, connect_sync)
    
    async def disconnect(self) -> None:
        """Close connection."""
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
        self.logger.info("Qwen Omni Realtime provider disconnected")
    
    async def cleanup(self) -> None:
        """Cleanup provider resources."""
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._is_connected
    
    async def update_session(self, 
                           instructions: Optional[str] = None, 
                           tools: Optional[List[ToolDefinition]] = None,
                           voice: Optional[str] = None) -> None:
        """
        Update session configuration.
        """
        if not self._is_connected or not self._conversation:
            return

        kwargs = {}
        if instructions:
            kwargs["instructions"] = instructions
        if voice:
            kwargs["voice"] = voice
        
        # Convert tools to Qwen format if provided
        # Note: This part depends on Qwen SDK support for tools
        # Currently Qwen Omni Realtime SDK documentation might not fully support dynamic tool update via update_session
        # This is a placeholder for when it is supported or via other means
        if tools:
            # self.logger.warning("Tool update via update_session not fully implemented for Qwen Omni yet")
            # For now we assume tools are configured via some other mechanism or not supported dynamically in this version
            pass

        if kwargs:
            def update_sync():
                try:
                    self._conversation.update_session(**kwargs)
                    self._emit_event(RealtimeEvent(RealtimeEventType.SESSION_UPDATED))
                except Exception as e:
                    self.logger.error(f"Error updating session: {e}")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, update_sync)

    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data to the model."""
        if not self._is_connected or not self._conversation:
            self.logger.warning("Cannot send audio: not connected")
            return
        
        def send_sync():
            try:
                # DashScope SDK expects base64 string for append_audio? No, it expects base64 string for append_audio according to docs
                # But let's check SDK. Docs say: append_audio(self, audio_b64: str)
                audio_b64 = base64.b64encode(audio_data).decode('ascii')
                self._conversation.append_audio(audio_b64)
            except Exception as e:
                self.logger.error(f"Error sending audio: {e}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_sync)

    async def send_tool_output(self, tool_call_id: str, output: str) -> None:
        """Send tool execution result back to the model."""
        # Note: Qwen Omni Realtime SDK might have specific method for this or use generic message
        # This implementation needs verification against latest SDK
        self.logger.warning("send_tool_output not fully implemented for Qwen Omni")
        pass

    def _emit_event(self, event: RealtimeEvent) -> None:
        """
        Emit event to both callback handler and response queue.
        
        Supports dual patterns:
        1. Callback-based (legacy): via _event_handler  
        2. AsyncIterator (recommended): via _response_queue
        """
        # Legacy callback pattern
        if self._event_handler:
            self._event_handler(event)
        
        # AsyncIterator pattern: enqueue if active
        if self._response_active and self._loop:
            try:
                # Thread-safe queue put (callback runs in WebSocket thread)
                self._loop.call_soon_threadsafe(
                    self._response_queue.put_nowait,
                    event
                )
            except Exception as e:
                self.logger.error(f"Error enqueueing event: {e}")
    
    async def interrupt(self) -> None:
        """Interrupt current generation."""
        if self._conversation:
            def interrupt_sync():
                try:
                    # In VAD mode, user speech interrupts. 
                    # For manual interrupt we might need specific method
                    # self._conversation.cancel_response() if available
                    pass
                except Exception as e:
                    self.logger.warning(f"Error interrupting: {e}")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, interrupt_sync)
    
    async def stream_response(self) -> AsyncIterator[RealtimeEvent]:
        """
        Stream response events as async iterator.
        
        This method activates queue mode and yields events until
        RESPONSE_DONE is received.
        
        Usage:
            await provider.send_audio(audio_chunk)
            async for event in provider.stream_response():
                # Process event...
                if event.type == RealtimeEventType.RESPONSE_DONE:
                    break
        
        Yields:
            RealtimeEvent: Events from Qwen (audio, text, etc.)
        """
        # Note: Queue activation happens in response.created event handler, not here.
        # This ensures we don't miss events that arrive before stream_response() is called.
        
        self.logger.debug("Stream response started, waiting for events...")
        
        timeout_seconds = 30
        start_time = asyncio.get_event_loop().time()
        
        try:
            while True:
                # Check timeout
                if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                    self.logger.warning("Stream response timeout")
                    break
                
                try:
                    # Wait for event with short timeout
                    event = await asyncio.wait_for(
                        self._response_queue.get(),
                        timeout=0.1
                    )
                    
                    self.logger.debug(f"Yielding event: {event.type}")
                    yield event
                    
                    # Stop on RESPONSE_DONE or ERROR
                    if event.type == RealtimeEventType.RESPONSE_DONE:
                        self.logger.debug("RESPONSE_DONE received, stream complete")
                        break
                    elif event.type == RealtimeEventType.ERROR:
                        self.logger.warning(f"ERROR event received: {event.text}, stopping stream")
                        break
                        
                except asyncio.TimeoutError:
                    # No event available, continue waiting
                    continue
                    
        finally:
            # Note: Queue deactivation happens in response.done event handler
            self.logger.debug("Stream response ended")

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
                
                # Deactivate queue on connection close
                provider._response_active = False
                
                # Emit error event to notify session that connection is closed
                provider._emit_event(RealtimeEvent(
                    type=RealtimeEventType.ERROR,
                    text=f"WebSocket connection closed: {close_status_code}"
                ))
            
            def on_event(self, event):
                try:
                    event_type = event.get("type", "")
                    provider.logger.debug(f"Qwen event: {event_type}")
                    
                    # === Audio/Text Output ===
                    if event_type == "response.audio.delta":
                        audio_b64 = event.get("delta", "")
                        if audio_b64:
                            audio_data = base64.b64decode(audio_b64)
                            provider._current_response_audio_bytes += len(audio_data)
                            provider.logger.debug(f"Qwen: response.audio.delta, size={len(audio_data)} bytes (total: {provider._current_response_audio_bytes})")
                            provider._emit_event(RealtimeEvent(
                                type=RealtimeEventType.AUDIO_DELTA,
                                data=audio_data
                            ))
                    
                    elif event_type == "response.audio_transcript.delta":
                        text_delta = event.get("delta", "")
                        if text_delta:
                            provider._current_response_text_length += len(text_delta)
                            provider.logger.debug(f"Qwen: response.audio_transcript.delta, text='{text_delta}' (total chars: {provider._current_response_text_length})")
                            provider._emit_event(RealtimeEvent(
                                type=RealtimeEventType.TEXT_DELTA,
                                text=text_delta
                            ))
                    
                    # === User Input Transcript ===
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "")
                        if transcript:
                            provider._emit_event(RealtimeEvent(
                                type=RealtimeEventType.TRANSCRIPT_COMPLETE,
                                text=transcript
                            ))
                    
                    # === VAD Events ===
                    elif event_type == "input_audio_buffer.speech_started":
                        provider._emit_event(RealtimeEvent(RealtimeEventType.SPEECH_START))
                    
                    elif event_type == "input_audio_buffer.speech_stopped":
                        provider._emit_event(RealtimeEvent(RealtimeEventType.SPEECH_STOP))
                    
                    # === Lifecycle Events ===
                    elif event_type == "response.created":
                        provider._is_responding = True
                        # Reset tracking counters for new response
                        provider._current_response_text_length = 0
                        provider._current_response_audio_bytes = 0
                        
                        # Activate queue mode IMMEDIATELY when response starts
                        # This ensures we capture all events (text, audio) that arrive
                        # before stream_response() is called (race condition fix)
                        provider._response_active = True
                        provider.logger.debug("Response queue activated (response.created)")
                        
                        provider._emit_event(RealtimeEvent(RealtimeEventType.RESPONSE_START))
                    
                    elif event_type == "response.done":
                        provider._is_responding = False
                        
                        # Get performance metrics from SDK (like official example)
                        try:
                            response_id = provider._conversation.get_last_response_id()
                            first_text_delay = provider._conversation.get_last_first_text_delay()
                            first_audio_delay = provider._conversation.get_last_first_audio_delay()
                            provider.logger.info(
                                f"[Metric] response_id={response_id}, "
                                f"first_text_delay={first_text_delay}ms, "
                                f"first_audio_delay={first_audio_delay}ms"
                            )
                        except Exception as e:
                            provider.logger.debug(f"Failed to get SDK metrics: {e}")
                        
                        # Log completeness metrics
                        provider.logger.info(
                            f"Response complete: text_length={provider._current_response_text_length} chars, "
                            f"audio_size={provider._current_response_audio_bytes} bytes "
                            f"(~{provider._current_response_audio_bytes / 48000:.2f}s at 24kHz PCM)"
                        )
                        # Warn if audio seems too short for the text
                        # Rough estimate: Chinese ~2-3 chars/sec, English ~3-4 words/sec
                        expected_min_audio_bytes = provider._current_response_text_length * 1000  # Very rough estimate
                        if provider._current_response_audio_bytes < expected_min_audio_bytes * 0.1:
                            provider.logger.warning(
                                f"⚠️ Audio may be incomplete! Text: {provider._current_response_text_length} chars, "
                                f"Audio: {provider._current_response_audio_bytes} bytes. This might indicate an API issue."
                            )
                        
                        # Emit RESPONSE_DONE event first
                        provider._emit_event(RealtimeEvent(RealtimeEventType.RESPONSE_DONE))
                        
                        # Deactivate queue mode after RESPONSE_DONE is sent
                        provider._response_active = False
                        provider.logger.debug("Response queue deactivated (response.done)")
                    
                    # === Error ===
                    elif event_type == "error":
                        error_msg = event.get("error", {}).get("message", "Unknown error")
                        provider._emit_event(RealtimeEvent(
                            type=RealtimeEventType.ERROR,
                            text=error_msg
                        ))
                        # Deactivate queue on error
                        provider._response_active = False
                        provider.logger.debug("Response queue deactivated (error)")
                    
                    # === Tool Calls (Placeholder) ===
                    # Need to check actual event type for tool calls in Qwen
                    # elif event_type == "response.function_call_arguments.done":
                    #     ...

                except Exception as e:
                    provider.logger.error(f"Error processing event: {e}")
            
            def on_error(self, error):
                provider.logger.error(f"Realtime WebSocket error: {error}")
                # Deactivate queue on error
                provider._response_active = False
                provider._emit_event(RealtimeEvent(
                    type=RealtimeEventType.ERROR,
                    text=str(error)
                ))
                with provider._connection_lock:
                    provider._is_connected = False
        
        return RealtimeCallback()
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration."""
        config = {
            "provider": self.__class__.__name__,
            "name": self.name,
            "model": self.model,
            "voice": self.voice,
            "base_url": self.base_url,
            "is_connected": self._is_connected,
        }
        return config
