"""
Example 3: Complete voice conversation with AI Agent (DAG Architecture)

A full voice chat DAG with VAD, ASR, Agent, sentence splitting, and TTS.
Demonstrates the complete integration of all components using DAG architecture.

DAG Architecture Benefits:
- Automatic routing based on ALLOWED_INPUT_TYPES
- No explicit passthrough logic needed
- Flexible branching and merging support
- Better separation of concerns

Usage:
    # Development mode - In-process inference (no external services needed)
    uv run python examples/pipeline.py --env dev-local-cn
    
    # Development mode - with gRPC microservices
    uv run python examples/pipeline.py --env dev-grpc 

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
        debug=["LatencyMonitor"],  # Only this component outputs DEBUG
        log_dir="logs"
    )
"""


import dotenv

dotenv.load_dotenv()

import asyncio
import os
import signal
import argparse
from typing import cast
from loguru import logger
from vixio.core.dag import DAG
from vixio.core.session import SessionManager
from vixio.core.tools import get_builtin_local_tools
from vixio.providers.agent import Tool, AgentProvider
from vixio.providers.vad import VADProvider
from vixio.providers.asr import ASRProvider
from vixio.providers.tts import TTSProvider
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.stations import (
    VADStation,
    TurnDetectorStation,
    ASRStation,
    TextAggregatorStation,
    AgentStation,
    SentenceAggregatorStation,
    TTSStation
)
from vixio.providers.factory import ProviderFactory
from vixio.providers.sentence_aggregator import SimpleSentenceAggregatorProviderCN
from vixio.utils import get_local_ip
from vixio.config import get_default_config_path


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
    Complete voice conversation server with AI Agent using DAG architecture.
    
    DAG data flow:
    1. Client sends audio via WebSocket (transport_in)
    2. VAD detects voice activity -> outputs AUDIO + VAD events
    3. TurnDetector waits for silence -> outputs EVENT_USER_STOPPED_SPEAKING
    4. ASR transcribes to text -> outputs TEXT_DELTA + EVENT_TEXT_COMPLETE
    5. TextAggregator collects deltas -> outputs TEXT
    6. Agent processes text -> outputs TEXT_DELTA (streaming)
    7. SentenceAggregator splits streaming -> outputs TEXT (sentences)
    8. TTS synthesizes -> outputs AUDIO + TTS events
    9. Audio sent back to client (transport_out)
    
    DAG routing:
    - Each node only processes chunks matching ALLOWED_INPUT_TYPES
    - No explicit passthrough needed
    - DAG handles automatic type-based routing
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Vixio AI Agent Voice Chat Server")
    parser.add_argument(
        "--env",
        type=str,
        default="dev-qwen-pipeline",
        choices=["dev-grpc", "dev-qwen-pipeline", "dev-in-process"],
        help="Deployment environment (default: dev)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to provider config file (default: config/providers.yaml)"
    )
    parser.add_argument(
        "--debug",
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
    if args.debug:
        from vixio.utils.logger_config import reset_logger, configure_logger
        reset_logger()
        configure_logger(
            level="INFO",
            debug_components=args.debug
        )
        logger.info(f"Enabled DEBUG logging for: {', '.join(args.debug)}")
    
    logger.info("=== Voice Chat with AI Agent ===")
    logger.info(f"Environment: {args.env}")
    
    # Step 1: Load provider configurations from file
    logger.info("Loading provider configurations...")
    
    config_path = args.config or get_default_config_path()
    
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return
    
    # Load raw config for transport (contains server settings, providers, etc.)
    import yaml
    with open(config_path, encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)
    
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
        "You should always respond with a short sentence first, ending with a period, not a comma."
    )
    
    logger.info("✓ Configurations loaded")
    
    # Step 2: Create transport
    # WebSocket endpoint: ws://0.0.0.0:8000/xiaozhi/v1/
    # HTTP endpoints for monitoring
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000,
        websocket_path="/xiaozhi/v1/",
        config=raw_config,  # Pass config for VLM and other settings
    )
    
    # Step 3: Create async DAG factory
    async def create_dag():
        """
        Async factory function to create a fresh DAG for each connection.
        
        Each session gets NEW provider instances created from the config,
        ensuring complete isolation between concurrent sessions.
        
        DAG Architecture:
        - Nodes process only chunks matching their ALLOWED_INPUT_TYPES
        - No explicit passthrough needed - DAG handles routing
        - Automatic type-based filtering at each node
        
        Returns:
            DAG: New DAG with independent provider instances
        """
        logger.debug("Creating new DAG with isolated providers...")
        
        # Create fresh provider instances for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        vad_provider = cast(VADProvider, session_providers["vad"])
        await vad_provider.initialize()
        
        asr_provider = cast(ASRProvider, session_providers.get("asr")) if "asr" in session_providers else None
        if asr_provider:
            await asr_provider.initialize()
        
        agent_provider = cast(AgentProvider, session_providers["agent"])
        
        # Convert builtin local tools to Agent Tool format
        local_tool_defs = get_builtin_local_tools()
        local_tools = [
            Tool(
                name=td.name,
                description=td.description,
                parameters=td.parameters,
                executor=td.executor
            )
            for td in local_tool_defs
        ]
        logger.info(f"Loaded {len(local_tools)} local tools: {[t.name for t in local_tools]}")
        
        await agent_provider.initialize(
            tools=local_tools,
            system_prompt=agent_system_prompt
        )
        
        tts_provider = cast(TTSProvider, session_providers["tts"])
        if hasattr(tts_provider, "initialize"):
            await tts_provider.initialize()
        
        # Build DAG
        dag = DAG("AgentVoiceChat")
        
        # Add nodes
        dag.add_node("vad", VADStation(vad_provider))
        dag.add_node("turn_detector", TurnDetectorStation(silence_threshold_ms=100))
        
        if asr_provider:
            dag.add_node("asr", ASRStation(asr_provider))
            dag.add_node("text_agg", TextAggregatorStation())
        
        dag.add_node("agent", AgentStation(agent_provider))
        
        # Create sentence aggregator provider (enhanced rule-based Chinese)
        sentence_provider = SimpleSentenceAggregatorProviderCN(
            min_sentence_length=5,
            enable_conjunction_check=True,
            enable_punctuation_pairing=True,
            enable_incomplete_start_check=True,
        )
        await sentence_provider.initialize()
        
        dag.add_node("sentence_agg", SentenceAggregatorStation(provider=sentence_provider))
        dag.add_node("tts", TTSStation(tts_provider))
        
        # Define edges (data flow)
        # DAG automatically routes based on ALLOWED_INPUT_TYPES
        if asr_provider:
            # Main flow: transport_in -> vad -> turn_detector -> asr -> ... -> transport_out
            dag.add_edge("transport_in", "vad", "turn_detector", "asr", "text_agg", "agent", "sentence_agg", "tts", "transport_out")
            # STT result branch: asr -> transport_out (show STT text to client)
            dag.add_edge("text_agg", "transport_out")
        else:
            raise ValueError("ASR provider not configured")
        
        logger.debug("✓ DAG created with isolated providers")
        
        return dag
    
    # Step 4: Create session manager with DAG factory
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
    logger.info("Vixio AI Agent Voice Chat Server")
    logger.info("=" * 70)
    logger.info(f"Environment: {args.env.upper()}")
    logger.info(f"")
    logger.info(f"Providers:")
    for category, provider in providers_dict.items():
        if provider.is_local:
            # Distinguish between in-process and gRPC local providers
            if "-local" in provider.name and "-grpc" not in provider.name:
                provider_type = "Local (In-Process)"
            else:
                provider_type = "Local (gRPC)"
        else:
            provider_type = "Remote (API)"
        logger.info(f"  - {category.upper():6s}: {provider.name:20s} [{provider_type}]")
    logger.info("")
    logger.info(f"  - OTA interface:   http://{local_ip}:{transport.port}/xiaozhi/ota/")
    logger.info("")
    
    # Build DAG description
    dag_nodes = ["VAD", "TurnDetector"]
    if has_asr:
        dag_nodes.extend(["ASR", "TextAggregator"])
    dag_nodes.extend(["Agent", "SentenceAggregator", "TTS"])
    logger.info(f"DAG: {' -> '.join(dag_nodes)}")
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

