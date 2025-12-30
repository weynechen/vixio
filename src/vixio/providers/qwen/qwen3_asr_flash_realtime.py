"""
Qwen3 ASR Flash Realtime Provider

Features:
- Built-in VAD (server-side voice activity detection)
- Text context support via corpus_text (domain-specific terms)
- Streaming transcription output
- Multi-language support

Reference: https://help.aliyun.com/zh/model-studio/qwen-real-time-speech-recognition

Requires: dashscope>=1.25.3
"""

import asyncio
import base64
import os
import time
from typing import Optional, List
from collections.abc import AsyncIterator

from loguru import logger as base_logger

from vixio.providers.asr import ASRProvider, ASRStreamResult
from vixio.providers.registry import register_provider


@register_provider("qwen3-asr-flash-realtime")
class Qwen3AsrFlashRealtimeProvider(ASRProvider):
    """
    Qwen3 ASR Flash Realtime Provider.
    
    Features:
    - Built-in VAD (server-side)
    - Text context support via corpus_text
    - Streaming transcription output
    - Multi-language support
    
    Reference: https://help.aliyun.com/zh/model-studio/qwen-real-time-speech-recognition
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
        return "asr"
    
    @property
    def supports_vad(self) -> bool:
        """This provider has built-in VAD support."""
        return True
    
    @property
    def supports_context(self) -> bool:
        """This provider supports text context enhancement."""
        return True
    
    @property
    def supports_streaming_input(self) -> bool:
        """This provider supports continuous audio streaming."""
        return True
    
    @classmethod
    def get_config_schema(cls):
        """Return configuration schema."""
        return {
            "api_key": {
                "type": "string",
                "default": None,
                "description": "DashScope API key. Uses DASHSCOPE_API_KEY env var if not provided."
            },
            "model": {
                "type": "string",
                "default": "qwen3-asr-flash-realtime",
                "description": "Model name"
            },
            "language": {
                "type": "string",
                "default": "zh",
                "description": "Language code (zh, en, auto, etc.)"
            },
            "base_url": {
                "type": "string",
                "default": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "description": "WebSocket API URL"
            },
            "sample_rate": {
                "type": "int",
                "default": 16000,
                "description": "Audio sample rate (8000 or 16000)"
            },
            "enable_vad": {
                "type": "bool",
                "default": False,
                "description": "Enable built-in VAD"
            },
            "vad_threshold": {
                "type": "float",
                "default": 0.2,
                "description": "VAD threshold (0.0-1.0)"
            },
            "silence_duration_ms": {
                "type": "int",
                "default": 800,
                "description": "Silence duration to trigger end (ms)"
            }
        }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen3-asr-flash-realtime",
        language: str = "zh",
        base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        sample_rate: int = 16000,
        enable_vad: bool = True,  
        vad_threshold: float = 0.2,
        silence_duration_ms: int = 800,
    ):
        """
        Initialize Qwen3 ASR provider.
        
        Args:
            api_key: DashScope API key (or use DASHSCOPE_API_KEY env var)
            model: Model name (default: qwen3-asr-flash-realtime)
            language: Language code (zh, en, auto, etc.)
            base_url: WebSocket URL
            sample_rate: Audio sample rate (8000 or 16000)
            enable_vad: Enable built-in VAD (useful when no external VAD)
            vad_threshold: VAD threshold (0.0-1.0)
            silence_duration_ms: Silence duration to trigger end
        """
        super().__init__(name="qwen3-asr-flash-realtime")
        
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY required. "
                "Set DASHSCOPE_API_KEY environment variable or pass api_key parameter."
            )
        
        self.model = model
        self.language = language
        self.base_url = base_url
        self.sample_rate = sample_rate
        self.enable_vad = enable_vad
        self.vad_threshold = vad_threshold
        self.silence_duration_ms = silence_duration_ms
        
        # Connection state
        self._conversation = None
        self._callback = None
        self._is_connected = False
        
        # Result and event queues
        self._result_queue: asyncio.Queue = asyncio.Queue()
        self._event_queue: asyncio.Queue = asyncio.Queue()  # VAD events queue
        self._loop = None
        
        # Streaming state
        self._speech_ended = False  # For StreamingASRStation
        
        self.logger = base_logger.bind(component=self.name)
        self.logger.info(
            f"Initialized Qwen3 ASR provider (model={model}, language={language}, "
            f"vad={'enabled' if enable_vad else 'disabled'})"
        )
    
    async def initialize(self) -> None:
        """Initialize provider."""
        self._loop = asyncio.get_running_loop()
        await self._connect()
    
    async def _connect(self) -> None:
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
            from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams
        except ImportError as e:
            raise ImportError(
                "dashscope required. "
                "or error module path"
            ) from e
        
        self._callback = self._create_callback()
        
        self._conversation = OmniRealtimeConversation(
            model=self.model,
            url=self.base_url,
            callback=self._callback
        )
        
        # Connect in thread
        def connect_sync():
            self._conversation.connect()
            
            transcription_params = TranscriptionParams(
                language=self.language,
                sample_rate=self.sample_rate,
                input_audio_format="pcm"
            )
            
            self._conversation.update_session(
                output_modalities=[MultiModality.TEXT],
                input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                enable_input_audio_transcription=True,
                transcription_params=transcription_params,
                enable_turn_detection=self.enable_vad,
                turn_detection_type='server_vad' if self.enable_vad else None,
                turn_detection_threshold=self.vad_threshold if self.enable_vad else None,
                turn_detection_silence_duration_ms=self.silence_duration_ms if self.enable_vad else None,
            )
            
            self._is_connected = True
            self.logger.info("ASR WebSocket connected")
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, connect_sync)
    
    async def transcribe_stream(
        self, 
        audio_chunks: List[bytes],
        context: Optional[str] = None
    ) -> AsyncIterator[str]:
        """
        Transcribe audio with optional context.
        
        Args:
            audio_chunks: PCM audio bytes (16kHz mono 16-bit)
            context: Optional text context for better recognition
        
        Yields:
            Transcription text (streaming or final)
        """
        if not self._is_connected:
            await self._connect()
        
        # Update context if provided
        if context and self._conversation:
            from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams
            
            def update_context():
                transcription_params = TranscriptionParams(
                    language=self.language,
                    sample_rate=self.sample_rate,
                    input_audio_format="pcm",
                    corpus_text=context  # Context enhancement!
                )
                self._conversation.update_session(
                    transcription_params=transcription_params
                )
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, update_context)
            self.logger.info(f"Updated ASR context: {context[:100]}...")
        
        # Send audio
        for audio_chunk in audio_chunks:
            def send_audio():
                audio_b64 = base64.b64encode(audio_chunk).decode('ascii')
                self._conversation.append_audio(audio_b64)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, send_audio)
        
        # Commit if VAD disabled (manual mode)
        if not self.enable_vad:
            def commit():
                self._conversation.commit()
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, commit)
        
        # Wait for result
        timeout = 10.0
        try:
            result = await asyncio.wait_for(
                self._result_queue.get(),
                timeout=timeout
            )
            if result:
                yield result
        except asyncio.TimeoutError:
            self.logger.warning("ASR transcription timeout")
    
    def _create_callback(self):
        """Create callback for WebSocket events."""
        from dashscope.audio.qwen_omni import OmniRealtimeCallback
        
        provider = self
        
        class AsrCallback(OmniRealtimeCallback):
            def on_open(self):
                provider.logger.debug("ASR WebSocket opened")
            
            def on_close(self, code, msg):
                provider.logger.info(f"ASR WebSocket closed: {code} {msg}")
                provider._is_connected = False
            
            def on_event(self, event):
                try:
                    event_type = event.get("type", "")
                    
                    # Streaming transcription (optional)
                    if event_type == "conversation.item.input_audio_transcription.text":
                        text = event.get("text", "")
                        if text and provider._loop:
                            provider._loop.call_soon_threadsafe(
                                provider._result_queue.put_nowait,
                                text
                            )
                    
                    # Final transcription
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "")
                        if transcript and provider._loop:
                            provider._loop.call_soon_threadsafe(
                                provider._result_queue.put_nowait,
                                transcript
                            )
                        
                        # Mark speech as ended (for StreamingASRStation)
                        provider._speech_ended = True
                    
                    # VAD events - put into event queue with timestamp
                    elif event_type == "input_audio_buffer.speech_started":
                        provider.logger.debug("VAD: Speech started")
                        provider._speech_ended = False  # Reset flag
                        if provider._loop:
                            provider._loop.call_soon_threadsafe(
                                provider._event_queue.put_nowait,
                                ("speech_started", time.time())
                            )
                    elif event_type == "input_audio_buffer.speech_stopped":
                        provider.logger.debug("VAD: Speech stopped")
                        # Put event into queue for StreamingASRStation to handle
                        if provider._loop:
                            provider._loop.call_soon_threadsafe(
                                provider._event_queue.put_nowait,
                                ("speech_stopped", time.time())
                            )
                        # Don't set _speech_ended here, wait for transcription.completed
                
                except Exception as e:
                    provider.logger.error(f"Error processing ASR event: {e}")
        
        return AsrCallback()
    
    async def reset(self) -> None:
        """Reset provider state."""
        # Clear result queue
        while not self._result_queue.empty():
            try:
                self._result_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        # Clear event queue
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
    async def append_audio_continuous(self, audio_data: bytes) -> AsyncIterator[ASRStreamResult]:
        """
        Append audio to continuous streaming ASR session (for StreamingASRStation).
        
        This method is called repeatedly by StreamingASRStation for each AUDIO_DELTA.
        ASR provider processes continuously with built-in VAD.
        
        Args:
            audio_data: Audio bytes (PCM, 16kHz, mono, 16-bit)
            
        Yields:
            ASRStreamResult with text and/or VAD events
        """
        if not self._is_connected:
            # Initialize with VAD enabled for streaming mode
            await self._connect()
            
            # Enable VAD for streaming
            def update_vad():
                from dashscope.audio.qwen_omni.omni_realtime import TurnDetectionType
                
                self._conversation.update_session(
                    enable_turn_detection=True,
                    turn_detection_type=TurnDetectionType.SERVER_VAD,
                    vad_threshold=self.vad_threshold,
                    silence_duration_ms=self.silence_duration_ms
                )
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, update_vad)
            
            self.logger.info("StreamingASR session started with built-in VAD")
        
        # Append audio (convert bytes to base64 string as required by API)
        audio_b64 = base64.b64encode(audio_data).decode('utf-8')
        
        def append():
            self._conversation.append_audio(audio_b64)
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, append)
        
        self.logger.debug(f"Appended {len(audio_data)} bytes to ASR stream")
        
        # Yield VAD events first (important for latency monitoring)
        while not self._event_queue.empty():
            try:
                event_type, timestamp = self._event_queue.get_nowait()
                yield ASRStreamResult(event=event_type, timestamp=timestamp)
            except asyncio.QueueEmpty:
                break
        
        # Yield text results
        while not self._result_queue.empty():
            try:
                text = self._result_queue.get_nowait()
                if text:
                    yield ASRStreamResult(text=text)
            except asyncio.QueueEmpty:
                break
    
    def is_speech_ended(self) -> bool:
        """
        Check if ASR has detected end of speech.
        
        In streaming mode with built-in VAD, ASR signals when user stops speaking.
        This is used by StreamingASRStation to emit completion.
        
        Note: This method returns True only ONCE per speech end event.
        After returning True, it automatically resets to False.
        
        Returns:
            True if speech has ended (only once per event)
        """
        # Check if there's a special marker in metadata
        # Note: Implementation depends on how Qwen signals speech end
        ended = getattr(self, '_speech_ended', False)
        if ended:
            # Reset after reading to ensure we only signal once
            self._speech_ended = False
        return ended
    
    async def stop_streaming(self) -> None:
        """
        Stop streaming ASR session.
        
        Called by StreamingASRStation on interrupt.
        """
        if self._conversation and self._is_connected:
            try:
                # Signal end of audio stream
                def commit():
                    self._conversation.commit()
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, commit)
                
                self.logger.info("Stopped ASR streaming session")
            except Exception as e:
                self.logger.error(f"Error stopping ASR stream: {e}")
        
        self._speech_ended = False
    
    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self._conversation:
            try:
                def close():
                    self._conversation.close()
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, close)
            except Exception as e:
                self.logger.warning(f"Error closing ASR connection: {e}")
        
        self._conversation = None
        self._is_connected = False
        self.logger.info("ASR provider cleaned up")
