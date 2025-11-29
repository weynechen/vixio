"""
VAD gRPC Service - Silero VAD with VAD-cycle locking

Implements per-session VAD with VAD-cycle lock:
- START: Acquire lock, begin VAD cycle
- CHUNK: Continue processing (lock held)
- END: Release lock, end VAD cycle

This allows multiple sessions to interleave their non-VAD periods,
maximizing CPU utilization on a single service instance.
"""

import time,sys
import threading
import asyncio
from dataclasses import dataclass, field
from collections import deque
from typing import Dict, Any
import numpy as np
import grpc
from loguru import logger

# Import proto files - support both package and script mode
try:
    # When run as package (e.g., from agent_chat.py)
    from . import vad_pb2, vad_pb2_grpc
except ImportError:
    # When run as script (e.g., uv run server.py)
    import vad_pb2, vad_pb2_grpc


@dataclass
class SessionState:
    """
    Per-session VAD state using ONNX runtime with external state.
    
    Design: Shared ONNX session + per-session external state.
    - Single shared ONNX runtime session (memory efficient, ~10MB)
    - Each session maintains its own state tensor (~0.5KB)
    - State is passed to/from session during inference (perfect isolation)
    - Fine-grained lock protects session.run() calls (~1ms per call)
    
    Why ONNX:
    - Supports external state management (unlike PyTorch JIT internal state)
    - No state interference between concurrent sessions
    - Optimized inference performance
    - Lower memory footprint per session
    """
    session_id: str
    threshold: float
    threshold_low: float
    frame_window_threshold: int
    
    # Silero VAD external state (per-session, passed to ONNX session)
    # Shape: (2, 1, 128), float32, ~0.5KB per session
    state: np.ndarray
    
    # Silero VAD context (last 64 samples for 16kHz, used for continuity)
    # Shape: (1, 64), float32
    context: np.ndarray
    
    # Processing buffers
    pcm_buffer: bytearray = field(default_factory=bytearray)
    # Increased window from 10 to 30 frames (~960ms) to handle sentence pauses
    # 10 frames = 320ms, 30 frames = 960ms (allows 600-800ms pauses within turn)
    voice_window: deque = field(default_factory=lambda: deque(maxlen=30))
    last_is_voice: bool = False
    is_speaking: bool = False
    
    # Stats
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    request_count: int = 0


