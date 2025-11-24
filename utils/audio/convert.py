"""
Audio format conversion utilities

Provides common audio format conversions for Transport and Provider layers.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Opus codec support
try:
    import opuslib_next
    OPUS_AVAILABLE = True
except ImportError:
    OPUS_AVAILABLE = False
    logger.warning("opuslib_next not available, Opus codec disabled")


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
        
        # Ensure we have exactly frame_size samples
        expected_bytes = self.frame_size * 2  # 16-bit = 2 bytes per sample
        if len(pcm_data) < expected_bytes:
            # Pad with zeros if not enough data
            pcm_data = pcm_data + b'\x00' * (expected_bytes - len(pcm_data))
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
    Get Opus codec instance (cached for common configurations).
    
    Args:
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels  
        frame_duration_ms: Frame duration in milliseconds
        
    Returns:
        OpusCodec instance
    """
    global _opus_codec_16k_mono
    
    # Cache common configuration (16kHz mono)
    if sample_rate == 16000 and channels == 1 and frame_duration_ms == 60:
        if _opus_codec_16k_mono is None:
            _opus_codec_16k_mono = OpusCodec(sample_rate, channels, frame_duration_ms)
        return _opus_codec_16k_mono
    
    # Create new instance for other configurations
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

