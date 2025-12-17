"""
Xiaozhi server runners for different modes.

Provides reusable server startup functions that can be called from:
- CLI (vixio run command)
- Template run.py scripts
- Custom user code

Available runners:
- run_pipeline_server: Pipeline mode (VAD → ASR → Agent → TTS)
- run_realtime_server: Realtime mode (End-to-end with Qwen Omni)
"""

from .pipeline_runner import run_pipeline_server
from .realtime_runner import run_realtime_server

__all__ = ["run_pipeline_server", "run_realtime_server"]
