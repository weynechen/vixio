"""
Example 3: Complete voice conversation with AI Agent

A full voice chat pipeline with VAD, ASR, Agent, sentence splitting, and TTS.
Demonstrates the complete integration of all components including LLM.

Usage:
    # Development mode (with local microservices)
    uv run python examples/agent_chat.py --env dev
    
    # Docker mode
    uv run python examples/agent_chat.py --env docker
    
    # Kubernetes mode
    uv run python examples/agent_chat.py --env k8s

Logger Configuration:
    Logger is auto-configured on import with INFO level, logging to logs/ directory.
    To customize, call configure_logger() before other imports:
    
    from utils import configure_logger
    configure_logger(level="DEBUG", log_dir="my_logs")
"""

import asyncio
import os
import sys
import signal
import atexit
import argparse
from pathlib import Path
from loguru import logger
from core.pipeline import Pipeline
from core.session import SessionManager
from transports.xiaozhi import XiaozhiTransport
from stations import (
    VADStation,
    TurnDetectorStation,
    ASRStation,
    TextAggregatorStation,
    AgentStation,
    SentenceSplitterStation,
    TTSStation
)
from providers.factory import ProviderFactory
from utils import get_local_ip
from utils.service_manager import ServiceManager

import dotenv

dotenv.load_dotenv()

# Global service manager for cleanup
_global_service_manager: ServiceManager = None


def cleanup_microservices():
    """
    Cleanup function called on exit.
    Ensures microservices are stopped even if main process crashes.
    """
    global _global_service_manager
    if _global_service_manager:
        logger.info("🧹 Cleaning up microservices...")
        try:
            _global_service_manager.stop_all()
            logger.info("✅ Microservices cleaned up")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        _global_service_manager = None


def signal_handler(signum, frame):
    """Handle termination signals"""
    logger.info(f"\n⚠️  Received signal {signum}, cleaning up...")
    cleanup_microservices()
    sys.exit(0)


