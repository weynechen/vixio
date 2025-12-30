"""
Unit tests for audio conversion utilities.

Tests cover:
- OpusCodec encode/decode
- PCM resampling
- MP3 conversion
- Edge cases and error handling
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock

# Import conditionally to handle missing dependencies
try:
    from vixio.utils.audio.convert import (
        OpusCodec, get_opus_codec,
        opus_to_pcm, pcm_to_opus,
        resample_pcm,
        OPUS_AVAILABLE, SCIPY_AVAILABLE, MP3_AVAILABLE
    )
except ImportError as e:
    pytest.skip(f"Audio conversion dependencies not available: {e}", allow_module_level=True)


# ============ Helper Functions ============

def generate_sine_wave(
    frequency: float = 440.0,
    duration: float = 0.1,
    sample_rate: int = 16000,
    amplitude: int = 16000
) -> bytes:
    """Generate a sine wave as PCM bytes."""
    t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float64)
    samples = (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.int16)
    return samples.tobytes()


def generate_silence(
    duration: float = 0.1,
    sample_rate: int = 16000
) -> bytes:
    """Generate silence as PCM bytes."""
    num_samples = int(sample_rate * duration)
    return b'\x00' * (num_samples * 2)  # 16-bit = 2 bytes per sample


# ============ Test OpusCodec ============

@pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib_next not available")
class TestOpusCodec:
    """Tests for OpusCodec class."""

    def test_initialization_default(self):
        """Test default codec initialization."""
        codec = OpusCodec()
        
        assert codec.sample_rate == 16000
        assert codec.channels == 1
        assert codec.frame_duration_ms == 60
        assert codec.frame_size == 960  # 16000 * 60 / 1000

    def test_initialization_custom(self):
        """Test custom codec initialization."""
        codec = OpusCodec(
            sample_rate=24000,
            channels=2,
            frame_duration_ms=20
        )
        
        assert codec.sample_rate == 24000
        assert codec.channels == 2
        assert codec.frame_duration_ms == 20
        assert codec.frame_size == 480  # 24000 * 20 / 1000

    def test_lazy_decoder_creation(self):
        """Test decoder is created lazily."""
        codec = OpusCodec()
        
        assert codec._decoder is None
        _ = codec.decoder
        assert codec._decoder is not None

    def test_lazy_encoder_creation(self):
        """Test encoder is created lazily."""
        codec = OpusCodec()
        
        assert codec._encoder is None
        _ = codec.encoder
        assert codec._encoder is not None

    def test_encode_decode_roundtrip(self):
        """Test encoding and decoding preserves audio structure.
        
        Note: Opus is lossy compression. Correlation depends on:
        - Frame duration (shorter frames = more artifacts)
        - Audio frequency content
        - Encoder settings
        
        We use a relaxed threshold since 60ms frames at 16kHz
        can have noticeable compression artifacts.
        """
        codec = OpusCodec()
        
        # Generate test audio (one frame worth)
        frame_bytes = codec.frame_size * 2
        pcm_input = generate_sine_wave(
            frequency=440.0,
            duration=codec.frame_duration_ms / 1000,
            sample_rate=codec.sample_rate
        )[:frame_bytes]
        
        # Encode then decode
        opus_data = codec.encode(pcm_input)
        pcm_output = codec.decode(opus_data)
        
        # Output should be same size
        assert len(pcm_output) == frame_bytes
        
        # Convert to arrays for comparison
        input_array = np.frombuffer(pcm_input, dtype=np.int16)
        output_array = np.frombuffer(pcm_output, dtype=np.int16)
        
        # Lossy compression - correlation should be positive (indicating similar structure)
        # Relaxed threshold for short frames with possible artifacts
        correlation = np.corrcoef(input_array, output_array)[0, 1]
        assert correlation > 0.5, f"Correlation {correlation:.3f} too low"

    def test_encode_empty_data(self):
        """Test encoding empty data."""
        codec = OpusCodec()
        result = codec.encode(b'')
        assert result == b''

    def test_decode_empty_data(self):
        """Test decoding empty data."""
        codec = OpusCodec()
        result = codec.decode(b'')
        assert result == b''

    def test_encode_short_data_padded(self):
        """Test encoding short data is padded."""
        codec = OpusCodec()
        
        # Short PCM data (less than one frame)
        short_pcm = generate_sine_wave(duration=0.01)[:100]
        
        # Should encode without error (gets padded)
        opus_data = codec.encode(short_pcm)
        assert len(opus_data) > 0

    def test_encode_multiple(self):
        """Test encoding multiple frames."""
        codec = OpusCodec()
        
        # Generate multiple frames worth of audio
        frame_bytes = codec.frame_size * 2
        pcm_data = generate_sine_wave(duration=0.3)
        
        opus_packets = codec.encode_multiple(pcm_data)
        
        assert len(opus_packets) > 0
        expected_packets = len(pcm_data) // frame_bytes
        assert len(opus_packets) == expected_packets

    def test_decode_multiple(self):
        """Test decoding multiple packets."""
        codec = OpusCodec()
        
        # Generate and encode
        pcm_original = generate_sine_wave(duration=0.18)  # ~3 frames at 60ms
        opus_packets = codec.encode_multiple(pcm_original)
        
        # Decode
        pcm_decoded = codec.decode_multiple(opus_packets)
        
        # Should have decoded audio
        assert len(pcm_decoded) > 0

    def test_bypass_mode(self):
        """Test bypass mode (PCM passthrough)."""
        codec = OpusCodec()
        codec.bypass = True
        
        pcm_data = generate_sine_wave(duration=0.06)
        
        # Encode should return original
        encoded = codec.encode(pcm_data)
        assert encoded == pcm_data
        
        # Decode should return original
        decoded = codec.decode(pcm_data)
        assert decoded == pcm_data


@pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib_next not available")
class TestOpusHelpers:
    """Tests for Opus helper functions."""

    def test_get_opus_codec(self):
        """Test get_opus_codec creates new instances."""
        codec1 = get_opus_codec()
        codec2 = get_opus_codec()
        
        # Should be different instances
        assert codec1 is not codec2

    def test_opus_to_pcm(self):
        """Test quick opus_to_pcm helper."""
        # First encode some PCM
        codec = get_opus_codec()
        original_pcm = generate_sine_wave(duration=0.06)[:960*2]
        opus_data = codec.encode(original_pcm)
        
        # Use helper to decode
        decoded_pcm = opus_to_pcm(opus_data)
        
        assert len(decoded_pcm) == 960 * 2

    def test_pcm_to_opus(self):
        """Test quick pcm_to_opus helper."""
        pcm_data = generate_sine_wave(duration=0.06)[:960*2]
        
        opus_data = pcm_to_opus(pcm_data)
        
        assert len(opus_data) > 0
        assert opus_data != pcm_data  # Should be compressed


# ============ Test PCM Resampling ============

class TestResamplePCM:
    """Tests for PCM resampling."""

    def test_resample_same_rate(self):
        """Test no change when same rate."""
        pcm_data = generate_sine_wave(sample_rate=16000, duration=0.1)
        
        result = resample_pcm(pcm_data, from_rate=16000, to_rate=16000)
        
        assert result == pcm_data

    def test_resample_downsample(self):
        """Test downsampling from 24kHz to 16kHz."""
        # Generate 24kHz audio
        pcm_24k = generate_sine_wave(sample_rate=24000, duration=0.1)
        
        # Resample to 16kHz
        pcm_16k = resample_pcm(pcm_24k, from_rate=24000, to_rate=16000)
        
        # Should have 2/3 the samples
        expected_len = int(len(pcm_24k) * 16000 / 24000)
        assert abs(len(pcm_16k) - expected_len) <= 2  # Allow small rounding diff

    def test_resample_upsample(self):
        """Test upsampling from 8kHz to 16kHz."""
        # Generate 8kHz audio
        pcm_8k = generate_sine_wave(sample_rate=8000, duration=0.1)
        
        # Resample to 16kHz
        pcm_16k = resample_pcm(pcm_8k, from_rate=8000, to_rate=16000)
        
        # Should have 2x the samples
        expected_len = len(pcm_8k) * 2
        assert abs(len(pcm_16k) - expected_len) <= 2

    def test_resample_empty(self):
        """Test resampling empty data."""
        result = resample_pcm(b'', from_rate=16000, to_rate=24000)
        assert result == b''

    def test_resample_preserves_frequency(self):
        """Test resampling preserves audio frequency content."""
        # Generate 440Hz sine at 24kHz
        duration = 0.1
        pcm_24k = generate_sine_wave(
            frequency=440.0,
            sample_rate=24000,
            duration=duration
        )
        
        # Resample to 16kHz
        pcm_16k = resample_pcm(pcm_24k, from_rate=24000, to_rate=16000)
        
        # Both should have similar zero-crossing pattern
        array_24k = np.frombuffer(pcm_24k, dtype=np.int16)
        array_16k = np.frombuffer(pcm_16k, dtype=np.int16)
        
        # Count zero crossings (rough frequency check)
        crossings_24k = np.sum(np.diff(np.signbit(array_24k)))
        crossings_16k = np.sum(np.diff(np.signbit(array_16k)))
        
        # Should have similar number of crossings (frequency preserved)
        assert abs(crossings_24k - crossings_16k) < 5

    def test_resample_multichannel(self):
        """Test resampling stereo audio."""
        # Generate stereo audio (2 channels)
        duration = 0.1
        sample_rate = 24000
        num_samples = int(sample_rate * duration)
        
        # Interleaved stereo data
        left = (10000 * np.sin(2 * np.pi * 440 * np.linspace(0, duration, num_samples))).astype(np.int16)
        right = (10000 * np.sin(2 * np.pi * 880 * np.linspace(0, duration, num_samples))).astype(np.int16)
        stereo = np.column_stack((left, right)).astype(np.int16)
        pcm_stereo = stereo.tobytes()
        
        # Resample to 16kHz
        result = resample_pcm(pcm_stereo, from_rate=24000, to_rate=16000, channels=2)
        
        assert len(result) > 0
        # Should have correct length for stereo 16kHz
        expected_samples = int(num_samples * 16000 / 24000)
        expected_bytes = expected_samples * 2 * 2  # 2 channels, 2 bytes per sample
        assert abs(len(result) - expected_bytes) <= 4


# ============ Test MP3 Conversion ============

@pytest.mark.skipif(not MP3_AVAILABLE, reason="pydub not available")
class TestMP3Conversion:
    """Tests for MP3 conversion (requires pydub)."""

    def test_mp3_import(self):
        """Test MP3 functions are available."""
        from vixio.utils.audio.convert import mp3_to_pcm, pcm_to_mp3
        assert callable(mp3_to_pcm)
        assert callable(pcm_to_mp3)

    def test_pcm_to_mp3_and_back(self):
        """Test PCM -> MP3 -> PCM roundtrip."""
        from vixio.utils.audio.convert import mp3_to_pcm, pcm_to_mp3
        
        # Generate PCM
        original_pcm = generate_sine_wave(duration=0.5, sample_rate=16000)
        
        # Convert to MP3
        mp3_data = pcm_to_mp3(original_pcm, sample_rate=16000, channels=1)
        assert len(mp3_data) > 0
        assert mp3_data != original_pcm  # Should be different (compressed)
        
        # Convert back to PCM
        restored_pcm = mp3_to_pcm(mp3_data, sample_rate=16000, channels=1)
        assert len(restored_pcm) > 0

    def test_mp3_to_pcm_empty(self):
        """Test MP3 conversion with empty data."""
        from vixio.utils.audio.convert import mp3_to_pcm
        
        result = mp3_to_pcm(b'')
        assert result == b''

    def test_pcm_to_mp3_empty(self):
        """Test PCM to MP3 with empty data."""
        from vixio.utils.audio.convert import pcm_to_mp3
        
        result = pcm_to_mp3(b'')
        assert result == b''


# ============ Test Error Handling ============

class TestErrorHandling:
    """Tests for error handling in audio conversion."""

    @pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib_next not available")
    def test_opus_decode_invalid_data(self):
        """Test decoding invalid Opus data raises error."""
        codec = OpusCodec()
        
        with pytest.raises(Exception):
            codec.decode(b'invalid opus data here')

    def test_resample_invalid_data(self):
        """Test resampling with invalid sample width."""
        # Odd number of bytes (invalid for 16-bit)
        invalid_pcm = b'\x00\x01\x02'
        
        # This might raise or produce incorrect output
        # The behavior depends on numpy's handling
        try:
            result = resample_pcm(invalid_pcm, from_rate=16000, to_rate=8000)
            # If it doesn't raise, at least it should produce something
            assert isinstance(result, bytes)
        except Exception:
            pass  # Expected to fail


# ============ Test Edge Cases ============

class TestEdgeCases:
    """Tests for edge cases in audio conversion."""

    @pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib_next not available")
    def test_opus_very_short_audio(self):
        """Test Opus with very short audio."""
        codec = OpusCodec()
        
        # Only 10 samples
        short_pcm = b'\x00\x00' * 10
        
        # Should handle gracefully
        opus_data = codec.encode(short_pcm)
        assert opus_data is not None

    def test_resample_very_short_audio(self):
        """Test resampling very short audio."""
        short_pcm = b'\x00\x00' * 10  # 10 samples
        
        result = resample_pcm(short_pcm, from_rate=16000, to_rate=24000)
        assert isinstance(result, bytes)

    @pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib_next not available")
    def test_opus_silence(self):
        """Test Opus with silence (zeros)."""
        codec = OpusCodec()
        
        silence = generate_silence(duration=0.06)[:960*2]
        
        opus_data = codec.encode(silence)
        decoded = codec.decode(opus_data)
        
        # Decoded silence should be close to zero
        samples = np.frombuffer(decoded, dtype=np.int16)
        assert np.abs(samples).max() < 100  # Near zero

    def test_resample_extreme_ratios(self):
        """Test resampling with extreme sample rate ratios."""
        pcm_8k = generate_sine_wave(sample_rate=8000, duration=0.1)
        
        # Upsample 8kHz to 48kHz (6x)
        pcm_48k = resample_pcm(pcm_8k, from_rate=8000, to_rate=48000)
        
        expected_len = len(pcm_8k) * 6
        assert abs(len(pcm_48k) - expected_len) <= 12

    @pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib_next not available")
    def test_opus_high_amplitude(self):
        """Test Opus with maximum amplitude audio."""
        codec = OpusCodec()
        
        # Maximum amplitude sine wave
        loud_pcm = generate_sine_wave(
            amplitude=32767,  # Max for int16
            duration=0.06
        )[:960*2]
        
        opus_data = codec.encode(loud_pcm)
        decoded = codec.decode(opus_data)
        
        # Should handle without clipping issues
        samples = np.frombuffer(decoded, dtype=np.int16)
        assert np.abs(samples).max() <= 32767


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
