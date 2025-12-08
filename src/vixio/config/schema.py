"""
Configuration Schema for Vixio

Defines the structure of configuration using Pydantic models.
"""

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """Base configuration for a provider"""
    type: str = Field(..., description="Provider type name")
    config: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific config")


class VADConfig(BaseModel):
    """VAD Provider configuration"""
    type: str = Field(default="silero-vad-local", description="VAD provider type (silero-vad-local or silero-vad-grpc)")
    service_url: str = Field(default="localhost:50051", description="gRPC service URL (for silero-vad-grpc)")
    threshold: float = Field(default=0.35, description="Voice detection threshold")
    threshold_low: float = Field(default=0.15, description="Lower threshold for hysteresis")
    frame_window_threshold: int = Field(default=8, description="Frames to confirm voice/silence")


class ASRConfig(BaseModel):
    """ASR Provider configuration"""
    type: str = Field(default="sherpa-onnx-asr-local", description="ASR provider type (sherpa-onnx-asr-local or sherpa-onnx-asr-grpc)")
    service_url: str = Field(default="localhost:50052", description="gRPC service URL (for sherpa-onnx-asr-grpc)")


class TTSConfig(BaseModel):
    """TTS Provider configuration"""
    type: str = Field(default="edge-tts-remote", description="TTS provider type (edge-tts-remote, kokoro-tts-local, kokoro-tts-grpc)")
    voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="Voice name")
    rate: str = Field(default="+0%", description="Speech rate")
    volume: str = Field(default="+0%", description="Speech volume")
    pitch: str = Field(default="+0Hz", description="Speech pitch")


class AgentConfig(BaseModel):
    """Agent Provider configuration"""
    type: str = Field(default="openai-agent", description="Agent provider type")
    model: str = Field(default="gpt-4o-mini", description="Model name")
    temperature: float = Field(default=0.7, description="Sampling temperature")
    system_prompt: Optional[str] = Field(default=None, description="System prompt")


class VixioConfig(BaseModel):
    """
    Main Vixio configuration.
    
    This is the root configuration object that contains all settings.
    """
    # Provider configurations
    vad: VADConfig = Field(default_factory=VADConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    
    # Global settings
    log_level: str = Field(default="INFO", description="Log level")
    log_file: Optional[str] = Field(default=None, description="Log file path")
    
    class Config:
        extra = "allow"  # Allow extra fields for extensibility
