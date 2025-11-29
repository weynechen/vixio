"""
Sherpa ONNX ASR gRPC Service

Implements streaming ASR using Sherpa ONNX models.
Each session maintains independent recognizer state.
"""

import time
import threading
import asyncio
from dataclasses import dataclass, field
from typing import Dict
import numpy as np
import grpc
from loguru import logger
import sherpa_onnx

# Import proto files - support both package and script mode
try:
    # When run as package (e.g., from agent_chat.py)
    from . import asr_pb2, asr_pb2_grpc
except ImportError:
    # When run as script (e.g., uv run server.py)
    import asr_pb2, asr_pb2_grpc


@dataclass
class SessionState:
    """Per-session ASR state"""
    session_id: str
    language: str
    recognizer: sherpa_onnx.OnlineRecognizer
    stream: sherpa_onnx.OnlineStream
    
    # Stats
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    transcribe_count: int = 0
    chunks_processed: int = 0


class ASRServiceServicer(asr_pb2_grpc.ASRServiceServicer):
    """Sherpa ONNX ASR gRPC service implementation"""
    
    def __init__(self, model_path: str):
        """Initialize ASR service"""
        logger.info(f"Initializing Sherpa ONNX ASR service with model: {model_path}")
        
        # Store model path
        self._model_path = model_path
        
        # Create offline recognizer for SenseVoice
        import os
        model_file = os.path.join(model_path, "model.int8.onnx")
        tokens_file = os.path.join(model_path, "tokens.txt")
        
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_file,
            tokens=tokens_file,
            num_threads=4,
            sample_rate=16000,
            feature_dim=80,
            use_itn=True,
            debug=False,
        )
        
        # Session management
        self._sessions: Dict[str, SessionState] = {}
        self._sessions_lock = threading.Lock()
        
        # Inference lock (protect shared recognizer from concurrent access)
        self._inference_lock = asyncio.Lock()
        
        # Service stats
        self._total_requests = 0
        self._total_chunks = 0
        
        logger.info("Sherpa ONNX ASR gRPC service initialized")
    
    async def CreateSession(self, request, context):
        """Create a new ASR session"""
        session_id = request.session_id
        language = request.language or "auto"
        
        logger.info(f"Creating ASR session: {session_id} (language={language})")
        
        try:
            # Create session state (Offline API doesn't need per-session stream)
            session = SessionState(
                session_id=session_id,
                language=language,
                recognizer=None,  # Use shared recognizer
                stream=None,      # Create stream per transcription
            )
            
            with self._sessions_lock:
                if session_id in self._sessions:
                    logger.warning(f"ASR session {session_id} already exists, overwriting")
                self._sessions[session_id] = session
            
            logger.info(f"ASR session created: {session_id}")
            
            return asr_pb2.CreateSessionResponse(
                success=True,
                message=f"Session {session_id} created"
            )
        
        except Exception as e:
            logger.error(f"Failed to create ASR session {session_id}: {e}")
            return asr_pb2.CreateSessionResponse(
                success=False,
                message=f"Failed to create session: {e}"
            )
    
    async def Transcribe(self, request, context):
        """Transcribe audio chunks to text"""
        session_id = request.session_id
        audio_chunks = request.audio_chunks
        
        # Get session
        session = self._sessions.get(session_id)
        if not session:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Session {session_id} not found. Call CreateSession first."
            )
        
        # Update activity
        session.last_activity = time.time()
        session.transcribe_count += 1
        self._total_requests += 1
        
        try:
            # Concatenate all audio chunks
            audio_data = b''.join(audio_chunks)
            
            if not audio_data:
                return asr_pb2.TranscribeResponse(
                    text="",
                    confidence=0.0,
                    session_id=session_id
                )
            
            # Convert bytes to numpy array (PCM 16-bit, 16kHz, mono)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Convert to float32 normalized to [-1, 1]
            audio_float = audio_array.astype(np.float32) / 32768.0
            
            # Acquire inference lock to prevent concurrent access to shared recognizer
            logger.debug(f"[{session_id}] Waiting for inference lock...")
            start_wait = time.time()
            async with self._inference_lock:
                wait_time = time.time() - start_wait
                if wait_time > 0.01:  # Log if waited > 10ms
                    logger.debug(f"[{session_id}] Acquired lock after {wait_time*1000:.1f}ms wait")
                
                # Create offline stream
                stream = self._recognizer.create_stream()
                stream.accept_waveform(16000, audio_float)
                
                # Decode offline (one-shot)
                inference_start = time.time()
                self._recognizer.decode_stream(stream)
                inference_time = time.time() - inference_start
                
                # Get result
                result = stream.result.text.strip()
                
                logger.debug(f"[{session_id}] Inference completed in {inference_time*1000:.1f}ms")
            
            # Update stats
            session.chunks_processed += len(audio_chunks)
            self._total_chunks += len(audio_chunks)
            
            # Estimate confidence
            confidence = 1.0 if result else 0.0
            
            if result:
                logger.info(f"[{session_id}] Transcribed: {result}")
            
            return asr_pb2.TranscribeResponse(
                text=result,
                confidence=confidence,
                session_id=session_id
            )
        
        except Exception as e:
            logger.error(f"[{session_id}] Transcription error: {e}")
            context.abort(
                grpc.StatusCode.INTERNAL,
                f"Transcription failed: {e}"
            )
    
    async def DestroySession(self, request, context):
        """Destroy an ASR session"""
        session_id = request.session_id
        
        with self._sessions_lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                
                # Log stats
                duration = time.time() - session.created_at
                logger.info(
                    f"Destroying ASR session {session_id}: "
                    f"duration={duration:.1f}s, "
                    f"transcribe_count={session.transcribe_count}, "
                    f"chunks_processed={session.chunks_processed}"
                )
                
                # Clean up (recognizer and stream will be garbage collected)
                del self._sessions[session_id]
            else:
                logger.warning(f"ASR session {session_id} not found for destruction")
        
        return asr_pb2.Empty()
    
    async def GetStats(self, request, context):
        """Get service statistics"""
        with self._sessions_lock:
            active_sessions = len(self._sessions)
        
        return asr_pb2.StatsResponse(
            active_sessions=active_sessions,
            total_requests=self._total_requests,
            total_chunks_processed=self._total_chunks
        )


async def serve(port: int, model_path: str):
    """Start gRPC server"""
    server = grpc.aio.server()
    
    # Add servicer
    servicer = ASRServiceServicer(model_path)
    asr_pb2_grpc.add_ASRServiceServicer_to_server(servicer, server)
    
    # Bind to port
    server.add_insecure_port(f'[::]:{port}')
    
    logger.info(f"Sherpa ONNX ASR gRPC service listening on port {port}")
    logger.info(f"Model: {model_path}")
    
    await server.start()
    await server.wait_for_termination()


def main():
    """Main entry point"""
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Sherpa ONNX ASR gRPC Service")
    parser.add_argument('--port', type=int, default=50052, help='Service port')
    parser.add_argument(
        '--model-path',
        default='models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17',
        help='Path to Sherpa ONNX model directory'
    )
    parser.add_argument('--log-level', default='INFO', help='Log level')
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=args.log_level
    )
    
    # Also log to file
    from pathlib import Path
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "sherpa_asr.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=args.log_level,
        rotation="100 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )
    
    # Start server
    asyncio.run(serve(args.port, args.model_path))


if __name__ == '__main__':
    main()

