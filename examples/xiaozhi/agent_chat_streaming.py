"""
Example: Full Streaming voice conversation with AI Agent

A fully streaming voice chat using streaming models with continuous audio/text streaming.
This is the most efficient pipeline with minimal latency.

Full Streaming Pipeline (4 stations):
    Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio

Comparison:
    Pipeline:       Audio → VAD → TurnDetector → ASR → TextAgg → Agent → SentenceAgg → TTS → Audio (7 stations)
    Streaming Pipeline:  Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio (4 stations)


Requirements:
- API Key (e.x. Qwen ASR, Agent, TTS)
- pip install vixio[dev-xxx]

Configuration:
    Uses config/providers_xxx.yaml with 'dev-xxx' environment:
    - ASR: xxx-asr-flash-realtime (streaming mode, built-in VAD)
    - Agent: xxx-agent (via OpenAI-compatible API)
    - TTS: xxx-tts-flash-realtime (server_commit mode, streaming)
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
    StreamingASRStation,
    TextAggregatorStation,
    AgentStation,
    StreamingTTSStation,
    SentenceAggregatorStation
)
from vixio.providers.sentence_aggregator import SimpleSentenceAggregatorProviderCN
from vixio.providers.factory import ProviderFactory
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
    """Main entry point"""
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    parser = argparse.ArgumentParser(
        description="Streaming Voice Chat Example"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file (default: config/providers.yaml)"
    )
    parser.add_argument(
        "--env",
        type=str,
        default="dev-qwen-streaming",
        help="Provider environment (default: dev-qwen-streaming)"
    )
    
    args = parser.parse_args()
    
    logger.info("=== Streaming Voice Chat with AI Agent ===")
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
        
        # Check required providers
        if "agent" not in providers_dict:
            logger.error("Agent provider not configured!")
            return
        
        if "tts" not in providers_dict:
            logger.error("TTS provider not configured!")
            return
        
        # ASR is optional (for testing)
        has_asr = "asr" in providers_dict
        if not has_asr:
            logger.error("ASR provider not configured!")
            return
        
        # Verify streaming support
        asr = cast(ASRProvider, providers_dict.get("asr"))
        tts = cast(TTSProvider, providers_dict.get("tts"))
        
        if not (hasattr(asr, 'supports_streaming_input') and asr.supports_streaming_input):
            logger.warning("⚠️  ASR provider does not support streaming input!")
        if not (hasattr(asr, 'supports_vad') and asr.supports_vad):
            logger.warning("⚠️  ASR provider does not have built-in VAD!")
        if not (hasattr(tts, 'supports_streaming_input') and tts.supports_streaming_input):
            logger.warning("⚠️  TTS provider does not support streaming input!")
        
    except Exception as e:
        logger.error(f"Failed to load provider configurations: {e}")
        import traceback
        traceback.print_exc()
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
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8000,
        websocket_path="/xiaozhi/v1/",
        config=raw_config,
    )
    
    # Step 3: Create async DAG factory
    async def create_dag():
        """
        Streaming Pipeline (4 stations):
            Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio
        
        Returns:
            DAG: New DAG with independent provider instances
        """
        # Create fresh provider instances for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=args.env
        )
        
        asr_provider = cast(ASRProvider, session_providers["asr"])
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
        
        sentence_provider = SimpleSentenceAggregatorProviderCN(
            min_sentence_length=5,
            enable_conjunction_check=True,
            enable_punctuation_pairing=True,
            enable_incomplete_start_check=True,
        )
        await sentence_provider.initialize()

        # Build DAG
        dag = DAG("StreamingVoiceChat")
        
        # Add nodes (4 stations total)
        dag.add_node("streaming_asr", StreamingASRStation(asr_provider, timeout_seconds=30.0))
        dag.add_node("text_agg", TextAggregatorStation())
        dag.add_node("agent", AgentStation(agent_provider, timeout_seconds=60.0))
        dag.add_node("streaming_tts", StreamingTTSStation(tts_provider, timeout_seconds=30.0))
        dag.add_node("sentence_agg", SentenceAggregatorStation(provider=sentence_provider))
        # Define edges (data flow)
        # Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio
        dag.add_edge("transport_in", "streaming_asr")
        dag.add_edge("streaming_asr", "text_agg")
        dag.add_edge("text_agg", "agent")
        dag.add_edge("agent", "streaming_tts")
        dag.add_edge("streaming_tts", "transport_out")
        # STT result branch: text_agg -> transport_out (show STT text to client)
        dag.add_edge("text_agg", "transport_out")
        # agent result branch: agent -> sentence_agg -> transport_out (show agent text to client)
        dag.add_edge("agent", "sentence_agg")
        dag.add_edge("sentence_agg", "transport_out")
        
        return dag
    
    # Set DAG factory
    manager = SessionManager(
        transport=transport,
        dag_factory=create_dag,
        turn_timeout_seconds=None  # No turn timeout for streaming mode
    )
    
    # Step 4: Run server
    logger.info(f"\n{'='*60}")
    logger.info("🚀 Streaming Voice Chat Server Starting...")
    logger.info(f"{'='*60}")
    logger.info(f"📡 Server listening on: 0.0.0.0:8000")
    logger.info(f"🔌 WebSocket endpoint: ws://0.0.0.0:8000/xiaozhi/v1/")
    logger.info(f"🔧 Pipeline: Streaming (4 stations)")
    logger.info(f"⚡ Mode: Full streaming with built-in VAD")
    logger.info(f"📊 Expected TTFT: ~500-1000ms")
    logger.info(f"{'='*60}\n")
    
    # Step 5: Start the session manager
    await manager.start()
    
    try:
        logger.info("Server running. Press Ctrl+C to stop.")
        await _shutdown_event.wait()  # Wait for shutdown signal
        logger.info("\nShutting down...")
    except KeyboardInterrupt:
        logger.info("\n⚠️  Interrupted by user, shutting down...")
    except asyncio.CancelledError:
        logger.info("\nShutting down...")
    finally:
        # Stop main server
        await manager.stop()
        logger.info("✅ Server shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Silently handle Ctrl+C
    except SystemExit:
        pass  # Silently handle sys.exit()
