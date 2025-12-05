"""
Audio utilities

Audio format conversion and processing tools.
"""

from vixio.utils.audio.convert import (
    # Opus codec
    OpusCodec,
    get_opus_codec,
    opus_to_pcm,
    pcm_to_opus,
    OPUS_AVAILABLE,
    # MP3 codec
    mp3_to_pcm,
    pcm_to_mp3,
    MP3_AVAILABLE,
)

__all__ = [
    # Opus
    "OpusCodec",
    "get_opus_codec",
    "opus_to_pcm",
    "pcm_to_opus",
    "OPUS_AVAILABLE",
    # MP3
    "mp3_to_pcm",
    "pcm_to_mp3",
    "MP3_AVAILABLE",
]

