"""
Kokoro TTS gRPC Client

Client wrapper for Kokoro TTS gRPC service.
"""

import grpc
from typing import Optional, AsyncGenerator
import numpy as np
from loguru import logger

# Import proto files from current package
from . import tts_pb2, tts_pb2_grpc


class TTSServiceClient:
    """
    Kokoro TTS gRPC client wrapper.
    
    Provides async methods to interact with Kokoro TTS gRPC service.
    """
    
    def __init__(self, service_url: str):
        """
        Initialize TTS client.
        
        Args:
            service_url: gRPC service URL (e.g., "localhost:50053")
        """
        self.service_url = service_url
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[tts_pb2_grpc.TTSServiceStub] = None
        logger.info(f"TTS client configured for {service_url}")
    
    async def connect(self):
        """Establish connection to gRPC service"""
        if self._channel:
            logger.warning("TTS client already connected")
            return
        
        self._channel = grpc.aio.insecure_channel(
            self.service_url,
            options=[
                ('grpc.max_receive_message_length', 100 * 1024 * 1024),  # 100MB
                ('grpc.max_send_message_length', 10 * 1024 * 1024),      # 10MB
            ]
        )
        self._stub = tts_pb2_grpc.TTSServiceStub(self._channel)
        logger.info(f"TTS client connected to {self.service_url}")
    
    async def close(self):
        """Close connection"""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None
            logger.info("TTS client disconnected")
    
    def _ensure_connected(self):
        """Ensure client is connected"""
        if not self._stub:
            raise RuntimeError(
                "TTS client not connected. Call connect() first."
            )
    
    async def create_session(
        self,
        session_id: str,
        voice: str = "zf_001",
        speed: float = 1.0,
        lang: str = "zh",
        sample_rate: int = 24000
    ) -> bool:
        """
        Create a new TTS session.
        
        Args:
            session_id: Unique session identifier
            voice: Voice ID (zf_001, zf_002, zm_001, zm_002)
            speed: Speech speed multiplier
            lang: Language code
            sample_rate: Audio sample rate
            
        Returns:
            True if session created successfully
        """
        self._ensure_connected()
        
        request = tts_pb2.CreateSessionRequest(
            session_id=session_id,
            voice=voice,
            speed=speed,
            lang=lang,
            sample_rate=sample_rate
        )
        
        try:
            response = await self._stub.CreateSession(request)
            return response.success
        except grpc.RpcError as e:
            logger.error(f"Failed to create TTS session {session_id}: {e}")
            raise
    
    async def synthesize(
        self,
        session_id: str,
        text: str,
        join_sentences: bool = True
    ) -> AsyncGenerator[tuple[int, np.ndarray, bool], None]:
        """
        Synthesize text to speech (streaming).
        
        Args:
            session_id: Session identifier
            text: Text to synthesize
            join_sentences: Join sentences together
            
        Yields:
            Tuple of (sample_rate, audio_data, is_final)
            - sample_rate: Audio sample rate
            - audio_data: Float32 PCM audio array
            - is_final: True if this is the last chunk
        """
        self._ensure_connected()
        
        request = tts_pb2.SynthesizeRequest(
            session_id=session_id,
            text=text,
            join_sentences=join_sentences
        )
        
        try:
            async for response in self._stub.Synthesize(request):
                if response.is_final:
                    # Final marker
                    yield (response.sample_rate, np.array([], dtype=np.float32), True)
                else:
                    # Convert bytes back to float32 numpy array
                    audio_data = np.frombuffer(response.audio_data, dtype=np.float32)
                    yield (response.sample_rate, audio_data, False)
        
        except grpc.RpcError as e:
            logger.error(f"TTS synthesis failed for session {session_id}: {e}")
            raise
    
    async def get_voices(self) -> list[dict]:
        """
        Get available voices.
        
        Returns:
            List of voice dicts with id, name, lang
        """
        self._ensure_connected()
        
        try:
            response = await self._stub.GetVoices(tts_pb2.Empty())
            return [
                {
                    "id": voice.id,
                    "name": voice.name,
                    "lang": voice.lang
                }
                for voice in response.voices
            ]
        except grpc.RpcError as e:
            logger.error(f"Failed to get voices: {e}")
            raise
    
    async def destroy_session(self, session_id: str):
        """
        Destroy a TTS session.
        
        Args:
            session_id: Session identifier
        """
        self._ensure_connected()
        
        request = tts_pb2.DestroySessionRequest(session_id=session_id)
        
        try:
            await self._stub.DestroySession(request)
            logger.info(f"TTS session destroyed: {session_id}")
        except grpc.RpcError as e:
            logger.error(f"Failed to destroy TTS session {session_id}: {e}")
            raise
    
    async def get_stats(self) -> dict:
        """
        Get service statistics.
        
        Returns:
            Stats dict with active_sessions, total_requests, etc.
        """
        self._ensure_connected()
        
        try:
            response = await self._stub.GetStats(tts_pb2.Empty())
            return {
                "active_sessions": response.active_sessions,
                "total_requests": response.total_requests,
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "synthesis_count": s.synthesis_count,
                        "age_seconds": s.age_seconds
                    }
                    for s in response.sessions
                ]
            }
        except grpc.RpcError as e:
            logger.error(f"Failed to get TTS stats: {e}")
            raise

