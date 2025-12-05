"""
Local Silero VAD Provider (In-Process Inference)

This provider runs VAD inference directly in the current process
without requiring a separate gRPC service.

Requires: pip install vixio[silero-vad-local]
"""

import threading
from collections import deque
from typing import Dict, Any
import numpy as np

from vixio.providers.vad import VADProvider, VADEvent
from vixio.providers.registry import register_provider


@register_provider("silero-vad-local")
class LocalSileroVADInProcessProvider(VADProvider):
    """
    Local Silero VAD Provider (In-Process Inference).
    
    Runs VAD inference directly in the current process using ONNX runtime.
    Suitable for development and single-instance deployments.
    
    Features:
    - No external service dependency
    - GPU acceleration when available (CUDA)
    - Per-session state isolation
    - Low latency (no network overhead)
    
    Requirements:
        pip install vixio[silero-vad-local]
        # or manually: pip install silero-vad onnxruntime-gpu
    """
    
    @property
    def is_local(self) -> bool:
        """This is a local (in-process) service"""
        return True
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "vad"
    
    def __init__(
        self,
        threshold: float = 0.35,
        threshold_low: float = 0.15,
        frame_window_threshold: int = 8,
        use_gpu: bool = True
    ):
        """
        Initialize Local Silero VAD provider (in-process).
        
        Args:
            threshold: Voice detection threshold (0.0-1.0)
            threshold_low: Lower threshold for hysteresis
            frame_window_threshold: Minimum frames to confirm voice
            use_gpu: Whether to use GPU acceleration if available
        """
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.frame_window_threshold = frame_window_threshold
        self.use_gpu = use_gpu
        
        # ONNX session (shared, initialized lazily)
        self._onnx_session = None
        self._inference_lock = threading.Lock()
        
        # Per-instance state
        self._state: np.ndarray = None
        self._context: np.ndarray = None
        self._pcm_buffer: bytearray = None
        self._voice_window: deque = None
        self._last_is_voice: bool = False
        self._is_speaking: bool = False
        
        self.logger.info(
            f"Initialized Local Silero VAD (in-process) "
            f"(threshold={threshold}, use_gpu={use_gpu})"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "threshold": {
                "type": "float",
                "default": 0.35,
                "description": "Voice detection threshold (0.0-1.0). Lower = more sensitive."
            },
            "threshold_low": {
                "type": "float",
                "default": 0.15,
                "description": "Lower threshold for hysteresis."
            },
            "frame_window_threshold": {
                "type": "int",
                "default": 8,
                "description": "Minimum frames to confirm voice/silence."
            },
            "use_gpu": {
                "type": "bool",
                "default": True,
                "description": "Whether to use GPU acceleration if available."
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider: load ONNX model and setup state.
        """
        try:
            from silero_vad import load_silero_vad
            import onnxruntime
        except ImportError as e:
            raise ImportError(
                "silero-vad or onnxruntime not installed. "
                "Install with: pip install vixio[silero-vad-local]"
            ) from e
        
        # Load ONNX model
        onnx_wrapper = load_silero_vad(onnx=True)
        
        # Configure providers based on availability
        available_providers = onnxruntime.get_available_providers()
        self.logger.info(f"Available ONNX providers: {available_providers}")
        
        if self.use_gpu and "CUDAExecutionProvider" in available_providers:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            self.logger.info("Using CUDA for VAD inference (GPU)")
        else:
            providers = ["CPUExecutionProvider"]
            self.logger.info("Using CPU for VAD inference")
        
        # Session options
        sess_options = onnxruntime.SessionOptions()
        sess_options.inter_op_num_threads = 1
        sess_options.intra_op_num_threads = 1
        
        # Create session
        model_path = onnx_wrapper.session._model_path
        self._onnx_session = onnxruntime.InferenceSession(
            model_path,
            providers=providers,
            sess_options=sess_options
        )
        
        self.logger.info(f"ONNX session created with providers: {self._onnx_session.get_providers()}")
        
        # Initialize state
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self._pcm_buffer = bytearray()
        self._voice_window = deque(maxlen=30)
        self._last_is_voice = False
        self._is_speaking = False
        
        self.logger.info("VAD provider initialized (in-process)")
    
    async def detect(
        self,
        audio_data: bytes,
        event: VADEvent = VADEvent.CHUNK
    ) -> bool:
        """
        Detect voice activity in audio data.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            event: VAD event type
            
        Returns:
            True if voice detected, False otherwise
        """
        if self._onnx_session is None:
            raise RuntimeError("VAD provider not initialized. Call initialize() first.")
        
        # Handle events
        if event == VADEvent.START:
            self.logger.debug("VAD_START event received")
        elif event == VADEvent.END:
            # Clear buffers
            self._pcm_buffer.clear()
            self._voice_window.clear()
            self._last_is_voice = False
            self.logger.debug("VAD_END: buffers cleared")
            return False
        
        # Process audio
        return await self._process_audio(audio_data)
    
    async def _process_audio(self, audio_data: bytes) -> bool:
        """
        Process audio data through ONNX model.
        
        Args:
            audio_data: PCM audio bytes
            
        Returns:
            True if voice detected in batch
        """
        if not audio_data:
            return False
        
        # Add to buffer
        self._pcm_buffer.extend(audio_data)
        
        # Process in 512-sample frames (16-bit = 1024 bytes)
        has_voice_in_batch = False
        frame_byte_size = 512 * 2
        
        while len(self._pcm_buffer) >= frame_byte_size:
            # Extract frame
            chunk = bytes(self._pcm_buffer[:frame_byte_size])
            self._pcm_buffer = self._pcm_buffer[frame_byte_size:]
            
            # Convert to numpy
            audio_array = np.frombuffer(chunk, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            audio_input = np.expand_dims(audio_float, 0)
            
            # Concatenate context with audio
            context_size = 64
            audio_with_context = np.concatenate((self._context, audio_input), axis=1)
            
            # Run inference
            with self._inference_lock:
                ort_inputs = {
                    'input': audio_with_context.astype(np.float32),
                    'state': self._state,
                    'sr': np.array(16000, dtype=np.int64)
                }
                ort_outputs = self._onnx_session.run(None, ort_inputs)
                speech_prob_array, updated_state = ort_outputs
                
                self._state = updated_state
                speech_prob = speech_prob_array[0, 0]
            
            # Update context
            self._context = audio_with_context[..., -context_size:]
            
            # Dual-threshold detection with hysteresis
            if speech_prob >= self.threshold:
                is_voice = True
            elif speech_prob <= self.threshold_low:
                is_voice = False
            else:
                is_voice = self._last_is_voice
            
            self._last_is_voice = is_voice
            self._voice_window.append(is_voice)
            
            # Check if enough frames have voice
            voice_count = sum(1 for v in self._voice_window if v)
            if voice_count >= self.frame_window_threshold:
                has_voice_in_batch = True
                self._is_speaking = True
        
        return has_voice_in_batch
    
    async def reset(self) -> None:
        """Reset VAD state"""
        self._pcm_buffer.clear()
        self._voice_window.clear()
        self._last_is_voice = False
        self._is_speaking = False
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self.logger.debug("VAD state reset")
    
    async def cleanup(self) -> None:
        """Cleanup provider resources"""
        self._onnx_session = None
        self._state = None
        self._context = None
        self._pcm_buffer = None
        self._voice_window = None
        self.logger.info("VAD provider cleaned up")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "threshold": self.threshold,
            "threshold_low": self.threshold_low,
            "frame_window_threshold": self.frame_window_threshold,
            "use_gpu": self.use_gpu,
        })
        return config