# Register cleanup handlers
atexit.register(cleanup_microservices)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def main():
    """
    Complete voice conversation server with AI Agent.
    
    Pipeline flow:
    1. Client sends audio via WebSocket
    2. VAD detects voice activity
    3. TurnDetector waits for silence
    4. ASR transcribes to text
    5. Agent processes text and generates response (streaming)
    6. SentenceSplitter splits streaming text into sentences
    7. TTS synthesizes each sentence to audio
    8. Audio sent back to client via WebSocket
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Vixio AI Agent Voice Chat Server")
    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        choices=["dev", "docker", "k8s"],
        help="Deployment environment (default: dev)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to provider config file (default: config/providers.yaml)"
    )
    args = parser.parse_args()
    
    logger.info("=== Voice Chat with AI Agent ===")
    logger.info(f"Environment: {args.env}")
    
    # Step 0: In dev mode, auto-start local microservices
    service_manager = None
    if args.env == "dev":
        logger.info("")
        logger.info("🚀 Dev Mode: Auto-managing local microservices...")
        logger.info("")
        
        global _global_service_manager
        service_manager = ServiceManager(
            config_path=args.config or os.path.join(
                Path(__file__).parent.parent,
                "config/providers.yaml"
            ),
            env=args.env
        )
        _global_service_manager = service_manager  # Store globally for cleanup
        
        # Start services
        if not service_manager.start_all():
            logger.error("Failed to start microservices")
            cleanup_microservices()  # Clean up on failure
            sys.exit(1)
        
        # Print service info
        service_manager.print_service_info()
        logger.info("")
    
    # Step 1: Load provider configurations from file
    logger.info("Loading provider configurations...")
    
    config_path = args.config or os.path.join(
        Path(__file__).parent.parent,
        "config/providers.yaml"
    )
    
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
        
        # Check required providers
        if "vad" not in providers_dict:
            logger.error("VAD provider not configured!")
            return
        
        if "agent" not in providers_dict:
            logger.error("Agent provider not configured!")
            return
        
        if "tts" not in providers_dict:
            logger.error("TTS provider not configured!")
            return
        
        # ASR is optional (for testing)
        has_asr = "asr" in providers_dict
        if not has_asr:
            logger.warning("ASR provider not configured - text input only mode")
        
    except Exception as e:
        logger.error(f"Failed to load provider configurations: {e}")
        return
    
    # Agent system prompt
    agent_system_prompt = (
        "You are a helpful AI voice assistant. "
        "Keep your responses concise and conversational, "
        "as they will be spoken aloud to the user."
    )
    
    logger.info("✓ Configurations loaded")
    
    # Step 2: Create transport
    # WebSocket endpoint: ws://0.0.0.0:8000/xiaozhi/v1/
    # HTTP endpoints for monitoring
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000,
        websocket_path="/xiaozhi/v1/"
    )
    
    # Step 3: Create async pipeline factory
    async def create_pipeline():
        """
        Async factory function to create a fresh pipeline for each connection.
        
        Each session gets NEW provider instances created from the config,
        ensuring complete isolation between concurrent sessions.
        
        For gRPC providers (VAD, ASR, TTS):
        - Each instance creates its own gRPC client connection
        - Server-side session isolation via session IDs
        - VAD-cycle locking for stateful operations
        
        Returns:
            Pipeline: New pipeline with independent provider instances
        """
        logger.debug("Creating new pipeline with isolated providers...")
        
        # Create fresh provider instances for this session
        # Load providers from config again to get new instances
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        vad_provider = session_providers["vad"]
        await vad_provider.initialize()
        
        asr_provider = session_providers.get("asr")
        if asr_provider:
            await asr_provider.initialize()
        
        agent_provider = session_providers["agent"]
        await agent_provider.initialize(system_prompt=agent_system_prompt)
        
        tts_provider = session_providers["tts"]
        # TTS providers may not need explicit initialization
        if hasattr(tts_provider, "initialize"):
            await tts_provider.initialize()
        
        # Build station list
        stations = [
            # Stage 1: Voice detection
            VADStation(vad_provider),
            TurnDetectorStation(silence_threshold_ms=100),
        ]
        
        # Stage 2: Speech recognition (if available)
        if asr_provider:
            stations.append(ASRStation(asr_provider))
            # Text aggregator: Aggregate TEXT_DELTA from ASR into complete TEXT for Agent
            stations.append(TextAggregatorStation())
        
        # Stage 3: AI Agent processing (requires TEXT input)
        stations.append(AgentStation(agent_provider))
        
        # Stage 4: Sentence splitting for streaming TTS
        stations.append(SentenceSplitterStation(min_sentence_length=5))
        
        # Stage 5: Speech synthesis
        stations.append(TTSStation(tts_provider))
        
        logger.debug("✓ Pipeline created with isolated providers")
        
        return Pipeline(
            stations=stations,
            name="AgentVoiceChat"
        )
    
    # Step 4: Create session manager
    manager = SessionManager(
        transport=transport,
        pipeline_factory=create_pipeline
    )
    
    # Step 5: Start server
    # Get real IP address
    local_ip = get_local_ip()
    
    logger.info("=" * 70)
    logger.info("Vixio AI Agent Voice Chat Server")
    logger.info("=" * 70)
    logger.info(f"Environment: {args.env.upper()}")
    logger.info(f"")
    logger.info(f"Providers:")
    for category, provider in providers_dict.items():
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
    logger.info(f"  - Vision analysis: http://{local_ip}:{transport.port}/mcp/vision/explain")
    logger.info("")
    
    # Build pipeline description
    pipeline_stages = ["VAD", "TurnDetector"]
    if has_asr:
        pipeline_stages.extend(["ASR", "TextAggregator"])
    pipeline_stages.extend(["Agent", "SentenceSplitter", "TTS"])
    logger.info(f"Pipeline: {' -> '.join(pipeline_stages)}")
    logger.info("=" * 70)
    
    # Show deployment-specific notes
    if args.env == "dev":
        logger.info("📌 Dev Mode Notes:")
        logger.info("   - Ensure microservices are running: ./scripts/dev/start-all.sh")
        logger.info("   - VAD service: localhost:50051")
        if has_asr:
            logger.info("   - ASR service: localhost:50052")
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
    try:
        await asyncio.Event().wait()  # Wait forever
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        # Stop main server
        await manager.stop()
        logger.info("Server stopped")
        logger.info("Note: Each session's providers are cleaned up automatically")
        
        # Stop microservices (dev mode only)
        if service_manager:
            logger.info("")
            cleanup_microservices()  # Use cleanup function


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nExiting...")

