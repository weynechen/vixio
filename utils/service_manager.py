"""
Service Manager for Development Mode

Automatically starts and stops local microservices based on configuration.
Used by examples/agent_chat.py in dev mode.
"""

import os
import subprocess
import time
import signal
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


class ServiceInfo:
    """Information about a microservice"""
    def __init__(self, name: str, directory: str, port: int, display_name: str, extra_args: Optional[List[str]] = None):
        self.name = name
        self.directory = directory
        self.port = port
        self.display_name = display_name
        self.extra_args = extra_args or []
        self.process: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None


class ServiceManager:
    """
    Manages local microservices in development mode.
    
    Automatically:
    - Parses config/providers.yaml
    - Identifies local (gRPC) services
    - Starts services in background
    - Monitors service health
    - Stops services on cleanup
    """
    
    # Mapping: provider name -> (service_dir, port, display_name, extra_args)
    SERVICE_MAP = {
        "silero-vad-grpc": ("silero_vad", 50051, "Silero VAD", []),
        "sherpa-onnx-asr-grpc": (
            "sherpa_onnx_local", 
            50052, 
            "Sherpa ONNX ASR", 
            ["--model-path", "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"]
        ),
        "kokoro-tts-grpc": ("kokoro", 50053, "Kokoro TTS", []),
    }
    
    def __init__(self, config_path: str = "config/providers.yaml", env: str = "dev"):
        """
        Initialize service manager.
        
        Args:
            config_path: Path to providers config file
            env: Environment (dev/docker/k8s)
        """
        self.config_path = Path(config_path)
        self.env = env
        self.services: List[ServiceInfo] = []
        self.project_root = Path.cwd()
        
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
    
    def discover_services(self) -> List[ServiceInfo]:
        """
        Discover local services from configuration.
        
        Returns:
            List of services to start
        """
        with open(self.config_path) as f:
            config = yaml.safe_load(f)
        
        env_config = config.get(self.env, {})
        providers_config = env_config.get("providers", {})
        
        services = []
        for category, provider_config in providers_config.items():
            provider_name = provider_config.get("provider")
            
            if provider_name in self.SERVICE_MAP:
                service_dir, port, display_name, extra_args = self.SERVICE_MAP[provider_name]
                service_path = self.project_root / "inference" / service_dir
                
                if not service_path.exists():
                    logger.warning(f"Service directory not found: {service_path} (skipping)")
                    continue
                
                service = ServiceInfo(
                    name=service_dir,
                    directory=str(service_path),
                    port=port,
                    display_name=display_name,
                    extra_args=extra_args
                )
                services.append(service)
                logger.debug(f"Discovered service: {display_name} ({provider_name})")
        
        return services
    
    def ensure_dependencies(self, service: ServiceInfo) -> bool:
        """
        Ensure service dependencies are installed.
        
        Args:
            service: Service information
            
        Returns:
            True if dependencies are ready
        """
        # Always run uv sync to ensure dependencies are up to date
        # uv sync is fast if nothing needs to be done
        logger.debug(f"  Ensuring dependencies for {service.display_name}...")
        
        try:
            # Run uv sync to install/update dependencies
            result = subprocess.run(
                ["uv", "sync"],
                cwd=service.directory,
                capture_output=True,
                text=True,
                timeout=120  # 120 seconds timeout for first install
            )
            
            if result.returncode != 0:
                logger.error(f"  ✗ Failed to sync dependencies:")
                logger.error(f"     {result.stderr}")
                return False
            
            # Check if dependencies were installed
            venv_path = Path(service.directory) / ".venv"
            if not venv_path.exists():
                logger.error(f"  ✗ Virtual environment not created at {venv_path}")
                return False
            
            logger.debug(f"  ✓ Dependencies ready")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error(f"  ✗ Dependency installation timeout (120s)")
            return False
        except Exception as e:
            logger.error(f"  ✗ Failed to ensure dependencies: {e}")
            return False
    
    def start_service(self, service: ServiceInfo) -> bool:
        """
        Start a single service.
        
        Args:
            service: Service information
            
        Returns:
            True if started successfully
        """
        logger.info(f"Starting {service.display_name}...")
        
        # # Ensure dependencies are installed
        # if not self.ensure_dependencies(service):
        #     logger.error(f"  ✗ Failed to ensure dependencies for {service.display_name}")
        #     return False
        
        # Create logs directory
        logs_dir = self.project_root / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        log_file = logs_dir / f"{service.name}.log"
        
        try:
            # Open log file
            log_fd = open(log_file, "w")
            
            # Prepare clean environment (remove parent's VIRTUAL_ENV)
            env = os.environ.copy()
            # Remove VIRTUAL_ENV to let uv use the service's own venv
            env.pop('VIRTUAL_ENV', None)
            env.pop('VIRTUAL_ENV_PROMPT', None)
            
            # Use script mode: directly run server.py with the service's own venv
            # This avoids path dependency issues with package mode (--project)
            python_path = Path(service.directory) / ".venv" / "bin" / "python"
            
            if not python_path.exists():
                logger.error(f"  ✗ Python interpreter not found: {python_path}")
                log_fd.close()
                return False
            
            # Build command with extra args
            cmd = [str(python_path), "server.py", 
                   "--port", str(service.port), "--log-level", "INFO"]
            cmd.extend(service.extra_args)
            
            # Debug: log the command we're running
            logger.debug(f"  Starting command: {' '.join(cmd)}")
            logger.debug(f"  Working directory: {service.directory}")
            
            # Start service process in script mode
            process = subprocess.Popen(
                cmd,
                cwd=service.directory,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,  # Use clean environment
                start_new_session=True  # Create new process group
            )
            
            service.process = process
            service.pid = process.pid
            
            logger.info(f"  ✓ {service.display_name} started (PID: {service.pid}, Port: {service.port})")
            
            return True
        
        except Exception as e:
            logger.error(f"  ✗ Failed to start {service.display_name}: {e}")
            return False
    
    def wait_for_services(self, timeout: float = 5.0) -> bool:
        """
        Wait for all services to be ready.
        
        Args:
            timeout: Maximum wait time in seconds
            
        Returns:
            True if all services are running
        """
        logger.info(f"Waiting for services to be ready (timeout: {timeout}s)...")
        
        start_time = time.time()
        interval = 0.5
        
        while time.time() - start_time < timeout:
            all_running = True
            
            for service in self.services:
                if service.process is None:
                    all_running = False
                    break
                
                # Check if process is still running
                if service.process.poll() is not None:
                    logger.error(f"  ✗ {service.display_name} (PID: {service.pid}) exited unexpectedly")
                    all_running = False
                    break
            
            if all_running:
                logger.info("  ✓ All services ready")
                return True
            
            time.sleep(interval)
        
        logger.error(f"  ✗ Service startup timeout after {timeout}s")
        return False
    
    def verify_services(self) -> bool:
        """
        Verify all services are running.
        
        Returns:
            True if all services are healthy
        """
        logger.info("Verifying services...")
        
        all_ok = True
        for service in self.services:
            if service.process and service.process.poll() is None:
                logger.info(f"  ✓ {service.display_name} (PID: {service.pid}) - Running")
            else:
                logger.error(f"  ✗ {service.display_name} - Not running")
                all_ok = False
        
        return all_ok
    
    def start_all(self) -> bool:
        """
        Start all required services.
        
        Returns:
            True if all services started successfully
        """
        # Discover services
        self.services = self.discover_services()
        
        if not self.services:
            logger.info("No local microservices configured")
            logger.info("Only remote providers will be used")
            return True
        
        logger.info(f"Starting {len(self.services)} microservice(s)...")
        logger.info("(First-time setup may take a minute to install dependencies...)")
        logger.info("")
        
        # Start each service
        for service in self.services:
            if not self.start_service(service):
                logger.error("Failed to start services")
                self.stop_all()
                return False
            
            # Wait a bit between services
            time.sleep(0.5)
        
        # Wait for services to be ready
        if not self.wait_for_services(timeout=5.0):
            logger.error("Services failed to start in time")
            self.stop_all()
            return False
        
        # Verify services
        if not self.verify_services():
            logger.error("Service verification failed")
            self.stop_all()
            return False
        
        logger.info("=" * 60)
        logger.info("✅ All microservices started successfully!")
        logger.info("=" * 60)
        
        return True
    
    def stop_service(self, service: ServiceInfo) -> None:
        """
        Stop a single service.
        
        Args:
            service: Service information
        """
        if service.process is None:
            return
        
        try:
            # Try graceful shutdown first (SIGTERM)
            service.process.terminate()
            
            # Wait up to 3 seconds for graceful shutdown
            try:
                service.process.wait(timeout=3.0)
                logger.info(f"  ✓ Stopped {service.display_name} (PID: {service.pid})")
            except subprocess.TimeoutExpired:
                # Force kill if still running
                logger.warning(f"  ⚠ Force killing {service.display_name} (PID: {service.pid})")
                service.process.kill()
                service.process.wait()
        
        except Exception as e:
            logger.error(f"  ✗ Error stopping {service.display_name}: {e}")
    
    def stop_all(self) -> None:
        """Stop all running services."""
        if not self.services:
            return
        
        logger.info(f"Stopping {len(self.services)} microservice(s)...")
        
        for service in self.services:
            self.stop_service(service)
        
        logger.info("✅ All microservices stopped")
    
    def print_service_info(self) -> None:
        """Print information about running services."""
        if not self.services:
            return
        
        logger.info("")
        logger.info("📝 Service Logs:")
        for service in self.services:
            logger.info(f"   tail -f logs/{service.name}.log  # {service.display_name}")

