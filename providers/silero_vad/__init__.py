"""
Silero VAD Provider (gRPC Client)

This provider connects to the Silero VAD microservice via gRPC.
The microservice handles the heavy PyTorch model loading and inference.

Usage:
    provider = LocalSileroVADProvider(service_url="localhost:50051")
    await provider.initialize()
    has_voice = await provider.detect(audio_data, event=VADEvent.CHUNK)
"""

from providers.silero_vad.grpc_provider import LocalSileroVADProvider

__all__ = ["LocalSileroVADProvider"]
