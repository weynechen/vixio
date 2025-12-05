"""
Silero VAD Provider with dual-mode support (local/grpc)

This provider supports two operation modes:
- local: Start local gRPC service subprocess, then connect via gRPC
- grpc: Connect to external gRPC service
- auto: Try gRPC first, fallback to local

Usage:
    # Auto mode (recommended for development)
    vad = SileroVADProvider()
    
    # Force local mode (starts subprocess)
    vad = SileroVADProvider(mode="local")
    
    # Force gRPC mode (production)
    vad = SileroVADProvider(mode="grpc", service_url="vad-service:50051")
"""

import uuid
import asyncio
import subprocess
import atexit
import socket
from enum import Enum
from typing import Dict, Any, Literal, Optional, Protocol, runtime_checkable

from vixio.providers.vad import VADProvider, VADEvent
from vixio.providers.registry import register_provider


class ServiceMode(str, Enum):
    """Service mode for dual-mode providers"""
    AUTO = "auto"      # Try gRPC first, fallback to local subprocess
    LOCAL = "local"    # Start local subprocess + connect via gRPC
    GRPC = "grpc"      # Connect to external gRPC service


@runtime_checkable
class VADBackend(Protocol):
    """Protocol for VAD backend implementations"""
    
    async def initialize(self) -> None:
        """Initialize the backend"""
        ...
    
    async def detect(self, audio_data: bytes, event: VADEvent) -> bool:
        """Detect voice activity"""
        ...
    
    async def reset(self) -> None:
        """Reset backend state"""
        ...
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        ...


