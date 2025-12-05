"""
Test audio format conversion utilities
"""

import pytest
import numpy as np
from vixio.utils.audio import (
    OpusCodec,
    get_opus_codec,
    opus_to_pcm,
    pcm_to_opus,
    OPUS_AVAILABLE,
)


@pytest.mark.skipif(not OPUS_AVAILABLE, reason="Opus codec not available")
class TestOpusCodec:
    """Test Opus codec functionality"""
    
    def test_codec_initialization(self):
        """Test codec initialization"""
        codec = OpusCodec(sample_rate=16000, channels=1, frame_duration_ms=60)
        assert codec.sample_rate == 16000
        assert codec.channels == 1
        assert codec.frame_duration_ms == 60
        assert codec.frame_size == 960  # 16000 * 60 / 1000
    
    def test_encode_decode_roundtrip(self):
        """Test encode -> decode roundtrip"""
        # Generate test PCM data (960 samples = 60ms at 16kHz)
        sample_count = 960
        frequency = 440.0  # A4 note
        sample_rate = 16000
        
        # Generate sine wave
        t = np.linspace(0, sample_count / sample_rate, sample_count, False)
        audio = np.sin(2 * np.pi * frequency * t)
        
        # Convert to 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)
        pcm_data = audio_int16.tobytes()
        
        # Encode to Opus
        codec = OpusCodec()
        opus_data = codec.encode(pcm_data)
        
        # Verify Opus data is compressed (should be smaller)
        assert len(opus_data) > 0
        assert len(opus_data) < len(pcm_data)
        
        # Decode back to PCM
        decoded_pcm = codec.decode(opus_data)
        
        # Verify size matches (lossy compression, so values won't match exactly)
        assert len(decoded_pcm) == len(pcm_data)
    
    def test_decode_empty_data(self):
        """Test decoding empty data"""
        codec = OpusCodec()
        result = codec.decode(b'')
        assert result == b''
    
    def test_encode_empty_data(self):
        """Test encoding empty data"""
        codec = OpusCodec()
        result = codec.encode(b'')
        assert result == b''
    
    def test_encode_padding(self):
        """Test encoding with data smaller than frame size"""
        codec = OpusCodec()
        
        # Create small PCM data (less than frame size)
        small_pcm = b'\x00\x00' * 100  # 100 samples instead of 960
        
        # Should pad with zeros automatically
        opus_data = codec.encode(small_pcm)
        assert len(opus_data) > 0
    
    def test_multiple_encode_decode(self):
        """Test encoding/decoding multiple frames"""
        codec = OpusCodec()
        
        # Generate multiple frames
        frames = []
        for i in range(5):
            pcm_frame = np.random.randint(-32768, 32767, 960, dtype=np.int16).tobytes()
            frames.append(pcm_frame)
        
        # Encode all frames
        opus_packets = codec.encode_multiple(b''.join(frames))
        assert len(opus_packets) == 5
        
        # Decode all packets
        decoded_pcm = codec.decode_multiple(opus_packets)
        assert len(decoded_pcm) == len(b''.join(frames))
    
    def test_get_opus_codec_singleton(self):
        """Test singleton caching for common configuration"""
        codec1 = get_opus_codec(16000, 1, 60)
        codec2 = get_opus_codec(16000, 1, 60)
        
        # Should return the same instance
        assert codec1 is codec2
    
    def test_get_opus_codec_different_config(self):
        """Test different configurations create different instances"""
        codec1 = get_opus_codec(16000, 1, 60)
        codec2 = get_opus_codec(8000, 1, 60)
        
        # Should return different instances
        assert codec1 is not codec2
    
    def test_quick_helpers(self):
        """Test quick helper functions"""
        # Generate test PCM
        pcm_data = np.random.randint(-32768, 32767, 960, dtype=np.int16).tobytes()
        
        # Encode using helper
        opus_data = pcm_to_opus(pcm_data)
        assert len(opus_data) > 0
        
        # Decode using helper
        decoded_pcm = opus_to_pcm(opus_data)
        assert len(decoded_pcm) == len(pcm_data)


@pytest.mark.skipif(OPUS_AVAILABLE, reason="Test Opus not available scenario")
class TestOpusNotAvailable:
    """Test behavior when Opus is not available"""
    
    def test_opus_not_available_flag(self):
        """Test OPUS_AVAILABLE flag is False"""
        assert not OPUS_AVAILABLE
    
    def test_codec_raises_error(self):
        """Test codec raises error when Opus not available"""
        with pytest.raises(RuntimeError):
            OpusCodec()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

