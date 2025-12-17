"""
Audio format conversion utilities

Provides common audio format conversions for Transport and Provider layers.
"""

from __future__ import annotations

from typing import Optional
from loguru import logger
import numpy as np

# Opus codec support
try:
    import opuslib_next
    OPUS_AVAILABLE = True
except ImportError:
    opuslib_next = None  # type: ignore
    OPUS_AVAILABLE = False
    logger.error("opuslib_next not available, Opus codec disabled")
    logger.warning("if you are in windows,please install opus from https://github.com/ShiftMediaProject/opus/releases")
    logger.warning("if you are in linux,please install opus with sudo apt-get install libopus-dev")

# Resampling support (try scipy first, fallback to numpy)
try:
    from scipy import signal as scipy_signal
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_signal = None  # type: ignore
    SCIPY_AVAILABLE = False
    logger.warning("scipy not available, will use numpy-based resampling (lower quality)")


class OpusCodec:
    """
    Opus audio codec for encoding/decoding.
    
    Default parameters match Xiaozhi protocol:
    - Sample rate: 16kHz
    - Channels: 1 (mono)
    - Frame duration: 60ms (960 samples)
    """
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_duration_ms: int = 60
    ):
        """
        Initialize Opus codec.
        
        Args:
            sample_rate: Audio sample rate in Hz
            channels: Number of audio channels
            frame_duration_ms: Frame duration in milliseconds
        """
        if not OPUS_AVAILABLE:
            raise RuntimeError("opuslib_next not available, cannot use Opus codec")
        
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        
        # Bypass mode (for PCM passthrough)
        self.bypass = False
        
        # Initialize decoder and encoder
        self._decoder: Optional[opuslib_next.Decoder] = None
        self._encoder: Optional[opuslib_next.Encoder] = None
    
    @property
    def decoder(self) -> opuslib_next.Decoder:
        """Get or create decoder instance."""
        if self._decoder is None:
            self._decoder = opuslib_next.Decoder(self.sample_rate, self.channels)
            logger.debug(f"Created Opus decoder: {self.sample_rate}Hz, {self.channels}ch")
        return self._decoder
    
    @property
    def encoder(self) -> opuslib_next.Encoder:
        """Get or create encoder instance."""
        if self._encoder is None:
            self._encoder = opuslib_next.Encoder(
                self.sample_rate,
                self.channels,
                opuslib_next.APPLICATION_AUDIO
            )
            logger.debug(f"Created Opus encoder: {self.sample_rate}Hz, {self.channels}ch")
        return self._encoder
    
    def decode(self, opus_data: bytes) -> bytes:
        """
        Decode Opus audio to PCM.
        
        Args:
            opus_data: Opus encoded audio bytes
            
        Returns:
            PCM audio bytes (16-bit signed integer, little-endian)
            
        Raises:
            opuslib_next.OpusError: If decoding fails
        """
        if not opus_data:
            return b''
        
        if self.bypass:
            return opus_data
        
        try:
            pcm_data = self.decoder.decode(opus_data, self.frame_size)
            return pcm_data
        except Exception as e:
            logger.error(f"Opus decode error: {e}")
            raise
    
    def encode(self, pcm_data: bytes) -> bytes:
        """
        Encode PCM audio to Opus.
        
        Args:
            pcm_data: PCM audio bytes (16-bit signed integer, little-endian)
            
        Returns:
            Opus encoded audio bytes
            
        Raises:
            opuslib_next.OpusError: If encoding fails
        """
        if not pcm_data:
            return b''
            
        if self.bypass:
            return pcm_data
        
        # Ensure we have exactly frame_size samples
        expected_bytes = self.frame_size * 2  # 16-bit = 2 bytes per sample
        if len(pcm_data) < expected_bytes:
            # Pad with zeros if not enough data
            pcm_data = pcm_data + b'\x00' * (expected_bytes - len(pcm_data))
            logger.warning(f"PCM data too short ({len(pcm_data)} bytes), padding with zeros to {expected_bytes} bytes")
        elif len(pcm_data) > expected_bytes:
            # Truncate if too much data
            logger.warning(f"PCM data too long ({len(pcm_data)} bytes), truncating to {expected_bytes}")
            pcm_data = pcm_data[:expected_bytes]
        
        try:
            opus_data = self.encoder.encode(pcm_data, self.frame_size)
            return opus_data
        except Exception as e:
            logger.error(f"Opus encode error: {e}")
            raise
    
    def decode_multiple(self, opus_packets: list[bytes]) -> bytes:
        """
        Decode multiple Opus packets to PCM.
        
        Args:
            opus_packets: List of Opus encoded audio packets
            
        Returns:
            Concatenated PCM audio bytes
        """
        pcm_chunks = []
        for packet in opus_packets:
            try:
                pcm_chunk = self.decode(packet)
                pcm_chunks.append(pcm_chunk)
            except Exception as e:
                logger.warning(f"Failed to decode Opus packet: {e}")
                continue
        
        return b''.join(pcm_chunks)
    
    def encode_multiple(self, pcm_data: bytes) -> list[bytes]:
        """
        Encode PCM audio to multiple Opus packets.
        
        Args:
            pcm_data: PCM audio bytes
            
        Returns:
            List of Opus encoded audio packets
        """
        opus_packets = []
        frame_bytes = self.frame_size * 2  # 16-bit = 2 bytes per sample
        
        for i in range(0, len(pcm_data), frame_bytes):
            chunk = pcm_data[i:i + frame_bytes]
            try:
                opus_packet = self.encode(chunk)
                opus_packets.append(opus_packet)
            except Exception as e:
                logger.warning(f"Failed to encode PCM chunk: {e}")
                continue
        
        return opus_packets