@register_provider("silero-vad")
class SileroVADProvider(VADProvider):
    """
    Silero VAD Provider with dual-mode support.
    
    This provider supports two operation modes for flexibility:
    
    1. **Local Mode** (mode="local"):
       - Starts VAD service as a subprocess
       - Connects via gRPC to the subprocess
       - Requires: pip install vixio[silero-vad-local] (workspace member)
       - Great for development
       
    2. **gRPC Mode** (mode="grpc"):
       - Connects to external gRPC service
       - Better for production (resource isolation, scaling)
       - Requires: running vad-service
       
    3. **Auto Mode** (mode="auto", default):
       - Tries gRPC first
       - Falls back to local subprocess if gRPC unavailable
       - Best of both worlds
    
    Examples:
        # Development (auto starts local service if needed)
        vad = SileroVADProvider()
        await vad.initialize()
        
        # Production
        vad = SileroVADProvider(mode="grpc", service_url="vad-service:50051")
        await vad.initialize()
    """
    
    @property
    def is_local(self) -> bool:
        """Whether using local subprocess"""
        return self._actual_mode == ServiceMode.LOCAL
    
    @property
    def category(self) -> str:
        """Provider category"""
        return "vad"
    
    def __init__(
        self,
        mode: Literal["auto", "local", "grpc"] = "auto",
        service_url: str = "localhost:50051",
        threshold: float = 0.35,
        threshold_low: float = 0.15,
        frame_window_threshold: int = 8,
        **kwargs
    ):
        """
        Initialize Silero VAD Provider.
        
        Args:
            mode: Operation mode
                - "auto": Try gRPC, fallback to local subprocess (default)
                - "local": Start local subprocess
                - "grpc": Connect to external gRPC service
            service_url: gRPC service URL (for grpc/auto mode)
            threshold: Voice detection threshold (0.0-1.0)
            threshold_low: Lower threshold for hysteresis
            frame_window_threshold: Minimum frames to confirm voice/silence
        """
        super().__init__(name="silero-vad")
        
        self.mode = ServiceMode(mode)
        self.service_url = service_url
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.frame_window_threshold = frame_window_threshold
        
        self._backend: Optional[VADBackend] = None
        self._actual_mode: Optional[ServiceMode] = None
        self._session_id: Optional[str] = None
        self._subprocess: Optional[subprocess.Popen] = None
        self._local_port: Optional[int] = None
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return configuration schema"""
        return {
            "mode": {
                "type": "string",
                "default": "auto",
                "enum": ["auto", "local", "grpc"],
                "description": "Operation mode: auto, local (subprocess), grpc (external)"
            },
            "service_url": {
                "type": "string",
                "default": "localhost:50051",
                "description": "gRPC service URL (for grpc/auto mode)"
            },
            "threshold": {
                "type": "float",
                "default": 0.35,
                "description": "Voice detection threshold (0.0-1.0)"
            },
            "threshold_low": {
                "type": "float",
                "default": 0.15,
                "description": "Lower threshold for hysteresis"
            },
            "frame_window_threshold": {
                "type": "int",
                "default": 8,
                "description": "Minimum frames to confirm voice/silence"
            }
        }
    
    def _find_free_port(self) -> int:
        """Find a free port for local service"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port
    
    async def initialize(self) -> None:
        """
        Initialize provider with automatic mode selection.
        
        For AUTO mode:
        1. Try to connect to gRPC service
        2. If failed, start local subprocess and connect
        """
        self._session_id = str(uuid.uuid4())
        
        if self.mode == ServiceMode.LOCAL:
            await self._start_local_service()
            self._backend = await self._init_grpc(f"localhost:{self._local_port}")
            self._actual_mode = ServiceMode.LOCAL
            
        elif self.mode == ServiceMode.GRPC:
            self._backend = await self._init_grpc(self.service_url)
            self._actual_mode = ServiceMode.GRPC
            
        else:  # AUTO mode
            # Step 1: Try external gRPC first
            try:
                self._backend = await self._init_grpc(self.service_url)
                self._actual_mode = ServiceMode.GRPC
                self.logger.info(
                    f"VAD using gRPC mode (connected to {self.service_url})"
                )
            except Exception as e:
                # Step 2: Fallback to local subprocess
                self.logger.debug(f"gRPC connection failed: {e}")
                self.logger.info("gRPC service unavailable, starting local subprocess...")
                
                try:
                    await self._start_local_service()
                    self._backend = await self._init_grpc(f"localhost:{self._local_port}")
                    self._actual_mode = ServiceMode.LOCAL
                    self.logger.info(f"VAD using local subprocess mode (port {self._local_port})")
                except Exception as e2:
                    raise RuntimeError(
                        f"Failed to start VAD service.\n"
                        f"gRPC error: {e}\n"
                        f"Local error: {e2}\n"
                        f"Install local service: pip install vixio[silero-vad-local]"
                    )
    
    async def _start_local_service(self) -> None:
        """Start VAD service as subprocess"""
        self._local_port = self._find_free_port()
        
        # Try to import and run the service
        try:
            # Check if service package is available
            import importlib.util
            spec = importlib.util.find_spec("vixio_silero_vad_service")
            if spec is None:
                # Try running from inference directory
                import os
                inference_dir = os.path.join(
                    os.path.dirname(__file__), 
                    '..', '..', '..', '..', '..', 'inference', 'silero_vad'
                )
                inference_dir = os.path.abspath(inference_dir)
                
                if os.path.exists(os.path.join(inference_dir, 'server.py')):
                    # Run server from inference directory using uv
                    self._subprocess = subprocess.Popen(
                        ['uv', 'run', 'python', 'server.py', '--port', str(self._local_port)],
                        cwd=inference_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                else:
                    raise ImportError(
                        "VAD service not found. Install with: pip install vixio[silero-vad-local]"
                    )
            else:
                # Run installed package
                self._subprocess = subprocess.Popen(
                    ['python', '-m', 'vixio_silero_vad_service.server', '--port', str(self._local_port)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
        except Exception as e:
            raise RuntimeError(f"Failed to start VAD subprocess: {e}")
        
        # Register cleanup
        atexit.register(self._stop_subprocess)
        
        # Wait for service to be ready
        await asyncio.sleep(2)
        
        # Check if process is still running
        if self._subprocess.poll() is not None:
            stderr = self._subprocess.stderr.read().decode() if self._subprocess.stderr else ""
            raise RuntimeError(f"VAD subprocess exited unexpectedly: {stderr}")
        
        self.logger.info(f"VAD subprocess started on port {self._local_port}")
    
    def _stop_subprocess(self) -> None:
        """Stop the subprocess"""
        if self._subprocess and self._subprocess.poll() is None:
            self._subprocess.terminate()
            try:
                self._subprocess.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._subprocess.kill()
            self.logger.info("VAD subprocess stopped")
    
    async def _init_grpc(self, service_url: str) -> VADBackend:
        """Initialize gRPC client backend"""
        from vixio.providers.silero_vad.grpc_backend import GrpcSileroBackend
        
        backend = GrpcSileroBackend(
            session_id=self._session_id,
            service_url=service_url,
            threshold=self.threshold,
            threshold_low=self.threshold_low,
            frame_window_threshold=self.frame_window_threshold,
        )
        await backend.initialize()
        return backend
    
    async def detect(
        self,
        audio_data: bytes,
        event: VADEvent = VADEvent.CHUNK
    ) -> bool:
        """
        Detect voice activity in audio data.
        
        Args:
            audio_data: PCM audio bytes (16kHz, mono, 16-bit)
            event: VAD event type (START/CHUNK/END)
            
        Returns:
            True if voice detected, False otherwise
        """
        if self._backend is None:
            raise RuntimeError("Provider not initialized. Call initialize() first.")
        
        return await self._backend.detect(audio_data, event)
    
    async def reset(self) -> None:
        """Reset VAD state (clear buffers)"""
        if self._backend:
            await self._backend.reset()
    
    async def cleanup(self) -> None:
        """Cleanup provider resources"""
        if self._backend:
            await self._backend.cleanup()
            self._backend = None
        
        # Stop subprocess if running
        self._stop_subprocess()
        self._subprocess = None
        self._actual_mode = None
    
    def get_config(self) -> Dict[str, Any]:
        """Get provider configuration"""
        config = super().get_config()
        config.update({
            "mode": self.mode.value,
            "actual_mode": self._actual_mode.value if self._actual_mode else None,
            "service_url": self.service_url,
            "local_port": self._local_port,
            "threshold": self.threshold,
            "threshold_low": self.threshold_low,
            "frame_window_threshold": self.frame_window_threshold,
            "session_id": self._session_id,
        })
        return config
