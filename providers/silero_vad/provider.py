"""
Silero VAD provider implementation
"""

import numpy as np
import torch
from typing import Dict, Any
from collections import deque
from providers.vad import VADProvider


class SileroVADProvider(VADProvider):
    """
    Silero VAD provider implementation.
    
    Uses Silero VAD model for voice activity detection.
    """
    
    def __init__(
        self,
        threshold: float = 0.5,
        threshold_low: float = 0.2,
        min_speech_duration_ms: int = 250,
        sample_rate: int = 16000,
        frame_window_threshold: int = 3,
        vad_frame_size: int = 512,
        name: str = "SileroVAD"
    ):
        """
        Initialize Silero VAD provider.
        
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
        
        # Load Silero VAD model
        try:
            # Use silero-vad package
            from silero_vad import load_silero_vad, get_speech_timestamps
            self.model = load_silero_vad()
            self.get_speech_timestamps = get_speech_timestamps
            self.logger.info(f"Loaded Silero VAD model with threshold={threshold}")
        except Exception as e:
            self.logger.error(f"Failed to load Silero VAD model: {e}")
            raise
        
        # PCM buffer for accumulating audio before VAD detection
        # Accumulate until we have at least vad_frame_size samples
        self._pcm_buffer = bytearray()
        
        # Voice detection state
        self._voice_window = deque(maxlen=10)  # Sliding window for voice frames
        self._last_is_voice = False
        self._is_speaking = False
    
    def detect(self, audio_data: bytes) -> bool:
        """
        Detect voice activity in PCM audio data.
        
        Important: This method expects PCM audio (16-bit signed integer, little-endian).
        Transport layers are responsible for format conversion (e.g., Opus -> PCM).
        
        Args:
            audio_data: PCM audio bytes (16-bit signed integer, little-endian)
            
        Returns:
            True if voice detected, False otherwise
        """
        if not audio_data or len(audio_data) == 0:
            return False
        
        try:
            # Add PCM data to buffer
            self._pcm_buffer.extend(audio_data)
            
            # Process buffer in chunks of vad_frame_size samples
            has_voice_in_batch = False
            frame_byte_size = self.vad_frame_size * 2  # 16-bit = 2 bytes per sample
            
            while len(self._pcm_buffer) >= frame_byte_size:
                # Extract frame from buffer
                chunk = bytes(self._pcm_buffer[:frame_byte_size])
                self._pcm_buffer = self._pcm_buffer[frame_byte_size:]
                
                # Convert to numpy array (16-bit PCM)
                audio_array = np.frombuffer(chunk, dtype=np.int16)
                
                # Convert to float32 normalized to [-1, 1]
                audio_float = audio_array.astype(np.float32) / 32768.0
                
                # Convert to torch tensor
                audio_tensor = torch.from_numpy(audio_float)
                
                # Get speech probability
                speech_prob = self.model(audio_tensor, self.sample_rate).item()
                
                # Dual threshold detection
                if speech_prob >= self.threshold:
                    is_voice = True
                elif speech_prob <= self.threshold_low:
                    is_voice = False
                else:
                    # Hysteresis: maintain previous state
                    is_voice = self._last_is_voice
                
                self._last_is_voice = is_voice
                
                # Update sliding window
                self._voice_window.append(is_voice)
                
                # Check if enough frames have voice
                voice_count = sum(1 for v in self._voice_window if v)
                has_voice_in_batch = voice_count >= self.frame_window_threshold
                
                self.logger.debug(
                    f"VAD prob: {speech_prob:.3f}, voice: {is_voice}, "
                    f"window: {voice_count}/{len(self._voice_window)}, "
                    f"has_voice: {has_voice_in_batch}"
                )
            
            # Update speaking state
            if has_voice_in_batch:
                self._is_speaking = True
            
            return has_voice_in_batch
        
        except Exception as e:
            self.logger.error(f"Error in VAD detection: {e}")
            return self._is_speaking
    
    def reset(self) -> None:
        """Reset internal state"""
        self._pcm_buffer.clear()
        self._voice_window.clear()
        self._last_is_voice = False
        self._is_speaking = False
        self.logger.debug("VAD state reset")
    
    def cleanup(self) -> None:
        """
        Cleanup resources and free model memory.
        
        Explicitly releases Torch model to prevent memory leaks.
        """
        try:
            if hasattr(self, 'model') and self.model is not None:
                # Clear model reference
                del self.model
                self.model = None
                
                # Clear buffers
                self._pcm_buffer.clear()
                self._voice_window.clear()
                
                # Force CUDA cache cleanup if using GPU
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                
                self.logger.debug("VAD model resources released")
        except Exception as e:
            self.logger.error(f"Error during VAD cleanup: {e}")
    
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
        })
        return config
