"""
Silero VAD provider implementation
"""

import numpy as np
import torch
from typing import Dict, Any
from vixio.providers.vad import VADProvider


class SileroVADProvider(VADProvider):
    """
    Silero VAD provider implementation.
    
    Uses Silero VAD model for voice activity detection.
    """
    
    def __init__(
        self,
        threshold: float = 0.5,
        min_speech_duration_ms: int = 250,
        sample_rate: int = 16000,
        name: str = "SileroVAD"
    ):
        """
        Initialize Silero VAD provider.
        
        Args:
            threshold: Voice detection threshold (0.0-1.0)
            min_speech_duration_ms: Minimum speech duration in ms
            sample_rate: Audio sample rate (default: 16000)
            name: Provider name
        """
        super().__init__(name=name)
        
        self.threshold = threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.sample_rate = sample_rate
        
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
        
        # Internal state
        self._speech_buffer = []
        self._is_speaking = False
    
    def detect(self, audio_data: bytes) -> bool:
        """
        Detect voice activity in audio data.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            
        Returns:
            True if voice detected, False otherwise
        """
        if not audio_data or len(audio_data) == 0:
            return False
        
        try:
            # Convert bytes to numpy array (16-bit PCM)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Convert to float32 normalized to [-1, 1]
            audio_float = audio_array.astype(np.float32) / 32768.0
            
            # Convert to torch tensor
            audio_tensor = torch.from_numpy(audio_float)
            
            # Get speech probability
            speech_prob = self.model(audio_tensor, self.sample_rate).item()
            
            # Detect voice based on threshold
            has_voice = speech_prob > self.threshold
            
            self.logger.debug(f"VAD probability: {speech_prob:.3f}, threshold: {self.threshold}, voice: {has_voice}")
            
            return has_voice
        
        except Exception as e:
            self.logger.error(f"Error in VAD detection: {e}")
            return False
    
    def reset(self) -> None:
        """Reset internal state"""
        self._speech_buffer.clear()
        self._is_speaking = False
        self.logger.debug("VAD state reset")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "threshold": self.threshold,
            "min_speech_duration_ms": self.min_speech_duration_ms,
            "sample_rate": self.sample_rate,
        })
        return config
