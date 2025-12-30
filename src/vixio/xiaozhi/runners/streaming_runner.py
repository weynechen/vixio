"""
Streaming mode server runner.

Extracted from examples/agent_chat_streaming.py for reusability.

Streaming Pipeline (4 stations):
    Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio

Benefits:
- Continuous audio streaming (no buffering for VAD/TurnDetector)
- ASR built-in VAD (no external VAD dependency)
- TTS intelligent segmentation (no SentenceAggregator)
- Lowest latency: ~500-1000ms TTFT
- Simplest pipeline: only 4 stations

Trade-offs:
- Requires stable network connection
- ASR's built-in VAD may be less accurate than Silero
- More complex error recovery
- Requires provider support for streaming
"""

import asyncio
import os
import signal
from typing import Optional, cast
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
from vixio.providers.factory import ProviderFactory
from vixio.providers.sentence_aggregator import SimpleSentenceAggregatorProviderCN
from vixio.utils import get_local_ip


# Event to signal shutdown
_shutdown_event = None
_original_sigint_handler = None
_original_sigterm_handler = None


def _make_signal_handler(shutdown_event):
    """Create a signal handler with the given shutdown event"""
    def signal_handler(signum, frame):
        """Handle termination signals"""
        logger.info(f"\nReceived signal {signum}, shutting down gracefully...")
        if shutdown_event and not shutdown_event.is_set():
            shutdown_event.set()
        else:
            # Force exit if event already set (second Ctrl+C)
            import sys
            logger.warning("Force exit!")
            sys.exit(0)
    return signal_handler


