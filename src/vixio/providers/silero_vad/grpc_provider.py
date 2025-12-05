"""
Local Silero VAD Provider (gRPC Client)

This provider acts as a gRPC client connecting to the VAD service.
Suitable for all deployment modes (dev/docker/k8s).
"""

import uuid
from typing import Dict, Any
from vixio.providers.vad import VADProvider, VADEvent
from vixio.providers.registry import register_provider
from vixio.providers.silero_vad.client import VADServiceClient


@register_provider("silero-vad-grpc")
class LocalSileroVADProvider(VADProvider):
    """
    Local Silero VAD Provider (gRPC Client).
    
    Connects to VAD gRPC service for voice activity detection.
    Supports VAD-cycle locking for optimal concurrency.
    
    Deployment modes:
    - Dev: localhost:50051 (1 replica)
    - Docker: vad-service:50051 (1 replica)
    - K8s: vad-service:50051 (2-10 replicas, load-balanced)
    """
    
    @property
    def is_local(self) -> bool:
        """This is a local (self-hosted) service"""
        return True
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "vad"
    
    def __init__(
        self,
        service_url: str,
        threshold: float = 0.35,        # Optimized: more sensitive to detect weak voice
        threshold_low: float = 0.15,    # Optimized: wider hysteresis range
        frame_window_threshold: int = 8  # Optimized: need more frames to confirm silence
    ):
        """
        Initialize Local Silero VAD provider.
        
        Args:
            service_url: gRPC service URL
                - Dev: "localhost:50051"
                - Docker: "vad-service:50051"
                - K8s: "vad-service:50051"
            threshold: Voice detection threshold (0.0-1.0)
            threshold_low: Lower threshold for hysteresis
            frame_window_threshold: Minimum frames to confirm voice
        """
        # Use registered name from decorator
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.service_url = service_url
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.frame_window_threshold = frame_window_threshold
        
        # gRPC client
        self._client: VADServiceClient = None
        self.session_id: str = None
        
        self.logger.info(
            f"Initialized Local Silero VAD (gRPC) "
            f"targeting {service_url}"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "service_url": {
                "type": "string",
                "required": True,
                "description": "VAD gRPC service URL",
                "examples": {
                    "dev": "localhost:50051",
                    "docker": "vad-service:50051",
                    "k8s": "vad-service:50051"
                }
            },
            "threshold": {
                "type": "float",
                "default": 0.35,
                "description": "Voice detection threshold (0.0-1.0). Lower = more sensitive. Optimized for sentence pauses."
            },
            "threshold_low": {
                "type": "float",
                "default": 0.15,
                "description": "Lower threshold for hysteresis. Wider range prevents flickering during pauses."
            },
            "frame_window_threshold": {
                "type": "int",
                "default": 8,
                "description": "Minimum frames to confirm voice/silence. Higher = more tolerant of pauses (was 3)."
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider: connect to gRPC service and create session.
        
        Called once when provider is created.
        """
        # Create gRPC client
        self._client = VADServiceClient(self.service_url)
        await self._client.connect()
        
        # Create session on server
        self.session_id = str(uuid.uuid4())
        success = await self._client.create_session(
            session_id=self.session_id,
            threshold=self.threshold,
            threshold_low=self.threshold_low,
            frame_window_threshold=self.frame_window_threshold
        )
        
        if not success:
            raise RuntimeError(f"Failed to create VAD session {self.session_id}")
        
        self.logger.info(
            f"VAD session created: {self.session_id} "
            f"(threshold={self.threshold})"
        )
    
    async def detect(
        self,
        audio_data: bytes,
        event: VADEvent = VADEvent.CHUNK
    ) -> bool:
        """
        Detect voice activity in audio data.
        
        The server will manage VAD-cycle locks based on event:
        - START: Acquire lock, begin VAD cycle
        - CHUNK: Continue detection (lock held)
        - END: Release lock, end VAD cycle
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            event: VAD event type
            
        Returns:
            True if voice detected, False otherwise
        """
        if not self._client or not self.session_id:
            raise RuntimeError("VAD provider not initialized. Call initialize() first.")
        
        try:
            response = await self._client.detect(
                session_id=self.session_id,
                audio_data=audio_data,
                event=event.value  # "start", "chunk", or "end"
            )
            
            return response.has_voice
        
        except Exception as e:
            self.logger.error(f"VAD detection failed: {e}")
            # Return False on error (fail-safe)
            return False
    
    async def reset(self) -> None:
        """Reset VAD session state (clear buffers)"""
        if self._client and self.session_id:
            try:
                await self._client.reset_session(self.session_id)
                self.logger.debug(f"VAD session reset: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Failed to reset VAD session: {e}")
    
    async def cleanup(self) -> None:
        """
        Cleanup provider resources.
        
        Destroys server-side session and closes gRPC connection.
        """
        if self._client and self.session_id:
            try:
                # Destroy server-side session
                await self._client.destroy_session(self.session_id)
                self.logger.info(f"VAD session destroyed: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Error destroying VAD session: {e}")
            
            try:
                # Close gRPC connection
                await self._client.close()
            except Exception as e:
                self.logger.error(f"Error closing VAD client: {e}")
            
            self._client = None
            self.session_id = None
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "service_url": self.service_url,
            "threshold": self.threshold,
            "threshold_low": self.threshold_low,
            "frame_window_threshold": self.frame_window_threshold,
            "session_id": self.session_id,
        })
        return config