# Singleton instances for common configurations
_opus_codec_16k_mono: Optional[OpusCodec] = None


def get_opus_codec(
    sample_rate: int = 16000,
    channels: int = 1,
    frame_duration_ms: int = 60
) -> OpusCodec:
    """
    Get Opus codec instance.
    
    Args:
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels  
        frame_duration_ms: Frame duration in milliseconds
        
    Returns:
        New OpusCodec instance
    """
    # Create new instance for each session to ensure isolation
    # (Decoder state and bypass flag should not be shared)
    return OpusCodec(sample_rate, channels, frame_duration_ms)


def opus_to_pcm(opus_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """
    Quick helper: Decode Opus to PCM.
    
    Args:
        opus_data: Opus encoded audio bytes
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels
        
    Returns:
        PCM audio bytes
    """
    codec = get_opus_codec(sample_rate, channels)
    return codec.decode(opus_data)


def pcm_to_opus(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """
    Quick helper: Encode PCM to Opus.
    
    Args:
        pcm_data: PCM audio bytes
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels
        
    Returns:
        Opus encoded audio bytes
    """
    codec = get_opus_codec(sample_rate, channels)
    return codec.encode(pcm_data)


# MP3 codec support
try:
    from pydub import AudioSegment
    from io import BytesIO
    MP3_AVAILABLE = True
except ImportError:
    MP3_AVAILABLE = False
    logger.warning("pydub not available, MP3 codec disabled")


def mp3_to_pcm(mp3_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """
    Convert MP3 audio to PCM.
    
    Args:
        mp3_data: MP3 encoded audio bytes
        sample_rate: Target sample rate in Hz
        channels: Target number of channels
        
    Returns:
        PCM audio bytes (16-bit signed, little-endian)
    """
    if not MP3_AVAILABLE:
        raise RuntimeError("pydub not available, cannot convert MP3")
    
    if not mp3_data:
        return b''
    
    try:
        # Load MP3 from bytes
        audio = AudioSegment.from_file(BytesIO(mp3_data), format="mp3")
        
        # Convert to target format
        audio = audio.set_frame_rate(sample_rate).set_channels(channels).set_sample_width(2)
        
        # Get raw PCM data
        pcm_data = audio.raw_data
        
        return pcm_data
    except Exception as e:
        logger.error(f"MP3 to PCM conversion error: {e}")
        raise


def pcm_to_mp3(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """
    Convert PCM audio to MP3.
    
    Args:
        pcm_data: PCM audio bytes (16-bit signed, little-endian)
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels
        
    Returns:
        MP3 encoded audio bytes
    """
    if not MP3_AVAILABLE:
        raise RuntimeError("pydub not available, cannot convert to MP3")
    
    if not pcm_data:
        return b''
    
    try:
        # Create AudioSegment from PCM
        audio = AudioSegment(
            data=pcm_data,
            sample_width=2,  # 16-bit
            frame_rate=sample_rate,
            channels=channels
        )
        
        # Export as MP3
        mp3_buffer = BytesIO()
        audio.export(mp3_buffer, format="mp3")
        mp3_data = mp3_buffer.getvalue()
        
        return mp3_data
    except Exception as e:
        logger.error(f"PCM to MP3 conversion error: {e}")
        raise


def resample_pcm(
    pcm_data: bytes,
    from_rate: int,
    to_rate: int,
    channels: int = 1,
    sample_width: int = 2
) -> bytes:
    """
    Resample PCM audio data from one sample rate to another.
    
    Uses scipy.signal.resample if available (high quality), otherwise falls back
    to numpy linear interpolation (lower quality but no extra dependency).
    
    Args:
        pcm_data: Input PCM audio bytes (16-bit signed little-endian)
        from_rate: Source sample rate in Hz
        to_rate: Target sample rate in Hz
        channels: Number of audio channels (default: 1)
        sample_width: Bytes per sample (default: 2 for 16-bit)
    
    Returns:
        Resampled PCM audio bytes
    
    Example:
        # Resample 24kHz audio to 16kHz
        pcm_16k = resample_pcm(pcm_24k, from_rate=24000, to_rate=16000)
    """
    if not pcm_data:
        return b''
    
    # No resampling needed
    if from_rate == to_rate:
        return pcm_data
    
    try:
        # Convert bytes to numpy array
        dtype = np.int16 if sample_width == 2 else np.int8
        audio_array = np.frombuffer(pcm_data, dtype=dtype)
        
        # Handle multi-channel audio
        if channels > 1:
            audio_array = audio_array.reshape(-1, channels)
        
        # Calculate target length
        num_samples = len(audio_array)
        target_length = int(num_samples * to_rate / from_rate)
        
        # Resample using scipy (high quality) or numpy (fallback)
        if SCIPY_AVAILABLE:
            # scipy.signal.resample uses FFT-based resampling (high quality)
            resampled = scipy_signal.resample(audio_array, target_length, axis=0)
        else:
            # Fallback: linear interpolation using numpy
            x_old = np.linspace(0, num_samples - 1, num_samples)
            x_new = np.linspace(0, num_samples - 1, target_length)
            if channels > 1:
                resampled = np.zeros((target_length, channels), dtype=np.float64)
                for ch in range(channels):
                    resampled[:, ch] = np.interp(x_new, x_old, audio_array[:, ch])
            else:
                resampled = np.interp(x_new, x_old, audio_array)
        
        # Clip and convert back to original dtype
        resampled = np.clip(resampled, np.iinfo(dtype).min, np.iinfo(dtype).max)
        resampled = resampled.astype(dtype)
        
        return resampled.tobytes()
    
    except Exception as e:
        logger.error(f"PCM resampling error ({from_rate}Hz -> {to_rate}Hz): {e}")
        raise

