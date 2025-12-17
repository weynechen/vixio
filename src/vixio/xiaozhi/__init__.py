"""
Xiaozhi voice conversation system.

This package contains all xiaozhi-specific components:
- runners: Server startup functions for pipeline and realtime modes
- presets: Pre-configured settings for quick start
- templates: Project templates for initialization

The xiaozhi system provides:
- WebSocket-based voice communication
- Support for pipeline (VAD→ASR→LLM→TTS) and realtime (end-to-end) modes
- Built-in device management and OTA updates
- Vision analysis capabilities
"""

__all__ = ["runners", "presets", "templates"]
