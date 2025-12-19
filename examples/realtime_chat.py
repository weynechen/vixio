"""
Example: End-to-End Real-time Voice Conversation (Qwen Omni Realtime)

A simplified voice chat using Qwen-Omni-Realtime model which integrates
VAD + ASR + LLM + TTS in a single model. No separate processing stages needed.

DAG Architecture:
    transport_in -> RealtimeStation -> transport_out

The Qwen-Omni-Realtime model handles:
- Voice Activity Detection (VAD): Auto-detects when user starts/stops speaking
- Speech Recognition (ASR): Transcribes user speech
- Language Model (LLM): Generates response
- Text-to-Speech (TTS): Synthesizes response audio

All processing happens server-side with automatic turn management.

Usage:
    # Requires DashScope API key
    export DASHSCOPE_API_KEY="your-api-key"
    
    # Run with dev-realtime environment
    uv run python examples/realtime_chat.py --env dev-realtime
    
    # Or specify config directly
    uv run python examples/realtime_chat.py --config config/providers.yaml --env dev-realtime

Reference:
    https://help.aliyun.com/zh/model-studio/realtime
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
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.stations import RealtimeStation, SentenceAggregatorStation
from vixio.providers.factory import ProviderFactory
from vixio.providers.realtime import BaseRealtimeProvider
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
    Real-time voice conversation server using Qwen-Omni-Realtime.
    
    The realtime model integrates VAD + ASR + LLM + TTS in one model,
    providing end-to-end voice conversation with minimal latency.
    
    Simple DAG:
        transport_in -> realtime -> transport_out
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Vixio Realtime Voice Chat Server")
    parser.add_argument(
        "--env",
        type=str,
        default="dev-realtime",
        choices=["dev-realtime"],
        help="Deployment environment (default: dev-realtime)"
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
        help="Enable DEBUG logging for specific components"
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
    
    logger.info("=== Real-time Voice Chat with Qwen Omni ===")
    logger.info(f"Environment: {args.env}")
    
    # Step 1: Load provider configurations from file
    logger.info("Loading provider configurations...")
    
    config_path = args.config or get_default_config_path()
    
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return
    
    # Load raw config for transport
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
        
        # Check required provider
        if "realtime" not in providers_dict:
            logger.error("Realtime provider not configured!")
            logger.error("Make sure you're using --env dev-realtime")
            return
        
    except Exception as e:
        logger.error(f"Failed to load provider configurations: {e}")
        import traceback
        traceback.print_exc()
        return
    
    logger.info("✓ Configurations loaded")
    
    # Step 2: Create transport
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000,
        websocket_path="/xiaozhi/v1/",
        config=raw_config,
    )
    
    # Step 3: Create async DAG factory
    async def create_dag():
        """
        Async factory function to create a fresh DAG for each connection.
        
        The realtime DAG is simple:
            transport_in -> realtime -> transport_out
        
        The RealtimeStation handles all audio processing internally.
        
        Returns:
            DAG: New DAG with realtime station
        """
        logger.debug("Creating new DAG with realtime provider...")
        
        # Create fresh provider instance for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        realtime_provider = cast(BaseRealtimeProvider, session_providers["realtime"])
        
        # Initialize provider (establishes WebSocket connection)
        await realtime_provider.initialize()
        
        # Build simple DAG
        dag = DAG("RealtimeVoiceChat")
        
        # Add realtime station
        dag.add_node("realtime", RealtimeStation(
            provider=realtime_provider,
            emit_text=True,      # Emit text deltas for display
            emit_events=True,    # Emit VAD/TTS events
        ))
        
        # Add sentence aggregator for Xiaozhi device compatibility (aggregates text deltas)
        # Create sentence aggregator provider (enhanced rule-based Chinese)
        sentence_provider = SimpleSentenceAggregatorProviderCN(
            min_sentence_length=5,
            enable_conjunction_check=False,
            enable_punctuation_pairing=True,
            enable_incomplete_start_check=True,
        )
        await sentence_provider.initialize()
        
        dag.add_node("sentence_aggregator", SentenceAggregatorStation(
            provider=sentence_provider
        ))
        
        # Define edges: 
        # 1. transport_in -> realtime
        dag.add_edge("transport_in", "realtime")
        
        # 2. realtime -> transport_out (for Audio and Events)
        # AUDIO_RAW and Events go directly to transport_out
        dag.add_edge("realtime", "transport_out")
        
        # 3. realtime -> sentence_aggregator -> transport_out (for TEXT_DELTA -> TEXT)
        # TEXT_DELTA goes through aggregator to become complete sentences
        dag.add_edge("realtime", "sentence_aggregator")
        dag.add_edge("sentence_aggregator", "transport_out")

        
        logger.debug("✓ DAG created with realtime provider and text aggregator")
        
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
    local_ip = get_local_ip()
    
    logger.info("=" * 70)
    logger.info("Vixio Realtime Voice Chat Server")
    logger.info("=" * 70)
    logger.info(f"Environment: {args.env.upper()}")
    logger.info(f"")
    logger.info(f"Providers:")
    for category, provider in providers_dict.items():
        if provider.is_local:
            provider_type = "Local"
        else:
            provider_type = "Remote (API)"
        logger.info(f"  - {category.upper():8s}: {provider.name:25s} [{provider_type}]")
    logger.info("")
    logger.info(f"Model Features:")
    logger.info(f"  - Integrated VAD: Server-side voice activity detection")
    logger.info(f"  - Integrated ASR: Real-time speech transcription")
    logger.info(f"  - Integrated LLM: Language model response generation")
    logger.info(f"  - Integrated TTS: Text-to-speech synthesis")
    logger.info("")
    logger.info(f"WebSocket endpoint:")
    logger.info(f"  ws://{local_ip}:{transport.port}{transport.websocket_path}")
    logger.info(f"")
    logger.info(f"HTTP endpoints:")
    logger.info(f"  - Server info:  http://{local_ip}:{transport.port}/")
    logger.info(f"  - Health check: http://{local_ip}:{transport.port}/health")
    logger.info(f"  - Connections:  http://{local_ip}:{transport.port}/connections")
    logger.info("")
    logger.info(f"DAG: transport_in -> RealtimeStation -> transport_out")
    logger.info("=" * 70)
    
    logger.info("📌 Realtime Mode Notes:")
    logger.info("   - Using Qwen-Omni-Realtime model (end-to-end voice)")
    logger.info("   - Server-side VAD automatically detects speech")
    logger.info("   - No separate ASR/TTS providers needed")
    logger.info("   - Requires DASHSCOPE_API_KEY environment variable")
    logger.info("")
    
    await manager.start()
    
    # Step 6: Run until interrupted
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    try:
        logger.info("Server running. Press Ctrl+C to stop.")
        await _shutdown_event.wait()
        logger.info("\nShutting down...")
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    except asyncio.CancelledError:
        logger.info("\nShutting down...")
    finally:
        await manager.stop()
        logger.info("Server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass
