"""
Qwen3 TTS Flash Realtime Provider

Features:
- Two modes: server_commit (auto-segmentation) and commit (manual)
- Streaming text input support (server_commit mode)
- Multi-format output (PCM, MP3, Opus, WAV)
- Adjustable voice parameters (speed, pitch, volume)

Reference: https://help.aliyun.com/zh/model-studio/qwen-tts-realtime

Requires: dashscope>=1.25.3
"""

import asyncio
import base64
import os
from typing import Optional
from collections.abc import AsyncIterator

from loguru import logger as base_logger

from vixio.providers.tts import TTSProvider
from vixio.providers.registry import register_provider


@register_provider("qwen3-tts-flash-realtime")
class Qwen3TtsFlashRealtimeProvider(TTSProvider):
    """
    Qwen3 TTS Flash Realtime Provider.
    
    Features:
    - Two modes: server_commit (auto-segmentation) and commit (manual)
    - Streaming text input support (server_commit mode)
    - Multi-format output (PCM, MP3, Opus, WAV)
    - Adjustable voice parameters (speed, pitch, volume)
    
    Reference: https://help.aliyun.com/zh/model-studio/qwen-tts-realtime
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
        """This provider supports streaming text input in server_commit mode."""
        return self.mode == "server_commit"
    
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
                "default": "qwen3-tts-flash-realtime",
                "description": "Model name"
            },
            "voice": {
                "type": "string",
                "default": "Cherry",
                "description": "Voice name (Cherry, Ethan, etc.)"
            },
            "base_url": {
                "type": "string",
                "default": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "description": "WebSocket API URL"
            },
            "mode": {
                "type": "string",
                "default": "server_commit",
                "description": "Response mode (server_commit or commit)"
            },
            "sample_rate": {
                "type": "int",
                "default": 24000,
                "description": "Output sample rate (8000-48000)"
            },
            "audio_format": {
                "type": "string",
                "default": "pcm",
                "description": "Audio format (pcm, mp3, opus, wav)"
            },
            "language_type": {
                "type": "string",
                "default": "Chinese",
                "description": "Language type (Chinese, English, Auto, etc.)"
            },
            "speech_rate": {
                "type": "float",
                "default": 1.0,
                "description": "Speech rate (0.5-2.0)"
            },
            "pitch_rate": {
                "type": "float",
                "default": 1.0,
                "description": "Pitch rate (0.5-2.0)"
            },
            "volume": {
                "type": "int",
                "default": 50,
                "description": "Volume (0-100)"
            }
        }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen3-tts-flash-realtime",
        voice: str = "Cherry",
        base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        mode: str = "server_commit",  # or "commit"
        sample_rate: int = 24000,
        audio_format: str = "pcm",
        language_type: str = "Chinese",
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        volume: int = 50,
    ):
        """
        Initialize Qwen3 TTS provider.
        
        Args:
            api_key: DashScope API key (or use DASHSCOPE_API_KEY env var)
            model: Model name (default: qwen3-tts-flash-realtime)
            voice: Voice name (Cherry, Ethan, etc.)
            base_url: WebSocket URL
            mode: "server_commit" (auto-segment) or "commit" (manual)
            sample_rate: Output sample rate (8000-48000)
            audio_format: "pcm", "mp3", "opus", "wav"
            language_type: "Chinese", "English", "Auto", etc.
            speech_rate: Speech rate (0.5-2.0)
            pitch_rate: Pitch rate (0.5-2.0)
            volume: Volume (0-100)
        """
        super().__init__(name="qwen3-tts-flash-realtime")
        
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY required. "
                "Set DASHSCOPE_API_KEY environment variable or pass api_key parameter."
            )
        
        self.model = model
        self.voice = voice
        self.base_url = base_url
        self.mode = mode
        self.sample_rate = sample_rate
        self.audio_format = audio_format
        self.language_type = language_type
        self.speech_rate = speech_rate
        self.pitch_rate = pitch_rate
        self.volume = volume
        
        # Connection state
        self._tts = None
        self._callback = None
        self._is_connected = False
        
        # Audio queue
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._synthesis_complete = asyncio.Event()
        self._loop = None
        
        self.logger = base_logger.bind(component=self.name)
        self.logger.info(
            f"Initialized Qwen3 TTS provider (model={model}, voice={voice}, mode={mode})"
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
            from dashscope.audio.qwen_tts_realtime import (
                QwenTtsRealtime,
                QwenTtsRealtimeCallback,
                AudioFormat
            )
        except ImportError as e:
            raise ImportError(
                "dashscope required. "
                "or error module path"
            ) from e
        
        self._callback = self._create_callback()
        
        self._tts = QwenTtsRealtime(
            model=self.model,
            url=self.base_url,
            callback=self._callback
        )
        
        # Connect in thread
        def connect_sync():
            self._tts.connect()
            
            self._tts.update_session(
                voice=self.voice,
                mode=self.mode,
                response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                sample_rate=self.sample_rate,
                audio_format=self.audio_format,
                language_type=self.language_type,
                speech_rate=self.speech_rate,
                pitch_rate=self.pitch_rate,
                volume=self.volume
            )
            
            self._is_connected = True
            self.logger.info(f"TTS WebSocket connected (mode={self.mode})")
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, connect_sync)
    
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize complete text (batch mode).
        
        Works in both server_commit and commit modes.
        
        Args:
            text: Complete text to synthesize
            
        Yields:
            Audio bytes (PCM format, 24kHz, mono, 16-bit)
        """
        if not self._is_connected:
            await self._connect()
        
        # Clear previous audio
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        self._synthesis_complete.clear()
        
        # Send text
        def send_text():
            self._tts.append_text(text)
            if self.mode == "commit":
                self._tts.commit()
            else:
                self._tts.finish()
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_text)
        
        # Yield audio chunks
        while True:
            try:
                audio = await asyncio.wait_for(
                    self._audio_queue.get(),
                    timeout=0.1
                )
                yield audio
            except asyncio.TimeoutError:
                if self._synthesis_complete.is_set():
                    break
                continue
    
    async def synthesize_stream(
        self, 
        text_stream: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """
        Synthesize streaming text (server_commit mode only).
        
        In server_commit mode, server intelligently segments text and
        starts synthesis without waiting for complete sentences.
        
        Args:
            text_stream: Streaming text chunks (e.g., from LLM)
            
        Yields:
            Audio bytes as synthesis progresses
        """
        if self.mode != "server_commit":
            # Fallback to batch mode
            self.logger.warning(
                f"synthesize_stream called but mode is '{self.mode}'. "
                "Falling back to batch mode."
            )
            async for audio in super().synthesize_stream(text_stream):
                yield audio
            return
        
        if not self._is_connected:
            await self._connect()
        
        self._synthesis_complete.clear()
        
        # Send text stream
        async def send_stream():
            async for text_chunk in text_stream:
                def send():
                    self._tts.append_text(text_chunk)
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, send)
            
            # Signal end
            def finish():
                self._tts.finish()
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, finish)
        
        # Start sending in background
        send_task = asyncio.create_task(send_stream())
        
        # Yield audio as it arrives
        try:
            while True:
                try:
                    audio = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=0.1
                    )
                    yield audio
                except asyncio.TimeoutError:
                    if self._synthesis_complete.is_set():
                        break
                    continue
        finally:
            await send_task
    
    def _create_callback(self):
        """Create callback for WebSocket events."""
        from dashscope.audio.qwen_tts_realtime import QwenTtsRealtimeCallback
        
        provider = self
        
        class TtsCallback(QwenTtsRealtimeCallback):
            def on_open(self):
                provider.logger.debug("TTS WebSocket opened")
            
            def on_close(self, code, msg):
                provider.logger.info(f"TTS WebSocket closed: {code} {msg}")
                provider._is_connected = False
            
            def on_event(self, event):
                try:
                    event_type = event.get("type", "")
                    
                    # Audio chunk available
                    if event_type == "response.audio.delta":
                        audio_b64 = event.get("delta", "")
                        if audio_b64 and provider._loop:
                            audio_data = base64.b64decode(audio_b64)
                            provider._loop.call_soon_threadsafe(
                                provider._audio_queue.put_nowait,
                                audio_data
                            )
                    
                    # Synthesis complete
                    elif event_type == "response.done":
                        provider.logger.debug("TTS synthesis complete")
                        if provider._loop:
                            provider._loop.call_soon_threadsafe(
                                provider._synthesis_complete.set
                            )
                    
                    # Session finished
                    elif event_type == "session.finished":
                        provider.logger.debug("TTS session finished")
                        if provider._loop:
                            provider._loop.call_soon_threadsafe(
                                provider._synthesis_complete.set
                            )
                
                except Exception as e:
                    provider.logger.error(f"Error processing TTS event: {e}")
        
        return TtsCallback()
    
    def cancel(self) -> None:
        """Cancel ongoing synthesis."""
        # Clear audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        self._synthesis_complete.set()
        self.logger.debug("TTS synthesis cancelled")
    
    async def reset_state(self) -> None:
        """
        Reset provider state for new turn.
        
        CRITICAL: Must be called when turn changes to prevent:
        - Stale audio from previous turn being mixed in
        - _synthesis_complete event causing premature finish
        """
        # Clear audio queue (discard any stale audio from previous turn)
        cleared_count = 0
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                cleared_count += 1
            except asyncio.QueueEmpty:
                break
        
        # Check if completion event was set (from previous turn)
        was_complete = self._synthesis_complete.is_set()
        
        # Reset completion event for new turn
        self._synthesis_complete.clear()
        
        # Log at INFO level so we can verify in production logs
        self.logger.info(
            f"TTS provider state reset: cleared {cleared_count} stale audio chunks, "
            f"completion_was_set={was_complete}"
        )
    
    async def append_text_stream(self, text_chunk: str) -> AsyncIterator[bytes]:
        """
        Append text chunk to streaming TTS session (for StreamingTTSStation).
        
        This method is called repeatedly by StreamingTTSStation for each TEXT_DELTA.
        In server_commit mode, TTS intelligently segments and synthesizes.
        
        Args:
            text_chunk: Text delta to append
            
        Yields:
            Audio bytes as they become available
        """
        if not self._is_connected:
            await self._connect()
        
        # Safety check: if completion event is set from previous turn, clear it
        # This handles edge cases where reset_state() might not be called
        if self._synthesis_complete.is_set():
            self.logger.warning(
                "TTS completion event was set when starting new text stream! "
                "Clearing to prevent premature finish. This may indicate reset_state() was not called."
            )
            self._synthesis_complete.clear()
        
        # Append text to TTS
        def send():
            self._tts.append_text(text_chunk)
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send)
        
        self.logger.debug(f"Appended text chunk: {text_chunk[:50]}...")
        
        # Yield any audio that has arrived
        # Note: In server_commit mode, audio may arrive asynchronously
        while not self._audio_queue.empty():
            try:
                audio = self._audio_queue.get_nowait()
                yield audio
            except asyncio.QueueEmpty:
                break
    
    async def finish_stream(self) -> AsyncIterator[bytes]:
        """
        Finish streaming TTS session and flush remaining audio.
        
        Called by StreamingTTSStation on completion event.
        
        Yields:
            Remaining audio bytes
        """
        if not self._is_connected:
            return
            yield  # Make it a generator
        
        self.logger.info("Finishing TTS stream")
        
        # Signal end of text
        def finish():
            self._tts.finish()
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, finish)
        
        # Yield remaining audio
        while True:
            try:
                audio = await asyncio.wait_for(
                    self._audio_queue.get(),
                    timeout=0.1
                )
                yield audio
            except asyncio.TimeoutError:
                if self._synthesis_complete.is_set():
                    break
                continue
    
    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self._tts:
            try:
                def close():
                    self._tts.close()
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, close)
            except Exception as e:
                self.logger.warning(f"Error closing TTS connection: {e}")
        
        self._tts = None
        self._is_connected = False
        self.logger.info("TTS provider cleaned up")
