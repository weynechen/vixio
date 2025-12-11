"""
Qwen3 TTS Flash Realtime Provider

Realtime text-to-speech synthesis using Alibaba Cloud's Qwen3-TTS-Flash model
via DashScope WebSocket API.

Reference: https://help.aliyun.com/zh/model-studio/qwen-tts-realtime

Features:
- Session-level connection reuse (connection established in initialize(), closed in cleanup())
- Low latency streaming synthesis
- Automatic reconnection on connection loss

Requires: pip install dashscope>=1.25.2
"""

import asyncio
import base64
import os
import queue
import threading
from typing import Any, AsyncIterator, Dict, Optional

import numpy as np

from vixio.providers.tts import TTSProvider
from vixio.providers.registry import register_provider


@register_provider("qwen-tts-realtime")
class QwenTTSRealtimeProvider(TTSProvider):
    """
    Qwen3 TTS Flash Realtime Provider with session-level connection reuse.
    
    The WebSocket connection is established during initialize() and reused
    for all synthesis requests within the session. Connection is closed
    in cleanup().
    
    Features:
    - Session-level connection reuse for low latency
    - Automatic reconnection on connection loss
    - Multiple voice options
    - PCM audio output (24kHz native, resampled to target rate if needed)
    
    Requirements:
        pip install dashscope>=1.25.2
    """
    
    # Native sample rate of Qwen TTS
    NATIVE_SAMPLE_RATE = 24000
    
    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service"""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """TTS is stateless - each request is independent"""
        return False
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "tts"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen3-tts-flash-realtime",
        voice: str = "Cherry",
        language_type: str = "Chinese",
        sample_rate: int = 16000,
        base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    ):
        """
        Initialize Qwen TTS Realtime provider.
        
        Args:
            api_key: DashScope API key. If None, uses DASHSCOPE_API_KEY env var.
            model: Model name (default: qwen3-tts-flash-realtime)
            voice: Voice name. Available voices:
                   - Chinese female: Cherry, Serena, Ethan, Chelsie
                   - More voices available in DashScope documentation
            language_type: Language type (Chinese/English)
            sample_rate: Output sample rate (will resample from 24kHz if different)
            base_url: WebSocket API URL
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
        self.language_type = language_type
        self.sample_rate = sample_rate
        self.base_url = base_url
        
        # Connection state
        self._tts_client = None
        self._callback = None
        self._is_connected = False
        self._connection_lock = threading.Lock()
        
        # Current synthesis state
        self._audio_queue: queue.Queue = queue.Queue()
        self._response_done = threading.Event()  # response.done received
        self._session_finished = threading.Event()  # session.finished received
        self._current_error: Optional[Exception] = None
        
        # Cancellation
        self._cancelled = False
        
        self.logger.info(
            f"Initialized Qwen TTS Realtime provider "
            f"(model={model}, voice={voice}, sample_rate={sample_rate})"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "api_key": {
                "type": "string",
                "default": None,
                "description": "DashScope API key. Uses DASHSCOPE_API_KEY env var if not provided."
            },
            "model": {
                "type": "string",
                "default": "qwen3-tts-flash-realtime",
                "description": "Model name for TTS"
            },
            "voice": {
                "type": "string",
                "default": "Cherry",
                "description": "Voice name (Cherry/Serena/Ethan/Chelsie/etc.)"
            },
            "language_type": {
                "type": "string",
                "default": "Chinese",
                "description": "Language type (Chinese/English)"
            },
            "sample_rate": {
                "type": "int",
                "default": 16000,
                "description": "Output sample rate (will resample from 24kHz if different)"
            },
            "base_url": {
                "type": "string",
                "default": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "description": "WebSocket API URL"
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
                "Install with: pip install dashscope>=1.25.2"
            ) from e
        
        # Establish connection
        await self._connect()
        self.logger.info("Qwen TTS Realtime provider initialized with persistent connection")
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._is_connected:
            return
        
        try:
            from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime, QwenTtsRealtimeCallback, AudioFormat
        except ImportError as e:
            raise ImportError(
                "QwenTtsRealtime not available. "
                "Install dashscope>=1.25.2 with: pip install dashscope>=1.25.2"
            ) from e
        
        # Create callback instance
        self._callback = self._create_callback()
        
        # Create TTS client
        self._tts_client = QwenTtsRealtime(
            model=self.model,
            callback=self._callback,
            url=self.base_url
        )
        
        # Connect in a thread to avoid blocking
        def connect_sync():
            try:
                self._tts_client.connect()
                
                # Configure session - Qwen TTS only supports 24kHz output
                self._tts_client.update_session(
                    voice=self.voice,
                    language_type=self.language_type,
                    response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                    mode='server_commit'
                )
                
                with self._connection_lock:
                    self._is_connected = True
                self.logger.info("TTS WebSocket connection established")
            except Exception as e:
                self.logger.error(f"Failed to establish TTS connection: {e}")
                raise
        
        # Run connection in thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, connect_sync)
    
    def _create_callback(self):
        """Create callback instance for handling WebSocket events."""
        from dashscope.audio.qwen_tts_realtime import QwenTtsRealtimeCallback
        
        provider = self  # Capture reference
        
        class TTSCallback(QwenTtsRealtimeCallback):
            def on_open(self) -> None:
                provider.logger.debug("TTS WebSocket connection opened")
            
            def on_close(self, close_status_code, close_msg) -> None:
                provider.logger.info(f"TTS WebSocket closed: {close_status_code}, {close_msg}")
                with provider._connection_lock:
                    provider._is_connected = False
                # Signal synthesis complete on close
                provider._session_finished.set()
            
            def on_event(self, response) -> None:
                try:
                    if provider._cancelled:
                        return
                    
                    event_type = response.get("type", "")
                    
                    if event_type == "response.audio.delta":
                        # Audio data chunk
                        delta = response.get("delta", "")
                        if delta:
                            audio_data = base64.b64decode(delta)
                            provider._audio_queue.put(audio_data)
                    
                    elif event_type == "response.done":
                        # Response done, but audio may still be arriving
                        provider.logger.debug("Response done (waiting for session.finished)")
                        provider._response_done.set()
                    
                    elif event_type == "session.created":
                        provider.logger.debug(f"Session created: {response.get('session', {}).get('id', '')}")
                    
                    elif event_type == "session.updated":
                        provider.logger.debug("Session updated")
                    
                    elif event_type == "session.finished":
                        # Session finished - all audio has been sent
                        provider.logger.debug("Session finished (all audio received)")
                        provider._session_finished.set()
                    
                    elif event_type == "error":
                        error_msg = response.get("error", {}).get("message", "Unknown error")
                        provider._current_error = Exception(f"TTS error: {error_msg}")
                        provider.logger.error(f"TTS error: {error_msg}")
                        provider._session_finished.set()
                
                except Exception as e:
                    provider.logger.error(f"Error processing TTS event: {e}")
                    provider._current_error = e
                    provider._session_finished.set()
            
            def on_error(self, error) -> None:
                provider.logger.error(f"TTS WebSocket error: {error}")
                provider._current_error = Exception(str(error))
                with provider._connection_lock:
                    provider._is_connected = False
                provider._synthesis_complete.set()
        
        return TTSCallback()
    
    async def cleanup(self) -> None:
        """Cleanup provider resources and close connection."""
        self.cancel()
        
        if self._tts_client:
            try:
                # Close in thread to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._tts_client.close)
            except Exception as e:
                self.logger.warning(f"Error closing TTS connection: {e}")
        
        self._tts_client = None
        self._callback = None
        self._is_connected = False
        self.logger.info("Qwen TTS Realtime provider cleaned up")
    
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize text to audio using the persistent connection.
        
        Args:
            text: Text to synthesize
            
        Yields:
            PCM audio bytes (16-bit signed, little-endian, mono)
        """
        if not text or not text.strip():
            self.logger.warning("Empty text provided for TTS")
            return
        
        # Ensure connection is established
        if not self._is_connected:
            self.logger.warning("TTS connection lost, reconnecting...")
            await self._connect()
        
        if not self._tts_client:
            raise RuntimeError("TTS provider not initialized. Call initialize() first.")
        
        self._cancelled = False
        
        # Reset state for new synthesis
        self._audio_queue = queue.Queue()
        self._response_done.clear()
        self._session_finished.clear()
        self._current_error = None
        
        # Send text in a thread
        def send_text():
            try:
                self.logger.debug(f"TTS synthesizing: '{text[:50]}...' (len={len(text)})")
                self._tts_client.append_text(text)
                self._tts_client.finish()
            except Exception as e:
                self.logger.error(f"Error sending text: {e}")
                self._current_error = e
                self._session_finished.set()
        
        # Start sending text
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_text)
        
        # Collect audio chunks
        # Wait for session.finished (not just response.done) to ensure all audio is received
        accumulated_audio = []
        start_time = asyncio.get_event_loop().time()
        max_wait_time = 60  # Maximum wait time in seconds
        
        while True:
            # Check cancellation
            if self._cancelled:
                self.logger.info("TTS synthesis cancelled")
                break
            
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > max_wait_time:
                self.logger.warning(f"TTS timeout after {elapsed:.1f}s")
                break
            
            # Wait for session.finished to ensure all audio is received
            # Note: We check session_finished OR (response_done + empty queue + small delay)
            if self._session_finished.is_set() and self._audio_queue.empty():
                break
            
            # Fallback: if response.done and queue is empty for a while, assume complete
            if self._response_done.is_set() and self._audio_queue.empty():
                # Wait a bit more to catch any late audio
                await asyncio.sleep(0.2)
                if self._audio_queue.empty():
                    self.logger.debug("Response done and no more audio, finishing")
                    break
            
            # Try to get audio chunk
            try:
                audio_chunk = self._audio_queue.get(timeout=0.1)
                accumulated_audio.append(audio_chunk)
            except queue.Empty:
                await asyncio.sleep(0.05)
        
        # Check for errors
        if self._current_error and not self._cancelled:
            self.logger.error(f"TTS synthesis failed: {self._current_error}")
        
        # Yield complete audio
        if accumulated_audio and not self._cancelled:
            complete_audio = b''.join(accumulated_audio)
            
            # Resample if needed (Qwen TTS outputs 24kHz)
            if self.sample_rate != self.NATIVE_SAMPLE_RATE:
                complete_audio = self._resample_audio(
                    complete_audio, 
                    from_rate=self.NATIVE_SAMPLE_RATE, 
                    to_rate=self.sample_rate
                )
            
            self.logger.info(f"TTS yielding {len(complete_audio)} bytes of PCM audio ({self.sample_rate}Hz)")
            yield complete_audio
        else:
            self.logger.warning("TTS no audio to yield")
        
        # Reconnect for next synthesis (TTS session ends after finish())
        # This is a workaround for Qwen TTS API behavior
        await self._reconnect_for_next()
    
    async def _reconnect_for_next(self) -> None:
        """Reconnect for the next synthesis request."""
        try:
            # Close current connection
            if self._tts_client:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._tts_client.close)
            
            self._tts_client = None
            self._is_connected = False
            
            # Establish new connection
            await self._connect()
        except Exception as e:
            self.logger.warning(f"Error reconnecting TTS: {e}")
            self._is_connected = False
    
    def _resample_audio(self, audio_data: bytes, from_rate: int, to_rate: int) -> bytes:
        """
        Resample PCM audio data from one sample rate to another.
        
        Args:
            audio_data: PCM audio bytes (16-bit signed, little-endian, mono)
            from_rate: Source sample rate
            to_rate: Target sample rate
            
        Returns:
            Resampled PCM audio bytes
        """
        # Convert bytes to numpy array
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        
        # Calculate resampling ratio
        ratio = to_rate / from_rate
        
        # Simple linear interpolation resampling
        new_length = int(len(audio_array) * ratio)
        indices = np.linspace(0, len(audio_array) - 1, new_length)
        resampled = np.interp(indices, np.arange(len(audio_array)), audio_array.astype(np.float32))
        
        # Convert back to int16
        resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
        
        self.logger.debug(f"Resampled audio from {from_rate}Hz to {to_rate}Hz: {len(audio_data)} -> {len(resampled) * 2} bytes")
        
        return resampled.tobytes()
    
    def cancel(self) -> None:
        """
        Cancel ongoing synthesis.
        
        Sets cancellation flag to stop the synthesis loop.
        """
        self._cancelled = True
        self._session_finished.set()
        self.logger.debug("TTS synthesis cancellation requested")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "model": self.model,
            "voice": self.voice,
            "language_type": self.language_type,
            "sample_rate": self.sample_rate,
            "base_url": self.base_url,
            "is_connected": self._is_connected,
        })
        return config