async def run_streaming_server(
    config_path: str,
    env: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8000,
    asr_timeout: float = 30.0,
    agent_timeout: float = 60.0,
    tts_timeout: float = 30.0,
    prompt: Optional[str] = None,
):
    """
    Run streaming mode server (StreamingASR + Agent + StreamingTTS).
    
    This is the most efficient pipeline with minimal latency, using 4 stations:
        Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio
    
    Args:
        config_path: Path to provider config file
        env: Environment name in config (e.g., "dev-qwen-streaming")
        host: Server host address
        port: Server port
        asr_timeout: ASR station timeout in seconds
        agent_timeout: Agent station timeout in seconds
        tts_timeout: TTS station timeout in seconds
        prompt: Custom system prompt for agent
    """
    logger.info("=== Voice Chat with AI Agent (Streaming Mode) ===")
    if env:
        logger.info(f"Environment: {env}")
    
    # Load provider configurations
    logger.info("Loading provider configurations...")
    
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return
    
    # Load raw config for transport
    import yaml
    with open(config_path, encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)
    
    # If env is specified and config is multi-env, use that env
    if env and env in raw_config:
        config_for_env = {env: raw_config[env]}
    elif env:
        logger.error(f"Environment '{env}' not found in config")
        return
    else:
        # Single config mode
        config_for_env = raw_config
    
    try:
        # Load all providers from config
        providers_dict = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=env
        )
        
        logger.info(f"✓ Loaded providers: {list(providers_dict.keys())}")
        
        # Check required providers
        if "asr" not in providers_dict:
            logger.error("ASR provider not configured!")
            return
        
        if "agent" not in providers_dict:
            logger.error("Agent provider not configured!")
            return
        
        if "tts" not in providers_dict:
            logger.error("TTS provider not configured!")
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
    agent_system_prompt = prompt or os.getenv("VIXIO_PROMPT") or (
        "You are a helpful AI voice assistant. "
        "Keep your responses concise and conversational, "
        "as they will be spoken aloud to the user. "
        "You should always respond with a short sentence first, ending with a period, not a comma."
    )
    
    logger.info("✓ Configurations loaded")
    
    # Create transport
    transport = XiaozhiTransport(
        host=host,
        port=port,
        websocket_path="/xiaozhi/v1/",
        config=config_for_env,
    )
    
    # Create async DAG factory
    async def create_dag():
        """
        Create a fresh DAG for each connection.
        
        Streaming Pipeline (4 stations):
            Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio
        """
        logger.debug("Creating new DAG with streaming providers...")
        
        # Create fresh provider instances for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=env
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
        
        # Create sentence aggregator provider (for displaying text to client)
        sentence_provider = SimpleSentenceAggregatorProviderCN(
            min_sentence_length=5,
            enable_conjunction_check=True,
            enable_punctuation_pairing=True,
            enable_incomplete_start_check=True,
        )
        await sentence_provider.initialize()
        
        # Build DAG
        dag = DAG("StreamingVoiceChat")
        
        # Add nodes (4 main stations + sentence_agg for display)
        dag.add_node("streaming_asr", StreamingASRStation(asr_provider, timeout_seconds=asr_timeout))
        dag.add_node("text_agg", TextAggregatorStation())
        dag.add_node("agent", AgentStation(agent_provider, timeout_seconds=agent_timeout))
        dag.add_node("streaming_tts", StreamingTTSStation(tts_provider, timeout_seconds=tts_timeout))
        dag.add_node("sentence_agg", SentenceAggregatorStation(provider=sentence_provider))
        
        # Define edges (data flow)
        # Main flow: Audio → StreamingASR(VAD) → TextAggregator → Agent → StreamingTTS → Audio
        dag.add_edge("transport_in", "streaming_asr")
        dag.add_edge("streaming_asr", "text_agg")
        dag.add_edge("text_agg", "agent")
        dag.add_edge("agent", "streaming_tts")
        dag.add_edge("streaming_tts", "transport_out")
        
        # STT result branch: text_agg -> transport_out (show STT text to client)
        dag.add_edge("text_agg", "transport_out")
        
        # Agent result branch: agent -> sentence_agg -> transport_out (show agent text to client)
        dag.add_edge("agent", "sentence_agg")
        dag.add_edge("sentence_agg", "transport_out")
        
        logger.debug("✓ DAG created with streaming providers")
        
        return dag
    
    # Create session manager with DAG factory
    manager = SessionManager(
        transport=transport,
        dag_factory=create_dag,
        turn_timeout_seconds=None  # No turn timeout for streaming mode
    )
    
    # Start server
    local_ip = get_local_ip()
    
    logger.info("=" * 70)
    logger.info("Vixio AI Agent Voice Chat Server (Streaming)")
    logger.info("=" * 70)
    logger.info(f"Mode: STREAMING")
    if env:
        logger.info(f"Environment: {env.upper()}")
    logger.info(f"")
    logger.info(f"Providers:")
    for category, provider in providers_dict.items():
        if provider.is_local:
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
    
    # Build DAG description
    dag_nodes = ["StreamingASR(VAD)", "TextAggregator", "Agent", "StreamingTTS"]
    logger.info(f"DAG: {' -> '.join(dag_nodes)}")
    logger.info(f"Expected TTFT: ~500-1000ms")
    logger.info("=" * 70)
    
    await manager.start()
    
    # Run until interrupted
    global _shutdown_event, _original_sigint_handler, _original_sigterm_handler
    _shutdown_event = asyncio.Event()
    
    # Register signal handlers for this session
    handler = _make_signal_handler(_shutdown_event)
    _original_sigint_handler = signal.signal(signal.SIGINT, handler)
    _original_sigterm_handler = signal.signal(signal.SIGTERM, handler)
    
    try:
        logger.info("✓ Server running. Press Ctrl+C to stop.")
        await _shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("\nShutting down...")
        
        # Restore original signal handlers
        if _original_sigint_handler:
            signal.signal(signal.SIGINT, _original_sigint_handler)
        if _original_sigterm_handler:
            signal.signal(signal.SIGTERM, _original_sigterm_handler)
        
        try:
            await asyncio.wait_for(manager.stop(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Manager stop timed out, forcing exit...")
        logger.info("✓ Server stopped")

