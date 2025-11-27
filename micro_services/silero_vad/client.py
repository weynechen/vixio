"""
VAD gRPC Client

Client wrapper for VAD gRPC service.
"""

import grpc
from typing import Optional
from loguru import logger

# Import proto files from current package
from . import vad_pb2, vad_pb2_grpc


class VADServiceClient:
    """
    VAD gRPC client wrapper.
    
    Provides async methods to interact with VAD gRPC service.
    """
    
    def __init__(self, service_url: str):
        """
        Initialize VAD client.
        
        Args:
            service_url: gRPC service URL (e.g., "localhost:50051")
        """
        self.service_url = service_url
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[vad_pb2_grpc.VADServiceStub] = None
        logger.info(f"VAD client configured for {service_url}")
    
    async def connect(self):
        """Establish connection to gRPC service"""
        if self._channel:
            logger.warning("VAD client already connected")
            return
        
        self._channel = grpc.aio.insecure_channel(
            self.service_url,
            options=[
                ('grpc.max_receive_message_length', 50 * 1024 * 1024),  # 50MB
                ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ]
        )
        self._stub = vad_pb2_grpc.VADServiceStub(self._channel)
        logger.info(f"VAD client connected to {self.service_url}")
    
    async def close(self):
        """Close connection"""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None
            logger.info("VAD client disconnected")
    
    def _ensure_connected(self):
        """Ensure client is connected"""
        if not self._stub:
            raise RuntimeError(
                "VAD client not connected. Call connect() first."
            )
    
    async def create_session(
        self,
        session_id: str,
        threshold: float = 0.5,
        threshold_low: float = 0.2,
        frame_window_threshold: int = 3
    ) -> bool:
        """
        Create a new VAD session.
        
        Args:
            session_id: Unique session identifier
            threshold: Voice detection threshold
            threshold_low: Lower threshold for hysteresis
            frame_window_threshold: Minimum frames to confirm voice
            
        Returns:
            True if session created successfully
        """
        self._ensure_connected()
        
        request = vad_pb2.CreateSessionRequest(
            session_id=session_id,
            threshold=threshold,
            threshold_low=threshold_low,
            frame_window_threshold=frame_window_threshold
        )
        
        try:
            response = await self._stub.CreateSession(request)
            return response.success
        except grpc.RpcError as e:
            logger.error(f"Failed to create VAD session {session_id}: {e}")
            raise
    
    async def detect(
        self,
        session_id: str,
        audio_data: bytes,
        event: str = "chunk"
    ) -> vad_pb2.DetectResponse:
        """
        Detect voice activity in audio data.
        
        Args:
            session_id: Session identifier
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            event: VAD event ("start", "chunk", "end")
            
        Returns:
            DetectResponse with has_voice, is_speaking fields
        """
        self._ensure_connected()
        
        request = vad_pb2.DetectRequest(
            session_id=session_id,
            audio_data=audio_data,
            event=event
        )
        
        try:
            response = await self._stub.Detect(request)
            return response
        except grpc.RpcError as e:
            logger.error(f"VAD detect failed for session {session_id}: {e}")
            raise
    
    async def reset_session(self, session_id: str):
        """
        Reset session state (clear buffers).
        
        Args:
            session_id: Session identifier
        """
        self._ensure_connected()
        
        request = vad_pb2.ResetSessionRequest(session_id=session_id)
        
        try:
            await self._stub.ResetSession(request)
        except grpc.RpcError as e:
            logger.error(f"Failed to reset VAD session {session_id}: {e}")
            raise
    
    async def destroy_session(self, session_id: str):
        """
        Destroy a VAD session.
        
        Args:
            session_id: Session identifier
        """
        self._ensure_connected()
        
        request = vad_pb2.DestroySessionRequest(session_id=session_id)
        
        try:
            await self._stub.DestroySession(request)
            logger.info(f"VAD session destroyed: {session_id}")
        except grpc.RpcError as e:
            logger.error(f"Failed to destroy VAD session {session_id}: {e}")
            raise
    
    async def get_stats(self) -> vad_pb2.StatsResponse:
        """
        Get service statistics.
        
        Returns:
            StatsResponse with active sessions, request counts, etc.
        """
        self._ensure_connected()
        
        try:
            response = await self._stub.GetStats(vad_pb2.Empty())
            return response
        except grpc.RpcError as e:
            logger.error(f"Failed to get VAD stats: {e}")
            raise

