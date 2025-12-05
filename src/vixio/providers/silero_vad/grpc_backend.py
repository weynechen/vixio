"""
gRPC Backend for Silero VAD Provider

This backend connects to an external gRPC service for VAD inference.
Used in production deployments where resource isolation is needed.
"""

from typing import Optional
from loguru import logger

from vixio.providers.vad import VADEvent
from vixio.providers.silero_vad.client import VADServiceClient


class GrpcSileroBackend:
    """
    gRPC client backend for Silero VAD.
    
    Connects to a remote VAD gRPC service.
    """
    
    def __init__(
        self,
        session_id: str,
        service_url: str = "localhost:50051",
        threshold: float = 0.35,
        threshold_low: float = 0.15,
        frame_window_threshold: int = 8,
    ):
        """
        Initialize gRPC backend.
        
        Args:
            session_id: Unique session identifier
            service_url: gRPC service URL
            threshold: Voice detection threshold
            threshold_low: Lower threshold for hysteresis
            frame_window_threshold: Minimum frames to confirm voice
        """
        self.session_id = session_id
        self.service_url = service_url
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.frame_window_threshold = frame_window_threshold
        
        self._client: Optional[VADServiceClient] = None
        self.logger = logger.bind(component="GrpcSileroBackend")
    
    async def initialize(self) -> None:
        """Connect to gRPC service and create session"""
        # Create gRPC client
        self._client = VADServiceClient(self.service_url)
        await self._client.connect()
        
        # Create session on server
        success = await self._client.create_session(
            session_id=self.session_id,
            threshold=self.threshold,
            threshold_low=self.threshold_low,
            frame_window_threshold=self.frame_window_threshold
        )
        
        if not success:
            raise RuntimeError(f"Failed to create VAD session {self.session_id}")
        
        self.logger.info(
            f"VAD gRPC session created: {self.session_id} "
            f"(service={self.service_url})"
        )
    
    async def detect(self, audio_data: bytes, event: VADEvent) -> bool:
        """
        Detect voice activity via gRPC.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            event: VAD event type
            
        Returns:
            True if voice detected, False otherwise
        """
        if not self._client:
            raise RuntimeError("Backend not initialized")
        
        try:
            response = await self._client.detect(
                session_id=self.session_id,
                audio_data=audio_data,
                event=event.value
            )
            return response.has_voice
        except Exception as e:
            self.logger.error(f"VAD detection failed: {e}")
            return False
    
    async def reset(self) -> None:
        """Reset VAD session state"""
        if self._client:
            try:
                await self._client.reset_session(self.session_id)
                self.logger.debug(f"VAD session reset: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Failed to reset VAD session: {e}")
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self._client:
            try:
                await self._client.destroy_session(self.session_id)
                self.logger.info(f"VAD session destroyed: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Error destroying VAD session: {e}")
            
            try:
                await self._client.close()
            except Exception as e:
                self.logger.error(f"Error closing VAD client: {e}")
            
            self._client = None

