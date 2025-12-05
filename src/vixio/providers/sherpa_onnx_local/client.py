"""
Sherpa ONNX ASR gRPC Client

Client library for connecting to the ASR service.
"""

import grpc
from typing import List
from loguru import logger

# Import proto files
from . import asr_pb2, asr_pb2_grpc


class ASRServiceClient:
    """
    gRPC client for Sherpa ONNX ASR service.
    
    Usage:
        client = ASRServiceClient("localhost:50052")
        await client.connect()
        
        await client.create_session("session-1", language="auto")
        result = await client.transcribe("session-1", [audio_chunk1, audio_chunk2])
        print(result.text)
        
        await client.destroy_session("session-1")
        await client.close()
    """
    
    def __init__(self, service_url: str):
        """
        Initialize ASR service client.
        
        Args:
            service_url: gRPC service URL (e.g., "localhost:50052")
        """
        self.service_url = service_url
        self._channel = None
        self._stub = None
    
    async def connect(self) -> None:
        """Establish connection to gRPC service"""
        try:
            self._channel = grpc.aio.insecure_channel(self.service_url)
            self._stub = asr_pb2_grpc.ASRServiceStub(self._channel)
            
            # Test connection by calling GetStats
            await self._stub.GetStats(asr_pb2.Empty())
            
            logger.debug(f"Connected to ASR service at {self.service_url}")
        
        except Exception as e:
            logger.error(f"Failed to connect to ASR service at {self.service_url}: {e}")
            raise ConnectionError(f"Failed to connect to ASR service: {e}")
    
    async def create_session(
        self,
        session_id: str,
        language: str = "auto"
    ) -> bool:
        """
        Create a new ASR session.
        
        Args:
            session_id: Unique session identifier
            language: Language code ("auto", "zh", "en", etc.)
            
        Returns:
            True if session created successfully
            
        Raises:
            RuntimeError: If session creation fails
        """
        if not self._stub:
            raise RuntimeError("Not connected. Call connect() first.")
        
        try:
            request = asr_pb2.CreateSessionRequest(
                session_id=session_id,
                language=language
            )
            
            response = await self._stub.CreateSession(request)
            
            if not response.success:
                raise RuntimeError(f"Failed to create session: {response.message}")
            
            logger.debug(f"ASR session created: {session_id}")
            return True
        
        except grpc.RpcError as e:
            logger.error(f"gRPC error creating session: {e.code()}: {e.details()}")
            raise RuntimeError(f"Failed to create ASR session: {e.details()}")
    
    async def transcribe(
        self,
        session_id: str,
        audio_chunks: List[bytes]
    ) -> asr_pb2.TranscribeResponse:
        """
        Transcribe audio chunks to text.
        
        Args:
            session_id: Session identifier
            audio_chunks: List of audio bytes (PCM 16-bit, 16kHz, mono)
            
        Returns:
            TranscribeResponse with text and confidence
            
        Raises:
            RuntimeError: If transcription fails
        """
        if not self._stub:
            raise RuntimeError("Not connected. Call connect() first.")
        
        try:
            request = asr_pb2.TranscribeRequest(
                session_id=session_id,
                audio_chunks=audio_chunks
            )
            
            response = await self._stub.Transcribe(request)
            
            return response
        
        except grpc.RpcError as e:
            logger.error(f"gRPC error during transcription: {e.code()}: {e.details()}")
            raise RuntimeError(f"Transcription failed: {e.details()}")
    
    async def destroy_session(self, session_id: str) -> None:
        """
        Destroy an ASR session.
        
        Args:
            session_id: Session identifier
        """
        if not self._stub:
            raise RuntimeError("Not connected. Call connect() first.")
        
        try:
            request = asr_pb2.DestroySessionRequest(
                session_id=session_id
            )
            
            await self._stub.DestroySession(request)
            logger.debug(f"ASR session destroyed: {session_id}")
        
        except grpc.RpcError as e:
            logger.error(f"gRPC error destroying session: {e.code()}: {e.details()}")
            # Don't raise - cleanup should be best-effort
    
    async def get_stats(self) -> asr_pb2.StatsResponse:
        """
        Get service statistics.
        
        Returns:
            StatsResponse with service metrics
        """
        if not self._stub:
            raise RuntimeError("Not connected. Call connect() first.")
        
        try:
            response = await self._stub.GetStats(asr_pb2.Empty())
            return response
        
        except grpc.RpcError as e:
            logger.error(f"gRPC error getting stats: {e.code()}: {e.details()}")
            raise RuntimeError(f"Failed to get stats: {e.details()}")
    
    async def close(self) -> None:
        """Close gRPC connection"""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None
            logger.debug("ASR service connection closed")
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()


# Example usage
async def main():
    """Example usage of ASR service client"""
    import numpy as np
    
    # Create client
    client = ASRServiceClient("localhost:50052")
    
    try:
        # Connect
        await client.connect()
        print("✓ Connected to ASR service")
        
        # Create session
        session_id = "test-session-1"
        await client.create_session(session_id, language="auto")
        print(f"✓ Created session: {session_id}")
        
        # Generate test audio (1 second of silence)
        sample_rate = 16000
        duration = 1.0
        samples = np.zeros(int(sample_rate * duration), dtype=np.int16)
        audio_bytes = samples.tobytes()
        
        # Transcribe
        result = await client.transcribe(session_id, [audio_bytes])
        print(f"✓ Transcription: '{result.text}' (confidence: {result.confidence:.2f})")
        
        # Get stats
        stats = await client.get_stats()
        print(f"✓ Stats: {stats.active_sessions} active sessions, "
              f"{stats.total_requests} requests, "
              f"{stats.total_chunks_processed} chunks processed")
        
        # Destroy session
        await client.destroy_session(session_id)
        print(f"✓ Destroyed session: {session_id}")
    
    finally:
        # Close connection
        await client.close()
        print("✓ Connection closed")


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

