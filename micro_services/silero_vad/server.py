"""
VAD gRPC Service - Silero VAD with VAD-cycle locking

Implements per-session VAD with VAD-cycle lock:
- START: Acquire lock, begin VAD cycle
- CHUNK: Continue processing (lock held)
- END: Release lock, end VAD cycle

This allows multiple sessions to interleave their non-VAD periods,
maximizing CPU utilization on a single service instance.
"""

import time
import threading
import asyncio
from dataclasses import dataclass, field
from collections import deque
from typing import Dict
import torch
import numpy as np
import grpc
from loguru import logger

# Import proto files from current package
from . import vad_pb2, vad_pb2_grpc


@dataclass
class SessionState:
    """Per-session VAD state"""
    session_id: str
    threshold: float
    threshold_low: float
    frame_window_threshold: int
    
    # Silero VAD internal state (stateful!)
    state: torch.Tensor
    context: torch.Tensor
    
    # Processing buffers
    pcm_buffer: bytearray = field(default_factory=bytearray)
    voice_window: deque = field(default_factory=lambda: deque(maxlen=10))
    last_is_voice: bool = False
    is_speaking: bool = False
    
    # VAD-cycle lock (only held during START→END)
    vad_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    vad_locked: bool = False  # Is lock currently held?
    
    # Stats
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    request_count: int = 0


