"""
Sherpa-ONNX local ASR provider implementation
"""

import numpy as np
from typing import List, Dict, Any
from providers.asr import ASRProvider


class SherpaOnnxLocalProvider(ASRProvider):
    """
    Sherpa-ONNX local ASR provider.
    
    Uses Sherpa-ONNX for local speech recognition.
    """
    
    def __init__(
        self,
        model_path: str,
        tokens_path: str = None,
        sample_rate: int = 16000,
        name: str = "SherpaOnnxLocal"
    ):
        """
        Initialize Sherpa-ONNX ASR provider.
        
        Args:
            model_path: Path to ONNX model directory
            tokens_path: Path to tokens file (optional, auto-detected from model_path)
            sample_rate: Audio sample rate (default: 16000)
            name: Provider name
        """
        super().__init__(name=name)
        
        self.model_path = model_path
        self.sample_rate = sample_rate
        
        # Auto-detect tokens path if not provided
        if tokens_path is None:
            import os
            tokens_path = os.path.join(model_path, "tokens.txt")
        self.tokens_path = tokens_path
        
        # Load Sherpa-ONNX model
        try:
            import sherpa_onnx
            import os
            
            # Build model file path
            model_file = os.path.join(model_path, "model.int8.onnx")
            
            # Create offline recognizer for SenseVoice
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model_file,
                tokens=tokens_path,
                num_threads=2,
                sample_rate=self.sample_rate,
                feature_dim=80,
                use_itn=True,
                debug=False,
            )
            self.logger.info(f"Loaded Sherpa-ONNX SenseVoice model from {model_path}")
        
        except Exception as e:
            self.logger.error(f"Failed to load Sherpa-ONNX model: {e}")
            raise
    
    async def transcribe(self, audio_chunks: List[bytes]) -> str:
        """
        Transcribe audio chunks to text.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            
        Returns:
            Transcribed text
        """
        if not audio_chunks:
            return ""
        
        try:
            # Concatenate all audio chunks
            audio_data = b''.join(audio_chunks)
            
            # Convert bytes to numpy array (16-bit PCM)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Convert to float32 normalized to [-1, 1]
            audio_float = audio_array.astype(np.float32) / 32768.0
            
            # Create offline stream
            stream = self.recognizer.create_stream()
            stream.accept_waveform(self.sample_rate, audio_float)
            
            # Decode offline
            self.recognizer.decode_stream(stream)
            
            # Get result
            result = stream.result.text.strip()
            
            self.logger.info(f"ASR transcribed: '{result}'")
            
            return result
        
        except Exception as e:
            self.logger.error(f"Error in ASR transcription: {e}", exc_info=True)
            return ""
    
    def reset(self) -> None:
        """Reset internal state"""
        self.logger.debug("ASR state reset")
    
    def cleanup(self) -> None:
        """
        Cleanup resources and free model memory.
        
        Explicitly releases ONNX recognizer to prevent memory leaks.
        """
        try:
            if hasattr(self, 'recognizer') and self.recognizer is not None:
                # Clear recognizer reference
                del self.recognizer
                self.recognizer = None
                self.logger.debug("ASR model resources released")
        except Exception as e:
            self.logger.error(f"Error during ASR cleanup: {e}")
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "model_path": self.model_path,
            "tokens_path": self.tokens_path,
            "sample_rate": self.sample_rate,
        })
        return config
