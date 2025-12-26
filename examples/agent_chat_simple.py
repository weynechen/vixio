"""
Example: Simplified voice conversation with AI Agent (Qwen Realtime)

A simplified voice chat using Qwen's realtime models with built-in VAD and streaming.
This version requires fewer stations and has lower latency compared to the full pipeline.

Simplified Pipeline:
    Audio → ASR(with VAD) → TextAggregator → Agent → TTS(streaming) → Audio

Comparison with Full Pipeline:
    Full:       Audio → VAD → TurnDetector → ASR → TextAgg → Agent → SentenceAgg → TTS → Audio (7 stations)
    Simplified: Audio → ASR(VAD) → TextAggregator → Agent → TTS(streaming) → Audio (4 stations)

Benefits:
- No external VAD dependency (uses ASR built-in VAD)
- No TurnDetector needed (ASR handles turn detection)
- No SentenceAggregator needed (TTS handles segmentation)
- Fewer stations: 4 vs 7 (43% reduction)
- Faster TTFT (Time To First Token): ~800-1500ms vs ~2000ms
- Simpler configuration and fewer moving parts

Requirements:
- DashScope API Key (for Qwen ASR, Agent, TTS)
- pip install vixio[dev-qwen]

Usage:
    # Using Qwen providers with simplified pipeline
    uv run python examples/agent_chat_simple.py
    
    # Or specify environment explicitly
    uv run python examples/agent_chat_simple.py --env dev-qwen

Configuration:
    Uses config/providers.yaml with 'dev-qwen' environment:
    - ASR: qwen3-asr-flash-realtime (with built-in VAD)
    - Agent: qwen-plus (via OpenAI-compatible API)
    - TTS: qwen3-tts-flash-realtime (server_commit mode)

Logger Configuration:
    Logger is auto-configured on import with INFO level, logging to logs/ directory.
    To customize, call configure_logger() before other imports:
    
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
from vixio.providers.asr import ASRProvider
from vixio.providers.tts import TTSProvider
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.stations import (
    ASRStation,
    TextAggregatorStation,
    AgentStation,
    TTSStation
)
from vixio.providers.factory import ProviderFactory
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
    Simplified voice conversation server with AI Agent using Qwen Realtime models.
    
    Simplified DAG data flow:
    1. Client sends audio via WebSocket (transport_in)
    2. ASR transcribes with built-in VAD -> outputs TEXT_DELTA + EVENT_STREAM_COMPLETE
    3. TextAggregator collects TEXT_DELTA -> outputs TEXT (on completion)
    4. Agent processes text -> outputs TEXT_DELTA (streaming)
    5. TTS synthesizes with auto-segmentation -> outputs AUDIO + TTS events
    6. Audio sent back to client (transport_out)
    
    Key differences from full pipeline (7 stations → 4 stations):
    ✗ VAD station removed (ASR has built-in VAD)
    ✗ TurnDetector removed (ASR handles turn detection)
    ✓ TextAggregator kept (still needed: TEXT_DELTA → TEXT)
    ✗ SentenceAggregator removed (TTS handles segmentation)
    
    Result: 43% fewer stations, lower latency, simpler configuration
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Vixio AI Agent Voice Chat Server (Simplified)")
    parser.add_argument(
        "--env",
        type=str,
        default="dev-qwen-simplified",
        choices=["dev-qwen-simplified"],
        help="Deployment environment (default: dev-qwen-simplified)"
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
    
    logger.info("=== Voice Chat with AI Agent (Simplified Pipeline) ===")
    logger.info(f"Environment: {args.env}")
    
    # Step 1: Load provider configurations from file
    logger.info("Loading provider configurations...")
    
    config_path = args.config or get_default_config_path()
    
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return
    
    # Load raw config for transport (contains server settings, providers, etc.)
    import yaml
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)
    
    try:
        # Load all providers from config
        providers_dict = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        logger.info(f"✓ Loaded providers: {list(providers_dict.keys())}")
        
        # Check required providers
        if "asr" not in providers_dict:
            logger.error("ASR provider not configured!")
            logger.error("Simplified pipeline requires ASR with built-in VAD (e.g., qwen3-asr-flash-realtime)")
            return
        
        if "agent" not in providers_dict:
            logger.error("Agent provider not configured!")
            return
        
        if "tts" not in providers_dict:
            logger.error("TTS provider not configured!")
            return
        
    except Exception as e:
        logger.error(f"Failed to load provider configurations: {e}")
        return
    
    # Agent system prompt
    agent_system_prompt = (
        "You are a helpful AI voice assistant. "
        "Keep your responses concise and conversational, "
        "as they will be spoken aloud to the user. "
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
        
        Simplified DAG Architecture:
        - 4 stations: ASR → TextAgg → Agent → TTS (vs 7 in full pipeline)
        - ASR handles VAD and turn detection internally (removes 2 stations)
        - TTS handles text segmentation internally (removes 1 station)
        - TextAggregator still needed (ASR→TEXT_DELTA, Agent→TEXT)
        - Lower latency, simpler configuration
        
        Returns:
            DAG: New DAG with independent provider instances
        """
        logger.debug("Creating new simplified DAG with isolated providers...")
        
        # Create fresh provider instances for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        asr_provider = cast(ASRProvider, session_providers["asr"])
        await asr_provider.initialize()
        
        # Check if ASR supports VAD
        if not hasattr(asr_provider, 'supports_vad') or not asr_provider.supports_vad:
            logger.warning(
                "ASR provider does not support built-in VAD. "
                "Simplified pipeline works best with providers that have VAD (e.g., qwen3-asr-flash-realtime)"
            )
        
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
        
        # Check if TTS supports streaming input
        if not hasattr(tts_provider, 'supports_streaming_input') or not tts_provider.supports_streaming_input:
            logger.warning(
                "TTS provider does not support streaming input. "
                "Simplified pipeline works best with providers that support streaming (e.g., qwen3-tts-flash-realtime in server_commit mode)"
            )
        
        # Build simplified DAG
        dag = DAG("AgentVoiceChatSimplified")
        
        # Add nodes
        # Note: Still need TextAggregator because:
        # - ASR outputs TEXT_DELTA (streaming)
        # - Agent requires TEXT (complete)
        # The "simplified" part is: no VAD, no TurnDetector, no SentenceAggregator
        dag.add_node("asr", ASRStation(
            asr_provider,
            context_source=None,  # Can be "previous_turn" or "session_context" if ASR supports context
            timeout_seconds=10.0
        ))
        
        dag.add_node("text_agg", TextAggregatorStation())
        
        dag.add_node("agent", AgentStation(agent_provider))
        
        # TTS station with streaming mode enabled
        dag.add_node("tts", TTSStation(
            tts_provider,
            use_streaming_mode=False,  # Set to True if TTS provider supports it
            timeout_seconds=15.0
        ))
        
        # Define edges (data flow)
        # Simplified flow: transport_in -> asr -> text_agg -> agent -> tts -> transport_out
        # Compared to full pipeline:
        #   - No VAD (ASR has built-in VAD)
        #   - No TurnDetector (ASR handles turn detection)
        #   - No SentenceAggregator (TTS handles segmentation in server_commit mode)
        dag.add_edge("transport_in", "asr", "text_agg", "agent", "tts", "transport_out")
        
        # STT result branch: text_agg -> transport_out (show complete STT text to client)
        dag.add_edge("text_agg", "transport_out")
        
        logger.debug("✓ Simplified DAG created with isolated providers")
        
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
    logger.info("Vixio AI Agent Voice Chat Server (Simplified Pipeline)")
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
    
    # Simplified DAG description
    logger.info(f"Simplified DAG: ASR(VAD) → TextAgg → Agent → TTS(streaming)")
    logger.info(f"  ✗ VAD removed (built into ASR)")
    logger.info(f"  ✗ TurnDetector removed (ASR handles it)")
    logger.info(f"  ✓ TextAggregator kept (TEXT_DELTA → TEXT)")
    logger.info(f"  ✗ SentenceAggregator removed (TTS handles it)")
    logger.info(f"  → 4 stations vs 7 in full pipeline (43% reduction)")
    logger.info(f"  → Expected TTFT: ~800-1500ms (vs ~2000ms for full pipeline)")
    logger.info("=" * 70)
    
    # Show deployment-specific notes
    logger.info("📌 Simplified Pipeline Notes:")
    logger.info(f"   - Using Qwen realtime models with built-in optimizations")
    logger.info(f"   - ASR: Built-in VAD + context enhancement support")
    logger.info(f"   - TTS: Auto-segmentation + streaming input support")
    logger.info(f"   - Fewer stations = lower latency and simpler debugging")
    logger.info(f"")
    logger.info(f"📌 Why TextAggregator is still needed:")
    logger.info(f"   - ASR outputs TEXT_DELTA (streaming text chunks)")
    logger.info(f"   - Agent requires TEXT (complete user message)")
    logger.info(f"   - TextAggregator bridges this gap")
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
