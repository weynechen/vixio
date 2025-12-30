"""
Example: Speech-to-Text Transcription Only

A minimal pipeline with VAD, ASR, and text aggregation for transcription.
No AI agent or TTS - just converts speech to text.

Usage:
    # Development mode (with local microservices)
    uv run python examples/transcribe.py --env dev
    
    # Docker mode
    uv run python examples/transcribe.py --env docker
    
    # Kubernetes mode
    uv run python examples/transcribe.py --env k8s

Logger Configuration:
    File logging is controlled by VIXIO_LOG_MODE environment variable:
    - VIXIO_LOG_MODE=file: Enable file logging (set by default in examples)
    - VIXIO_LOG_MODE=none or not set: Console only (default for CLI/uvx)
    
    To customize log level, call configure_logger() before other imports:
    
    from vixio.utils.logger_config import configure_logger, reset_logger
    
    # Reset auto-configured logger first
    reset_logger()
    
    # Option 1: Set DEBUG level for all components
    configure_logger(level="DEBUG", log_dir="my_logs")
    
    # Option 2: Enable DEBUG only for specific components
    configure_logger(
        level="INFO",  # Global level
        debug_components=["LatencyMonitor"],  # Only this component outputs DEBUG
        log_dir="logs"
    )
"""

import asyncio
import os
import signal
import argparse
from typing import cast
from loguru import logger
from vixio.core.dag import DAG
from vixio.core.session import SessionManager
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.stations import (
    VADStation,
    TurnDetectorStation,
    ASRStation,
    TextAggregatorStation,
)
from vixio.providers.factory import ProviderFactory
from vixio.providers.vad import VADProvider
from vixio.providers.asr import ASRProvider
from vixio.utils import get_local_ip
from vixio.config import get_default_config_path

import dotenv

dotenv.load_dotenv()

# Event to signal shutdown
_shutdown_event = None


def signal_handler(signum, frame):
    """Handle termination signals"""
    logger.info(f"\n⚠️  Received signal {signum}, shutting down gracefully...")
    if _shutdown_event:
        _shutdown_event.set()


# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def main():
    """
    Speech-to-text transcription server.
    
    Pipeline flow:
    1. Client sends audio via WebSocket
    2. VAD detects voice activity
    3. TurnDetector waits for silence
    4. ASR transcribes to text
    5. TextAggregator outputs complete text
    
    No AI agent or TTS processing - pure transcription only.
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Vixio Speech-to-Text Transcription Server")
    parser.add_argument(
        "--env",
        type=str,
        default="dev-grpc",
        choices=["dev-grpc", "dev-local-cn", "dev-qwen", "docker", "k8s"],
        help="Deployment environment (default: dev)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to provider config file (default: config/providers.yaml)"
    )
    parser.add_argument(
        "--debug-components",
        type=str,
        nargs="*",
        default=None,
        help="Enable DEBUG logging for specific components (e.g., LatencyMonitor InputValidator)"
    )
    parser.add_argument(
        "--turn-timeout",
        type=float,
        default=30.0,
        help="Turn inactivity timeout in seconds (default: 30.0). Set to 0 to disable."
    )
    args = parser.parse_args()
    
    # Configure logger with debug components if specified
    if args.debug_components:
        from vixio.utils.logger_config import reset_logger, configure_logger
        reset_logger()
        configure_logger(
            level="INFO",
            debug_components=args.debug_components
        )
        logger.info(f"Enabled DEBUG logging for: {', '.join(args.debug_components)}")
    
    logger.info("=== Speech-to-Text Transcription ===")
    logger.info(f"Environment: {args.env}")
    
    # Step 1: Load provider configurations from file
    logger.info("Loading provider configurations...")
    
    config_path = args.config or get_default_config_path()
    
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return
    
    try:
        # Load all providers from config
        providers_dict = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        logger.info(f"✓ Loaded providers: {list(providers_dict.keys())}")
        
        # Check required providers (only VAD and ASR for transcription)
        if "vad" not in providers_dict:
            logger.error("VAD provider not configured!")
            return
        
        if "asr" not in providers_dict:
            logger.error("ASR provider not configured! ASR is required for transcription.")
            return
        
    except Exception as e:
        logger.error(f"Failed to load provider configurations: {e}")
        return
    
    logger.info("✓ Configurations loaded")
    
    # Step 2: Create transport
    # WebSocket endpoint: ws://0.0.0.0:8000/xiaozhi/v1/
    # HTTP endpoints for monitoring
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000,
        websocket_path="/xiaozhi/v1/"
    )
    
    # Step 3: Create async DAG factory
    async def create_dag():
        """
        Async factory function to create a fresh DAG for each connection.
        
        Each session gets NEW provider instances created from the config,
        ensuring complete isolation between concurrent sessions.
        
        Returns:
            DAG: New DAG with independent provider instances
        """
        logger.debug("Creating new transcription DAG with isolated providers...")
        
        # Create fresh provider instances for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        vad_provider = cast(VADProvider, session_providers["vad"])
        await vad_provider.initialize()
        
        asr_provider = cast(ASRProvider, session_providers["asr"])
        await asr_provider.initialize()
        
        # Build DAG (transcription only - no agent or TTS)
        dag = DAG("Transcription")
        
        # Add nodes
        dag.add_node("vad", VADStation(vad_provider))
        dag.add_node("turn_detector", TurnDetectorStation(silence_threshold_ms=100))
        dag.add_node("asr", ASRStation(asr_provider))
        dag.add_node("text_agg", TextAggregatorStation())
        
        # Define edges (data flow)
        dag.add_edge("transport_in", "vad", "turn_detector", "asr", "text_agg", "transport_out")
        
        logger.debug("✓ Transcription DAG created")
        
        return dag
    
    # Step 4: Create session manager
    turn_timeout = args.turn_timeout if args.turn_timeout > 0 else None
    if turn_timeout:
        logger.info(f"Turn timeout enabled: {turn_timeout}s")
    else:
        logger.info("Turn timeout disabled")
    
    manager = SessionManager(
        transport=transport,
        dag_factory=create_dag,
        turn_timeout_seconds=turn_timeout
    )
    
    # Step 5: Start server
    # Get real IP address
    local_ip = get_local_ip()
    
    logger.info("=" * 70)
    logger.info("Vixio Speech-to-Text Transcription Server")
    logger.info("=" * 70)
    logger.info(f"Environment: {args.env.upper()}")
    logger.info(f"")
    logger.info(f"Providers:")
    # Only show VAD and ASR (TTS and Agent are not used)
    for category in ["vad", "asr"]:
        if category in providers_dict:
            provider = providers_dict[category]
            provider_type = "Local (gRPC)" if provider.is_local else "Remote (API)"
            logger.info(f"  - {category.upper():6s}: {provider.name:20s} [{provider_type}]")
    logger.info("")
    logger.info(f"WebSocket endpoint:")
    logger.info(f"  ws://{local_ip}:{transport.port}{transport.websocket_path}")
    logger.info(f"")
    logger.info(f"HTTP endpoints:")
    logger.info(f"  - Server info:     http://{local_ip}:{transport.port}/")
    logger.info(f"  - Health check:    http://{local_ip}:{transport.port}/health")
    logger.info(f"  - Connections:     http://{local_ip}:{transport.port}/connections")
    logger.info(f"  - OTA interface:   http://{local_ip}:{transport.port}/xiaozhi/ota/")
    logger.info("")
    
    # Processing flow description
    flow_stages = ["VAD", "TurnDetector", "ASR", "TextAggregator"]
    logger.info(f"Processing Flow: {' -> '.join(flow_stages)}")
    logger.info("=" * 70)
    
    # Show deployment-specific notes
    if args.env in ("dev-grpc", "dev-local-cn", "dev-qwen"):
        logger.info("📌 Dev Mode Notes:")
        logger.info(f"   - Using environment: {args.env}")
        logger.info("   - Ensure gRPC services are running if configured")
        logger.info("")
    elif args.env == "docker":
        logger.info("📌 Docker Mode Notes:")
        logger.info("   - Ensure Docker services are running: docker-compose up -d")
        logger.info("")
    elif args.env == "k8s":
        logger.info("📌 K8s Mode Notes:")
        logger.info("   - Services are auto-scaled by HPA (2-10 replicas)")
        logger.info("")
    
    await manager.start()
    
    # Step 6: Run until interrupted
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    try:
        logger.info("Server running. Press Ctrl+C to stop.")
        await _shutdown_event.wait()  # Wait for shutdown signal
        logger.info("\nShutting down...")
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    except asyncio.CancelledError:
        logger.info("\nShutting down...")
    finally:
        # Stop main server
        await manager.stop()
        logger.info("Server stopped")
        logger.info("Note: Each session's providers are cleaned up automatically")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Silently handle Ctrl+C
    except SystemExit:
        pass  # Silently handle sys.exit()

