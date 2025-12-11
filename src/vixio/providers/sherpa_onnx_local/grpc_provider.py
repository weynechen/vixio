"""
Local Sherpa ONNX ASR Provider (gRPC Client)

This provider acts as a gRPC client connecting to the ASR service.
Suitable for all deployment modes (dev/docker/k8s).
"""

import uuid
from typing import Dict, Any
from vixio.providers.asr import ASRProvider
from vixio.providers.registry import register_provider
from vixio.providers.sherpa_onnx_local.client import ASRServiceClient


@register_provider("sherpa-onnx-asr-grpc")
class LocalSherpaASRProvider(ASRProvider):
    """
    Local Sherpa ONNX ASR Provider (gRPC Client).
    
    Connects to Sherpa ONNX ASR gRPC service for speech recognition.
    
    Deployment modes:
    - Dev: localhost:50052 (1 replica)
    - Docker: sherpa-asr-service:50052 (1 replica)
    - K8s: sherpa-asr-service:50052 (2-10 replicas, load-balanced)
    """
    
    @property
    def is_local(self) -> bool:
        """This is a local (self-hosted) service"""
        return True
    
    @property
    def is_stateful(self) -> bool:
        """ASR is stateful - maintains recognition state"""
        return True
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "asr"
    
    def __init__(
        self,
        service_url: str,
        language: str = "auto"
    ):
        """
        Initialize Local Sherpa ONNX ASR provider.
        
        Args:
            service_url: gRPC service URL
                - Dev: "localhost:50052"
                - Docker: "sherpa-asr-service:50052"
                - K8s: "sherpa-asr-service:50052"
            language: Language code ("auto", "zh", "en", etc.)
        """
        # Use registered name from decorator
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.service_url = service_url
        self.language = language
        
        # gRPC client
        self._client: ASRServiceClient = None
        self.session_id: str = None
        
        self.logger.info(
            f"Initialized Local Sherpa ONNX ASR (gRPC) "
            f"targeting {service_url}"
        )
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "service_url": {
                "type": "string",
                "required": True,
                "description": "ASR gRPC service URL",
                "examples": {
                    "dev": "localhost:50052",
                    "docker": "sherpa-asr-service:50052",
                    "k8s": "sherpa-asr-service:50052"
                }
            },
            "language": {
                "type": "string",
                "default": "auto",
                "description": "Language code (auto/zh/en/ja/ko/yue)"
            }
        }
    
    async def initialize(self) -> None:
        """
        Initialize provider: connect to gRPC service and create session.
        
        Called once when provider is created.
        """
        # Create gRPC client
        self._client = ASRServiceClient(self.service_url)
        await self._client.connect()
        
        # Create session on server
        self.session_id = str(uuid.uuid4())
        success = await self._client.create_session(
            session_id=self.session_id,
            language=self.language
        )
        
        if not success:
            raise RuntimeError(f"Failed to create ASR session {self.session_id}")
        
        self.logger.info(
            f"ASR session created: {self.session_id} "
            f"(language={self.language})"
        )
    
    async def transcribe_stream(self, audio_chunks: list[bytes]):
        """
        Transcribe audio chunks to text (streaming output).
        
        This is a pseudo-streaming implementation - the underlying
        gRPC service processes all audio at once, but output is
        wrapped as an async iterator for interface consistency.
        
        Args:
            audio_chunks: List of PCM audio bytes (16kHz, mono, 16-bit)
            
        Yields:
            Single text result after processing completes
        """
        if not self._client or not self.session_id:
            raise RuntimeError("ASR provider not initialized. Call initialize() first.")
        
        try:
            # Call gRPC service (Offline API returns single result)
            response = await self._client.transcribe(
                session_id=self.session_id,
                audio_chunks=audio_chunks
            )
            
            # Yield transcribed text (pseudo-streaming)
            if response.text:
                self.logger.debug(f"Transcribed: {response.text}")
                yield response.text
        
        except Exception as e:
            self.logger.error(f"ASR transcription failed: {e}")
            # Yield nothing on error
    
    async def reset(self) -> None:
        """Reset ASR session state"""
        if self._client and self.session_id:
            try:
                # Destroy and recreate session
                await self._client.destroy_session(self.session_id)
                
                # Create new session
                self.session_id = str(uuid.uuid4())
                await self._client.create_session(
                    session_id=self.session_id,
                    language=self.language
                )
                
                self.logger.debug(f"ASR session reset: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Failed to reset ASR session: {e}")
    
    async def cleanup(self) -> None:
        """
        Cleanup provider resources.
        
        Destroys server-side session and closes gRPC connection.
        """
        if self._client and self.session_id:
            try:
                # Destroy server-side session
                await self._client.destroy_session(self.session_id)
                self.logger.info(f"ASR session destroyed: {self.session_id}")
            except Exception as e:
                self.logger.error(f"Error destroying ASR session: {e}")
            
            try:
                # Close gRPC connection
                await self._client.close()
            except Exception as e:
                self.logger.error(f"Error closing ASR client: {e}")
            
            self._client = None
            self.session_id = None
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "service_url": self.service_url,
            "language": self.language,
            "session_id": self.session_id,
        })
        return config