class VADServiceServicer(vad_pb2_grpc.VADServiceServicer):
    """
    VAD gRPC service implementation.
    
    Design: Shared model + per-session VADIterator (official Silero VAD approach).
    
    Architecture:
    1. Single shared Silero VAD model (memory efficient)
    2. Each session has its own VADIterator instance
    3. Iterator manages state internally and provides streaming API
    4. Fine-grained lock protects iterator calls (~1ms)
    
    Why VADIterator:
    - PyTorch JIT Silero VAD maintains state internally (can't externalize)
    - VADIterator is the official streaming solution
    - Provides per-session state isolation
    - Handles speech start/end detection automatically
    
    Concurrency:
    - Lock is only held during iterator call (~1ms)
    - Multiple sessions can process concurrently
    - No blocking or state interference
    """
    
    def __init__(self):
        """Initialize VAD service"""
        # Load shared Silero VAD ONNX model (supports external state management)
        from silero_vad import load_silero_vad
        import onnxruntime
        
        # Load ONNX wrapper (default CPU)
        onnx_wrapper = load_silero_vad(onnx=True)
        
        # Recreate session with GPU support if available
        # Priority: CUDA > CPU 
        available_providers = onnxruntime.get_available_providers()
        logger.info(f"Available ONNX providers: {available_providers}")
        
        if "CUDAExecutionProvider" in available_providers:
            # Use CUDA for GPU acceleration
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            logger.info("Using CUDA for VAD inference (GPU)")
        elif "CPUExecutionProvider" in available_providers:
            # Fallback to CPU
            providers = ["CPUExecutionProvider"]
            logger.info("Using CPU for VAD inference")
        else:
            # Use whatever is available
            providers = available_providers
            logger.warning(f"Using default providers: {providers}")
        
        # Configure session options for optimal performance
        sess_options = onnxruntime.SessionOptions()
        sess_options.inter_op_num_threads = 1  # Limit threads for controlled concurrency
        sess_options.intra_op_num_threads = 1
        
        # Recreate session with optimized providers
        model_path = onnx_wrapper.session._model_path
        self._onnx_session = onnxruntime.InferenceSession(
            model_path, 
            providers=providers,
            sess_options=sess_options
        )
        
        logger.info(f"ONNX session created with providers: {self._onnx_session.get_providers()}")
        
        # Session management
        self._sessions: Dict[str, SessionState] = {}
        self._sessions_lock = threading.Lock()
        
        # Model inference lock (very short-lived, only during forward pass)
        # Direct ONNX session supports external state, perfect for concurrent sessions
        # Lock only protects the session.run() call itself (~1ms on CPU, <0.5ms on GPU)
        self._inference_lock = threading.Lock()
        
        # Service stats
        self._total_requests = 0
        
        logger.info("VAD gRPC service initialized (ONNX runtime + per-session external state)")
    
    async def CreateSession(self, request, context):
        """
        Create a new VAD session with independent external state.
        
        Each session gets its own state tensor that is passed to/from
        the shared ONNX session, providing perfect state isolation.
        """
        session_id = request.session_id
        
        logger.info(f"Creating session: {session_id[:8]}...")
        
        # Initialize ONNX state for this session
        # Silero VAD state shape: (2, 1, 128), ~0.5KB per session
        state = np.zeros((2, 1, 128), dtype=np.float32)
        
        # Initialize context (64 samples for 16kHz, 32 for 8kHz)
        # Context is used for audio continuity between frames
        context_size = 64  # 64 for 16kHz
        context = np.zeros((1, context_size), dtype=np.float32)
        
        # Create session state with optimized VAD parameters
        # Adjusted defaults for better handling of sentence pauses:
        # - threshold: 0.35 (down from 0.5) - more sensitive to weak voice
        # - threshold_low: 0.15 (down from 0.2) - wider hysteresis range
        # - frame_window_threshold: 8 (up from 3) - need more frames to confirm silence
        session = SessionState(
            session_id=session_id,
            threshold=request.threshold or 0.35,
            threshold_low=request.threshold_low or 0.15,
            frame_window_threshold=request.frame_window_threshold or 8,
            state=state,
            context=context,
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
        Detect voice activity using session-independent model.
        
        No locks needed - each session has its own model instance,
        ensuring complete isolation and no concurrency issues.
        """
        session_id = request.session_id
        event = request.event or "chunk"
        
        # Debug: Log detect request
        logger.debug(f"[{session_id[:8]}] Detect called: event={event}, audio_bytes={len(request.audio_data)}")
        
        # Get session
        session = self._sessions.get(session_id)
        if not session:
            logger.error(f"[{session_id[:8]}] Session not found!")
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Session {session_id} not found. Call CreateSession first."
            )
        
        # Update activity
        session.last_activity = time.time()
        session.request_count += 1
        self._total_requests += 1
        
        # Debug: Log session stats every 100 requests
        if session.request_count % 100 == 0:
            logger.info(f"[{session_id[:8]}] Stats: requests={session.request_count}, is_speaking={session.is_speaking}")
        
        # Handle events
        if event == "start":
            # BEGIN VAD cycle
            logger.debug(f"[{session_id}] VAD_START event received")
        
        elif event == "end":
            # END VAD cycle: clear buffers
            session.pcm_buffer.clear()
            session.voice_window.clear()
            session.last_is_voice = False
            logger.debug(f"[{session_id}] VAD_END: buffers cleared")
            
            return vad_pb2.DetectResponse(
                has_voice=False,
                is_speaking=session.is_speaking,
                session_id=session_id
            )
        
        # Process audio chunk
        has_voice = await self._process_audio(session, request.audio_data)
        
        return vad_pb2.DetectResponse(
            has_voice=has_voice,
            is_speaking=session.is_speaking,
            session_id=session_id
        )
    
    async def _process_audio(self, session: SessionState, audio_data: bytes) -> bool:
        """
        Process audio data through shared ONNX model with per-session state.
        
        ONNX model supports external state management, so we pass session-specific
        state/context tensors in, and get updated state back. This provides
        perfect state isolation between concurrent sessions.
        
        Args:
            session: Session state (contains per-session state/context arrays)
            audio_data: PCM audio bytes
            
        Returns:
            True if voice detected in batch
        """
        if not audio_data:
            logger.debug(f"[{session.session_id[:8]}] No audio data received")
            return False
        
        # Add to PCM buffer
        session.pcm_buffer.extend(audio_data)
        logger.debug(f"[{session.session_id[:8]}] Buffer size: {len(session.pcm_buffer)} bytes")
        
        # Process in 512-sample frames (16-bit = 1024 bytes)
        has_voice_in_batch = False
        frame_byte_size = 512 * 2
        
        while len(session.pcm_buffer) >= frame_byte_size:
            # Extract frame
            chunk = bytes(session.pcm_buffer[:frame_byte_size])
            session.pcm_buffer = session.pcm_buffer[frame_byte_size:]
            
            # Convert to numpy array (ONNX expects numpy)
            audio_array = np.frombuffer(chunk, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            # Expand dims for batch: (512,) -> (1, 512)
            audio_input = np.expand_dims(audio_float, 0)
            
            # CRITICAL: Concatenate context with audio (ONNX model requirement!)
            # ONNX Silero VAD needs context (last 64 samples) + current audio (512 samples)
            # Total input: (1, 64+512=576)
            context_size = 64  # 64 for 16kHz
            if session.context.shape[1] == 0:
                # First frame, initialize context
                session.context = np.zeros((1, context_size), dtype=np.float32)
            
            # Concatenate: [context(64) + audio(512)] = (1, 576)
            audio_with_context = np.concatenate((session.context, audio_input), axis=1)
            
            # CRITICAL SECTION: ONNX inference with fine-grained lock
            # Lock duration: ~1ms (only during session.run())
            # Each session's state is passed in and updated
            with self._inference_lock:
                # Call ONNX session with context-concatenated audio
                # Inputs: audio_with_context (1, 576), state (2, 1, 128), sr (scalar)
                # Outputs: prob (1, 1), updated_state (2, 1, 128)
                ort_inputs = {
                    'input': audio_with_context.astype(np.float32),
                    'state': session.state,
                    'sr': np.array(16000, dtype=np.int64)
                }
                ort_outputs = self._onnx_session.run(None, ort_inputs)
                speech_prob_array, updated_state = ort_outputs
                
                # Update session's state (critical for state isolation!)
                session.state = updated_state
                speech_prob = speech_prob_array[0, 0]  # Extract scalar
            # Lock released here
            
            # Update context for next frame (last 64 samples of concatenated input)
            session.context = audio_with_context[..., -context_size:]
            
            # Post-processing (outside lock)
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
            
            # Debug: Log every frame to diagnose issue
            if session.request_count <= 10 or speech_prob > 0.05:  # Log first 10 frames or significant probs
                logger.debug(f"[{session.session_id[:8]}] Frame: prob={speech_prob:.4f}, is_voice={is_voice}, voice_count={voice_count}/{len(session.voice_window)}, threshold={session.threshold}")
        
        return has_voice_in_batch
    
    async def ResetSession(self, request, context):
        """Reset session state (clear buffers and ONNX state)"""
        session_id = request.session_id
        
        session = self._sessions.get(session_id)
        if not session:
            context.abort(grpc.StatusCode.NOT_FOUND, f"Session {session_id} not found")
        
        # Clear buffers
        session.pcm_buffer.clear()
        session.voice_window.clear()
        session.last_is_voice = False
        session.is_speaking = False
        
        # Reset ONNX external state (critical for state consistency!)
        # This clears the VAD model's memory for this session
        session.state = np.zeros((2, 1, 128), dtype=np.float32)
        
        # Reset context as well
        context_size = 64  # 64 for 16kHz
        session.context = np.zeros((1, context_size), dtype=np.float32)
        
        logger.info(f"[{session_id[:8]}] Session reset: buffers cleared, ONNX state reset")
        
        return vad_pb2.Empty()
    
    async def DestroySession(self, request, context):
        """Destroy a VAD session and cleanup resources"""
        session_id = request.session_id
        
        with self._sessions_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"Session destroyed: {session_id[:8]}...")
            else:
                logger.warning(f"Attempted to destroy non-existent session: {session_id[:8]}...")
        
        return vad_pb2.Empty()
    
    async def GetStats(self, request, context):
        """Get service statistics"""
        with self._sessions_lock:
            sessions = [
                vad_pb2.SessionStats(
                    session_id=s.session_id,
                    request_count=s.request_count,
                    age_seconds=time.time() - s.created_at,
                    vad_locked=False  # No longer using session-level lock
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
    parser.add_argument('--log-level', default='INFO', help='Log level')  # Changed to DEBUG for troubleshooting
    args = parser.parse_args()
    
    args.log_level = 'DEBUG' # for debug use

    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=args.log_level
    )

    logger.add(
        "logs/silero_vad.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=args.log_level,
        rotation="100 MB",
        retention="30 days",
        compression="zip",  # Compress rotated logs
        encoding="utf-8",
    )
    
    # Start server
    asyncio.run(serve(args.port))


if __name__ == '__main__':
    main()

