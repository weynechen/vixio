"""
Kokoro TTS gRPC Service

Implements streaming text-to-speech using Kokoro v1.1 Chinese model.
Each session maintains independent TTS state.
"""

import time
import threading
import asyncio
from dataclasses import dataclass, field
from typing import Dict, AsyncGenerator
import numpy as np
import grpc
from loguru import logger

# Import proto files - support both package and script mode
try:
    # When run as package (e.g., from agent_chat.py)
    from . import tts_pb2, tts_pb2_grpc
except ImportError:
    # When run as script (e.g., uv run server.py)
    import tts_pb2, tts_pb2_grpc

# Import Kokoro TTS
from kokoro import KModel, KPipeline
import torch


@dataclass
class SessionState:
    """Per-session TTS state"""
    session_id: str
    voice: str
    speed: float
    lang: str
    sample_rate: int
    
    # Stats
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    synthesis_count: int = 0


class TTSServiceServicer(tts_pb2_grpc.TTSServiceServicer):
    """Kokoro TTS gRPC service implementation"""
    
    def __init__(self, repo_id: str = "hexgrad/Kokoro-82M-v1.1-zh"):
        """Initialize TTS service"""
        # Determine device
        self._device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logger.info(f"Using device: {self._device}")
        
        # Load Kokoro TTS model (shared across sessions)
        self._model = KModel(repo_id=repo_id).to(self._device).eval()
        self._repo_id = repo_id
        
        # Session management
        self._sessions: Dict[str, SessionState] = {}
        self._sessions_lock = threading.Lock()
        
        # Inference lock (protect shared model from concurrent access)
        self._inference_lock = asyncio.Lock()
        
        # Service stats
        self._total_requests = 0
        
        logger.info(f"Kokoro TTS gRPC service initialized (repo: {repo_id}, device: {self._device})")
    
    async def CreateSession(self, request, context):
        """Create a new TTS session"""
        session_id = request.session_id
        
        logger.info(f"Creating TTS session: {session_id}")
        
        # Create session state
        session = SessionState(
            session_id=session_id,
            voice=request.voice or "zf_001",
            speed=request.speed or 1.0,
            lang=request.lang or "zh",
            sample_rate=request.sample_rate or 16000,
        )
        
        with self._sessions_lock:
            if session_id in self._sessions:
                logger.warning(f"TTS session {session_id} already exists, overwriting")
            self._sessions[session_id] = session
        
        logger.info(
            f"TTS session created: {session_id} "
            f"(voice={session.voice}, speed={session.speed}, lang={session.lang})"
        )
        
        return tts_pb2.CreateSessionResponse(
            success=True,
            message=f"Session {session_id} created"
        )
    
    async def Synthesize(self, request, context):
        """
        Synthesize text to speech (streaming).
        
        Yields audio chunks as they are generated.
        """
        session_id = request.session_id
        text = request.text
        join_sentences = request.join_sentences if request.HasField('join_sentences') else True
        
        # Get session
        session = self._sessions.get(session_id)
        if not session:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Session {session_id} not found. Call CreateSession first."
            )
        
        # Update activity
        session.last_activity = time.time()
        session.synthesis_count += 1
        self._total_requests += 1
        
        logger.info(f"[{session_id}] Synthesizing text: {text[:50]}...")
        
        try:
            # Acquire inference lock to prevent concurrent access to shared model
            logger.debug(f"[{session_id}] Waiting for inference lock...")
            start_wait = time.time()
            async with self._inference_lock:
                wait_time = time.time() - start_wait
                if wait_time > 0.01:  # Log if waited > 10ms
                    logger.debug(f"[{session_id}] Acquired lock after {wait_time*1000:.1f}ms wait")
                
                # Create pipeline for this synthesis
                # Use 'z' for Chinese (zh), 'a' for English (en)
                lang_code = 'z' if session.lang == 'zh' else 'a'
                pipeline = KPipeline(
                    lang_code=lang_code,
                    repo_id=self._repo_id,
                    model=self._model
                )
                
                # HACK: Mitigate rushing caused by lack of training data beyond ~100 tokens
                def speed_callable(len_ps):
                    speed = session.speed * 0.8
                    if len_ps <= 83:
                        speed = session.speed
                    elif len_ps < 183:
                        speed = session.speed * (1 - (len_ps - 83) / 500)
                    return speed * 1.1
                
                # Generate audio
                inference_start = time.time()
                generator = pipeline(text, voice=session.voice, speed=speed_callable)
                result = next(generator)
                inference_time = time.time() - inference_start
                
                logger.debug(f"[{session_id}] Inference completed in {inference_time*1000:.1f}ms")
            
            # Get audio data (PyTorch Tensor -> NumPy array)
            audio_data = result.audio
            if hasattr(audio_data, 'cpu'):
                # Convert PyTorch Tensor to NumPy
                audio_np = audio_data.cpu().numpy()
            else:
                # Already NumPy array
                audio_np = audio_data
            
            # Convert to bytes (ensure float32)
            audio_bytes = audio_np.astype(np.float32).tobytes()
            
            # Send audio chunk
            yield tts_pb2.SynthesizeResponse(
                audio_data=audio_bytes,
                sample_rate=session.sample_rate,
                is_final=False,
                session_id=session_id
            )
            
            # Send final marker
            yield tts_pb2.SynthesizeResponse(
                audio_data=b'',
                sample_rate=session.sample_rate,
                is_final=True,
                session_id=session_id
            )
            
            logger.info(f"[{session_id}] Synthesis completed: {len(audio_np)} samples")
        
        except Exception as e:
            logger.error(f"[{session_id}] Synthesis error: {e}")
            context.abort(
                grpc.StatusCode.INTERNAL,
                f"Synthesis failed: {e}"
            )
    
    async def GetVoices(self, request, context):
        """Get available voices"""
        # Kokoro v1.1 Chinese voices
        voices = [
            tts_pb2.Voice(id="zf_001", name="Female Voice 001", lang="zh"),
            tts_pb2.Voice(id="zf_002", name="Female Voice 002", lang="zh"),
            tts_pb2.Voice(id="zm_001", name="Male Voice 001", lang="zh"),
            tts_pb2.Voice(id="zm_002", name="Male Voice 002", lang="zh"),
        ]
        
        return tts_pb2.VoicesResponse(voices=voices)
    
    async def DestroySession(self, request, context):
        """Destroy a TTS session"""
        session_id = request.session_id
        
        with self._sessions_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"TTS session destroyed: {session_id}")
            else:
                logger.warning(f"Attempted to destroy non-existent session: {session_id}")
        
        return tts_pb2.Empty()
    
    async def GetStats(self, request, context):
        """Get service statistics"""
        with self._sessions_lock:
            sessions = [
                tts_pb2.SessionStats(
                    session_id=s.session_id,
                    synthesis_count=s.synthesis_count,
                    age_seconds=time.time() - s.created_at
                )
                for s in self._sessions.values()
            ]
        
        return tts_pb2.StatsResponse(
            active_sessions=len(sessions),
            total_requests=self._total_requests,
            sessions=sessions
        )


async def serve(port: int = 50053, repo_id: str = "hexgrad/Kokoro-82M-v1.1-zh"):
    """Start the gRPC server"""
    server = grpc.aio.server(
        options=[
            ('grpc.max_receive_message_length', 10 * 1024 * 1024),   # 10MB
            ('grpc.max_send_message_length', 100 * 1024 * 1024),     # 100MB (audio)
        ]
    )
    
    tts_pb2_grpc.add_TTSServiceServicer_to_server(
        TTSServiceServicer(repo_id=repo_id),
        server
    )
    
    server.add_insecure_port(f'[::]:{port}')
    
    await server.start()
    logger.info(f"Kokoro TTS gRPC service listening on port {port}")
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down Kokoro TTS service...")
        await server.stop(grace=5)


def main():
    """Main entry point"""
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Kokoro TTS gRPC Service")
    parser.add_argument('--port', type=int, default=50053, help='Service port')
    parser.add_argument('--repo-id', default='hexgrad/Kokoro-82M-v1.1-zh', help='Hugging Face repo ID')
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
        log_dir / "kokoro.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=args.log_level,
        rotation="100 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )
    
    # Start server
    asyncio.run(serve(args.port, args.repo_id))


if __name__ == '__main__':
    main()

