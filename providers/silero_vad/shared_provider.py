"""
Shared model Silero VAD provider implementation.

This provider shares the Torch model weights across multiple instances,
but maintains isolated state per instance. This reduces memory usage and
initialization time while ensuring proper session isolation.
"""

import numpy as np
import torch
from typing import Dict, Any, Optional
from collections import deque
from providers.vad import VADProvider
from loguru import logger as global_logger


class SharedModelSileroVADProvider(VADProvider):
    """
    Silero VAD provider with shared model weights.
    
    Key Features:
    - Model weights are shared across all instances (loaded once)
    - Each instance has independent state (buffers, windows)
    - Thread-safe and async-safe
    - Significant memory savings for multi-session scenarios
    
    Memory Comparison:
    - Regular provider: 50MB × N sessions = 500MB for 10 sessions
    - Shared provider: 50MB + 1MB × N sessions = 60MB for 10 sessions
    """
    
    # Class-level shared resources
    _shared_model: Optional[torch.nn.Module] = None
    _shared_get_speech_timestamps = None
    _model_lock = None  # For thread-safe initialization
    _inference_lock = None  # For thread-safe model inference
    
    def __init__(
        self,
        threshold: float = 0.5,
        threshold_low: float = 0.2,
        min_speech_duration_ms: int = 250,
        sample_rate: int = 16000,
        frame_window_threshold: int = 3,
        vad_frame_size: int = 512,
        name: str = "SharedSileroVAD"
    ):
        """
        Initialize Silero VAD provider with shared model.
        
        Args:
            threshold: Voice detection threshold (0.0-1.0)
            threshold_low: Lower threshold for voice detection
            min_speech_duration_ms: Minimum speech duration in ms
            sample_rate: Audio sample rate (default: 16000)
            frame_window_threshold: Minimum frames to confirm voice
            vad_frame_size: VAD processing frame size in samples (default: 512)
            name: Provider name
        """
        super().__init__(name=name)
        
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.min_speech_duration_ms = min_speech_duration_ms
        self.sample_rate = sample_rate
        self.frame_window_threshold = frame_window_threshold
        self.vad_frame_size = vad_frame_size
        
        # Initialize shared model (only once)
        self._ensure_model_loaded()
        
        # ✅ Instance-specific state (isolated per session)
        self._pcm_buffer = bytearray()
        self._voice_window = deque(maxlen=10)
        self._last_is_voice = False
        self._is_speaking = False
        
        self.logger.debug(f"Initialized with shared model (total instances using model)")
    
    @classmethod
    def _ensure_model_loaded(cls):
        """
        Ensure shared model is loaded (thread-safe).
        
        This method is called by __init__ and loads the model only once.
        """
        # Initialize locks if needed
        if cls._model_lock is None:
            import threading
            cls._model_lock = threading.Lock()
            cls._inference_lock = threading.Lock()  # Lock for thread-safe inference
        
        # Load model if not already loaded
        if cls._shared_model is None:
            with cls._model_lock:
                # Double-check after acquiring lock
                if cls._shared_model is None:
                    try:
                        from silero_vad import load_silero_vad, get_speech_timestamps
                        cls._shared_model = load_silero_vad()
                        cls._shared_get_speech_timestamps = get_speech_timestamps
                        global_logger.info("Loaded shared Silero VAD model (will be reused by all instances)")
                    except Exception as e:
                        global_logger.error(f"Failed to load shared Silero VAD model: {e}")
                        raise
    
    def detect(self, audio_data: bytes) -> bool:
        """
        Detect voice activity in PCM audio data.
        
        Uses shared model but instance-specific state.
        
        Args:
            audio_data: PCM audio bytes (16-bit signed integer, little-endian)
            
        Returns:
            True if voice detected, False otherwise
        """
        if not audio_data or len(audio_data) == 0:
            return False
        
        try:
            # Add PCM data to instance buffer
            self._pcm_buffer.extend(audio_data)
            
            # Process buffer in chunks
            has_voice_in_batch = False
            frame_byte_size = self.vad_frame_size * 2  # 16-bit = 2 bytes per sample
            
            while len(self._pcm_buffer) >= frame_byte_size:
                # Extract frame from buffer
                chunk = bytes(self._pcm_buffer[:frame_byte_size])
                self._pcm_buffer = self._pcm_buffer[frame_byte_size:]
                
                # Convert to numpy array
                audio_array = np.frombuffer(chunk, dtype=np.int16)
                audio_float = audio_array.astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_float)
                
                # ✅ Use shared model with thread-safe inference lock
                # PyTorch model inference is not thread-safe, must use lock
                with self._inference_lock:
                    with torch.no_grad():
                        speech_prob = self._shared_model(audio_tensor, self.sample_rate).item()
                
                # Dual threshold detection using instance state
                if speech_prob >= self.threshold:
                    is_voice = True
                elif speech_prob <= self.threshold_low:
                    is_voice = False
                else:
                    is_voice = self._last_is_voice
                
                self._last_is_voice = is_voice
                self._voice_window.append(is_voice)
                
                voice_count = sum(1 for v in self._voice_window if v)
                has_voice_in_batch = voice_count >= self.frame_window_threshold
                
                self.logger.debug(
                    f"VAD prob: {speech_prob:.3f}, voice: {is_voice}, "
                    f"window: {voice_count}/{len(self._voice_window)}, "
                    f"has_voice: {has_voice_in_batch}"
                )
            
            # Update instance speaking state
            if has_voice_in_batch:
                self._is_speaking = True
            
            return has_voice_in_batch
        
        except Exception as e:
            self.logger.error(f"Error in VAD detection: {e}")
            return self._is_speaking
    
    def reset(self) -> None:
        """Reset instance state (model remains loaded)."""
        self._pcm_buffer.clear()
        self._voice_window.clear()
        self._last_is_voice = False
        self._is_speaking = False
        self.logger.debug("VAD instance state reset (model shared)")
    
    def cleanup(self) -> None:
        """
        Cleanup instance resources.
        
        Note: Only cleans up instance state, not the shared model.
        The shared model remains loaded for other instances to use.
        """
        try:
            # Clear instance buffers
            self._pcm_buffer.clear()
            self._voice_window.clear()
            
            self.logger.debug("VAD instance cleaned up (shared model retained)")
        except Exception as e:
            self.logger.error(f"Error during VAD cleanup: {e}")
    
    @classmethod
    def unload_shared_model(cls):
        """
        Unload shared model (call only on application shutdown).
        
        WARNING: This will affect ALL instances. Only call when shutting down
        the entire application, not individual sessions.
        """
        if cls._shared_model is not None:
            del cls._shared_model
            cls._shared_model = None
            
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            global_logger.info("Shared Silero VAD model unloaded")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "threshold": self.threshold,
            "threshold_low": self.threshold_low,
            "min_speech_duration_ms": self.min_speech_duration_ms,
            "sample_rate": self.sample_rate,
            "frame_window_threshold": self.frame_window_threshold,
            "vad_frame_size": self.vad_frame_size,
            "model_sharing": "enabled",  # Indicator that this uses shared model
        })
        return config

