"""
Qwen3 ASR Flash Realtime Provider

Realtime speech recognition using Alibaba Cloud's Qwen3-ASR-Flash model
via DashScope WebSocket API.

Reference: https://help.aliyun.com/zh/model-studio/qwen-real-time-speech-recognition

Features:
- Session-level connection reuse (connection established in initialize(), closed in cleanup())
- Low latency streaming recognition
- Automatic reconnection on connection loss

Requires: pip install dashscope>=1.24.8
"""

import asyncio
import base64
import os
import queue
import threading
from typing import Any, AsyncIterator, Dict, List, Optional

from vixio.providers.asr import ASRProvider
from vixio.providers.registry import register_provider


@register_provider("qwen-asr-realtime")
class QwenASRRealtimeProvider(ASRProvider):
    """
    Qwen3 ASR Flash Realtime Provider with session-level connection reuse.
    
    The WebSocket connection is established during initialize() and reused
    for all transcription requests within the session. Connection is closed
    in cleanup().
    
    Features:
    - Session-level connection reuse for low latency
    - Automatic reconnection on connection loss
    - Multiple language support (zh, en, ja, ko, yue, etc.)
    
    Requirements:
        pip install dashscope>=1.24.8
    """
    
    @property
    def is_local(self) -> bool:
        """This is a remote (cloud API) service"""
        return False
    
    @property
    def is_stateful(self) -> bool:
        """ASR is stateful - maintains recognition state"""
        return True
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "asr"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen3-asr-flash-realtime",
        language: str = "zh",
        sample_rate: int = 16000,
        input_audio_format: str = "pcm",
        base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    ):
        """
        Initialize Qwen ASR Realtime provider.
        
        Args:
            api_key: DashScope API key. If None, uses DASHSCOPE_API_KEY env var.
            model: Model name (default: qwen3-asr-flash-realtime)
            language: Recognition language (zh/en/ja/ko/yue/auto)
            sample_rate: Audio sample rate (default: 16000)
            input_audio_format: Audio format (pcm/wav/mp3)
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
        self.language = language
        self.sample_rate = sample_rate
        self.input_audio_format = input_audio_format
        self.base_url = base_url
        
        # Connection state
        self._conversation = None
        self._callback = None
        self._is_connected = False
        self._connection_lock = threading.Lock()
        
        # Current transcription state
        self._result_queue: queue.Queue = queue.Queue()
        self._transcription_complete = threading.Event()
        self._current_error: Optional[Exception] = None
        
        self.logger.info(
            f"Initialized Qwen ASR Realtime provider "
            f"(model={model}, language={language})"
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
                "default": "qwen3-asr-flash-realtime",
                "description": "Model name for ASR"
            },
            "language": {
                "type": "string",
                "default": "zh",
                "description": "Recognition language (zh/en/ja/ko/yue/auto)"
            },
            "sample_rate": {
                "type": "int",
                "default": 16000,
                "description": "Audio sample rate in Hz"
            },
            "input_audio_format": {
                "type": "string",
                "default": "pcm",
                "description": "Input audio format (pcm/wav/mp3)"
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
                "Install with: pip install dashscope>=1.24.8"
            ) from e
        
        # Establish connection in background thread
        await self._connect()
        self.logger.info("Qwen ASR Realtime provider initialized with persistent connection")
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._is_connected:
            return
        
        try:
            from dashscope.audio.qwen_omni import OmniRealtimeConversation, OmniRealtimeCallback
        except ImportError:
            from dashscope.audio.qwen_omni import OmniRealtimeConversation, OmniRealtimeCallback
        
        # Create callback instance
        self._callback = self._create_callback()
        
        # Create and connect
        self._conversation = OmniRealtimeConversation(
            model=self.model,
            url=self.base_url,
            callback=self._callback
        )
        
        # Connect in a thread to avoid blocking
        def connect_sync():
            try:
                self._conversation.connect()
                
                # Configure session for ASR
                try:
                    from dashscope.audio.qwen_omni import MultiModality
                    from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams
                    
                    self._conversation.update_session(
                        output_modalities=[MultiModality.TEXT],
                        enable_input_audio_transcription=True,
                        transcription_params=TranscriptionParams(
                            language=self.language,
                            sample_rate=self.sample_rate,
                            input_audio_format=self.input_audio_format
                        )
                    )
                except ImportError:
                    # Fallback without TranscriptionParams
                    self._conversation.update_session(
                        output_modalities=["TEXT"],
                        enable_input_audio_transcription=True,
                    )
                
                with self._connection_lock:
                    self._is_connected = True
                self.logger.info("ASR WebSocket connection established")
            except Exception as e:
                self.logger.error(f"Failed to establish ASR connection: {e}")
                raise
        
        # Run connection in thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, connect_sync)
    
    def _create_callback(self):
        """Create callback instance for handling WebSocket events."""
        from dashscope.audio.qwen_omni import OmniRealtimeCallback
        
        provider = self  # Capture reference
        
        class ASRCallback(OmniRealtimeCallback):
            def on_open(self):
                provider.logger.debug("ASR WebSocket connection opened")
            
            def on_close(self, close_status_code, close_msg):
                provider.logger.info(f"ASR WebSocket closed: {close_status_code}, {close_msg}")
                with provider._connection_lock:
                    provider._is_connected = False
                # Signal any pending transcription
                provider._transcription_complete.set()
            
            def on_event(self, event):
                try:
                    event_type = event.get("type", "")
                    
                    if event_type == "conversation.item.input_audio_transcription.completed":
                        # Final transcription result
                        transcript = event.get("transcript", "")
                        if transcript:
                            provider._result_queue.put(transcript)
                            provider.logger.info(f"ASR transcription completed: {transcript}")
                        # Signal completion
                        provider._transcription_complete.set()
                    
                    elif event_type == "conversation.item.input_audio_transcription.text":
                        # Intermediate result (streaming)
                        pass
                    
                    elif event_type == "input_audio_buffer.speech_started":
                        provider.logger.debug("Speech started detected")
                    
                    elif event_type == "input_audio_buffer.speech_stopped":
                        provider.logger.debug("Speech stopped detected")
                    
                    elif event_type == "input_audio_buffer.committed":
                        provider.logger.debug("Audio buffer committed")
                    
                    elif event_type == "error":
                        error_msg = event.get("error", {}).get("message", "Unknown error")
                        provider._current_error = Exception(f"ASR error: {error_msg}")
                        provider.logger.error(f"ASR error: {error_msg}")
                        provider._transcription_complete.set()
                    
                    elif event_type == "session.created":
                        provider.logger.debug(f"Session created: {event.get('session', {}).get('id', '')}")
                    
                    elif event_type == "session.updated":
                        provider.logger.debug("Session updated")
                    
                    elif event_type == "response.done":
                        provider.logger.debug("Response done")
                        provider._transcription_complete.set()
                
                except Exception as e:
                    provider.logger.error(f"Error processing ASR event: {e}")
                    provider._current_error = e
                    provider._transcription_complete.set()
            
            def on_error(self, error):
                provider.logger.error(f"ASR WebSocket error: {error}")
                provider._current_error = Exception(str(error))
                with provider._connection_lock:
                    provider._is_connected = False
                provider._transcription_complete.set()
        
        return ASRCallback()
    
    async def cleanup(self) -> None:
        """Cleanup provider resources and close connection."""
        if self._conversation:
            try:
                # Close in thread to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._conversation.close)
            except Exception as e:
                self.logger.warning(f"Error closing ASR connection: {e}")
        
        self._conversation = None
        self._callback = None
        self._is_connected = False
        self.logger.info("Qwen ASR Realtime provider cleaned up")
    
    async def transcribe_stream(self, audio_chunks: List[bytes]) -> AsyncIterator[str]:
        """
        Transcribe audio chunks to text using the persistent connection.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            
        Yields:
            Text segments as recognition progresses
        """
        # Ensure connection is established
        if not self._is_connected:
            self.logger.warning("ASR connection lost, reconnecting...")
            await self._connect()
        
        if not self._conversation:
            raise RuntimeError("ASR provider not initialized. Call initialize() first.")
        
        # Concatenate all audio chunks
        audio_data = b''.join(audio_chunks)
        
        if not audio_data:
            return
        
        # Reset state for new transcription
        self._result_queue = queue.Queue()
        self._transcription_complete.clear()
        self._current_error = None
        
        # Send audio in a thread
        def send_audio():
            try:
                # Send audio data in chunks
                chunk_size = 3200  # 100ms of 16kHz 16-bit mono audio
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i + chunk_size]
                    audio_b64 = base64.b64encode(chunk).decode('ascii')
                    self._conversation.append_audio(audio_b64)
                
                # Send a small silence to help trigger transcription
                silence_data = bytes(chunk_size)
                for _ in range(5):
                    audio_b64 = base64.b64encode(silence_data).decode('ascii')
                    self._conversation.append_audio(audio_b64)
                
                self.logger.debug(f"Sent {len(audio_data)} bytes of audio")
            except Exception as e:
                self.logger.error(f"Error sending audio: {e}")
                self._current_error = e
                self._transcription_complete.set()
        
        # Start sending audio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_audio)
        
        # Wait for results with timeout
        start_time = asyncio.get_event_loop().time()
        max_wait_time = 30  # Maximum wait time in seconds
        
        while True:
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > max_wait_time:
                self.logger.warning(f"ASR timeout after {elapsed:.1f}s")
                break
            
            # Check if transcription complete and queue is empty
            if self._transcription_complete.is_set() and self._result_queue.empty():
                break
            
            # Try to get result
            try:
                result = self._result_queue.get(timeout=0.1)
                self.logger.debug(f"Yielding ASR result: {result[:50]}...")
                yield result
            except queue.Empty:
                await asyncio.sleep(0.05)
        
        # Check for errors
        if self._current_error:
            self.logger.error(f"ASR transcription failed: {self._current_error}")
    
    async def reset(self) -> None:
        """Reset ASR state for new turn."""
        self._result_queue = queue.Queue()
        self._transcription_complete.clear()
        self._current_error = None
        self.logger.debug("ASR state reset")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "model": self.model,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "input_audio_format": self.input_audio_format,
            "base_url": self.base_url,
            "is_connected": self._is_connected,
        })
        return config