class VADServiceServicer(vad_pb2_grpc.VADServiceServicer):
    """VAD gRPC service implementation"""
    
    def __init__(self):
        """Initialize VAD service"""
        # Load Silero VAD model (shared across sessions)
        from silero_vad import load_silero_vad
        self._model = load_silero_vad()
        
        # Session management
        self._sessions: Dict[str, SessionState] = {}
        self._sessions_lock = threading.Lock()
        
        # Model inference lock (short-lived, per-frame)
        self._model_lock = threading.Lock()
        
        # Service stats
        self._total_requests = 0
        
        logger.info("VAD gRPC service initialized")
    
    async def CreateSession(self, request, context):
        """Create a new VAD session"""
        session_id = request.session_id
        
        logger.info(f"Creating session: {session_id}")
        
        # Create session state
        session = SessionState(
            session_id=session_id,
            threshold=request.threshold or 0.5,
            threshold_low=request.threshold_low or 0.2,
            frame_window_threshold=request.frame_window_threshold or 3,
            state=torch.zeros((2, 1, 128)),
            context=torch.zeros(0),
        )
        
        with self._sessions_lock:
            if session_id in self._sessions:
                logger.warning(f"Session {session_id} already exists, overwriting")
            self._sessions[session_id] = session
        
        logger.info(
            f"Session created: {session_id} "
            f"(threshold={session.threshold}, frame_window={session.frame_window_threshold})"
        )
        
        return vad_pb2.CreateSessionResponse(
            success=True,
            message=f"Session {session_id} created"
        )
    
    async def Detect(self, request, context):
        """
        Detect voice activity with VAD-cycle locking
        
        Lock strategy:
        - START: Acquire vad_lock (begin VAD cycle)
        - CHUNK: Continue processing (lock already held)
        - END: Release vad_lock (end VAD cycle)
        
        This allows multiple sessions to process concurrently
        during their non-VAD periods.
        """
        session_id = request.session_id
        event = request.event or "chunk"
        
        # Get session
        session = self._sessions.get(session_id)
        if not session:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Session {session_id} not found. Call CreateSession first."
            )
        
        # Update activity
        session.last_activity = time.time()
        session.request_count += 1
        self._total_requests += 1
        
        # VAD-cycle lock management
        if event == "start":
            # BEGIN VAD cycle: acquire lock
            if session.vad_locked:
                logger.warning(
                    f"[{session_id}] VAD_START but lock already held "
                    "(missed END event?)"
                )
            else:
                await session.vad_lock.acquire()
                session.vad_locked = True
                logger.debug(f"[{session_id}] VAD_START: lock acquired")
        
        elif event == "end":
            # END VAD cycle: release lock
            if not session.vad_locked:
                logger.warning(
                    f"[{session_id}] VAD_END but lock not held "
                    "(missed START event?)"
                )
            else:
                session.vad_lock.release()
                session.vad_locked = False
                logger.debug(f"[{session_id}] VAD_END: lock released")
            
            # Clear buffers (VAD cycle complete)
            session.pcm_buffer.clear()
            session.voice_window.clear()
            session.last_is_voice = False
            
            return vad_pb2.DetectResponse(
                has_voice=False,
                is_speaking=session.is_speaking,
                session_id=session_id
            )
        
        # Auto-acquire lock if not held (fault tolerance)
        if not session.vad_locked and event == "chunk":
            await session.vad_lock.acquire()
            session.vad_locked = True
            logger.debug(
                f"[{session_id}] Auto-acquired lock (no START event received)"
            )
        
        # Process audio (lock is held at this point)
        has_voice = await self._process_audio(session, request.audio_data)
        
        return vad_pb2.DetectResponse(
            has_voice=has_voice,
            is_speaking=session.is_speaking,
            session_id=session_id
        )
    
    async def _process_audio(self, session: SessionState, audio_data: bytes) -> bool:
        """
        Process audio data through Silero VAD
        
        Args:
            session: Session state
            audio_data: PCM audio bytes
            
        Returns:
            True if voice detected in batch
        """
        if not audio_data:
            return False
        
        # Add to PCM buffer
        session.pcm_buffer.extend(audio_data)
        
        # Process in 512-sample frames (16-bit = 1024 bytes)
        has_voice_in_batch = False
        frame_byte_size = 512 * 2
        
        while len(session.pcm_buffer) >= frame_byte_size:
            # Extract frame
            chunk = bytes(session.pcm_buffer[:frame_byte_size])
            session.pcm_buffer = session.pcm_buffer[frame_byte_size:]
            
            # Convert to tensor
            audio_array = np.frombuffer(chunk, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            audio_tensor = torch.from_numpy(audio_float)
            
            # Model inference (thread-safe, short lock)
            with self._model_lock:
                with torch.no_grad():
                    speech_prob = self._model(audio_tensor, 16000).item()
            
            # Dual-threshold detection with hysteresis
            if speech_prob >= session.threshold:
                is_voice = True
            elif speech_prob <= session.threshold_low:
                is_voice = False
            else:
                # Hysteresis: maintain previous state
                is_voice = session.last_is_voice
            
            session.last_is_voice = is_voice
            
            # Update sliding window
            session.voice_window.append(is_voice)
            
            # Check if enough frames have voice
            voice_count = sum(1 for v in session.voice_window if v)
            if voice_count >= session.frame_window_threshold:
                has_voice_in_batch = True
                session.is_speaking = True
        
        return has_voice_in_batch
    
    async def ResetSession(self, request, context):
        """Reset session state (clear buffers)"""
        session_id = request.session_id
        
        session = self._sessions.get(session_id)
        if not session:
            context.abort(grpc.StatusCode.NOT_FOUND, f"Session {session_id} not found")
        
        # Clear buffers
        session.pcm_buffer.clear()
        session.voice_window.clear()
        session.last_is_voice = False
        session.is_speaking = False
        
        logger.info(f"Session reset: {session_id}")
        
        return vad_pb2.Empty()
    
    async def DestroySession(self, request, context):
        """Destroy a VAD session"""
        session_id = request.session_id
        
        with self._sessions_lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                
                # Ensure lock is released
                if session.vad_locked:
                    session.vad_lock.release()
                    logger.warning(
                        f"[{session_id}] Force-released lock on destroy "
                        f"(VAD cycle was not properly ended)"
                    )
                
                del self._sessions[session_id]
                logger.info(f"Session destroyed: {session_id}")
            else:
                logger.warning(f"Attempted to destroy non-existent session: {session_id}")
        
        return vad_pb2.Empty()
    
    async def GetStats(self, request, context):
        """Get service statistics"""
        with self._sessions_lock:
            sessions = [
                vad_pb2.SessionStats(
                    session_id=s.session_id,
                    request_count=s.request_count,
                    age_seconds=time.time() - s.created_at,
                    vad_locked=s.vad_locked
                )
                for s in self._sessions.values()
            ]
        
        return vad_pb2.StatsResponse(
            active_sessions=len(sessions),
            total_requests=self._total_requests,
            sessions=sessions
        )


async def serve(port: int = 50051):
    """Start the gRPC server"""
    server = grpc.aio.server(
        options=[
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),  # 50MB
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
        ]
    )
    
    vad_pb2_grpc.add_VADServiceServicer_to_server(
        VADServiceServicer(),
        server
    )
    
    server.add_insecure_port(f'[::]:{port}')
    
    await server.start()
    logger.info(f"VAD gRPC service listening on port {port}")
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down VAD service...")
        await server.stop(grace=5)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="VAD gRPC Service")
    parser.add_argument('--port', type=int, default=50051, help='Service port')
    parser.add_argument('--log-level', default='INFO', help='Log level')
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=args.log_level
    )
    
    # Start server
    asyncio.run(serve(args.port))


if __name__ == '__main__':
    main()

