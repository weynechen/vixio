"""
Xiaozhi server runners for different modes.

Provides reusable server startup functions that can be called from:
- CLI (vixio run command)
- Template run.py scripts
- Custom user code

Available runners:
- run_pipeline_server: Pipeline mode (VAD → ASR → Agent → TTS, 7 stations)
- run_streaming_server: Streaming mode (StreamingASR → Agent → StreamingTTS, 4 stations)
- run_realtime_server: Realtime mode (End-to-end with Qwen Omni)
"""

from .pipeline_runner import run_pipeline_server
from .streaming_runner import run_streaming_server
from .realtime_runner import run_realtime_server

__all__ = ["run_pipeline_server", "run_streaming_server", "run_realtime_server"]
