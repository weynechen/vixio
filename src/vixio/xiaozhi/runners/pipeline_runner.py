"""
Pipeline mode server runner.

Extracted from examples/agent_chat.py for reusability.
"""

import asyncio
import os
import signal
from typing import Optional
from loguru import logger

from vixio.core.dag import DAG
from vixio.core.session import SessionManager
from vixio.core.tools import get_builtin_local_tools
from vixio.providers.agent import Tool
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


async def run_pipeline_server(
    config_path: str,
    env: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8000,
    turn_timeout: float = 30.0,
    prompt: Optional[str] = None,
):
    """
    Run pipeline mode server (VAD + ASR + Agent + TTS).
    
    Args:
        config_path: Path to provider config file
        env: Environment name in config (e.g., "dev-qwen")
        host: Server host address
        port: Server port
        turn_timeout: Turn inactivity timeout in seconds (0 to disable)
        prompt: Custom system prompt for agent
    """
    logger.info("=== Voice Chat with AI Agent (Pipeline Mode) ===")
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
        """Create a fresh DAG for each connection"""
        logger.debug("Creating new DAG with isolated providers...")
        
        # Create fresh provider instances for this session
        session_providers = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=env
        )
        
        vad_provider = session_providers["vad"]
        await vad_provider.initialize()
        
        asr_provider = session_providers.get("asr")
        if asr_provider:
            await asr_provider.initialize()
        
        agent_provider = session_providers["agent"]
        
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
        
        tts_provider = session_providers["tts"]
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
        
        # Create sentence aggregator provider
        sentence_provider = SimpleSentenceAggregatorProviderCN(
            min_sentence_length=5,
            enable_conjunction_check=True,
            enable_punctuation_pairing=True,
            enable_incomplete_start_check=True,
        )
        await sentence_provider.initialize()
        
        dag.add_node("sentence_agg", SentenceAggregatorStation(provider=sentence_provider))
        dag.add_node("tts", TTSStation(tts_provider))
        
        # Define edges
        if asr_provider:
            dag.add_edge("transport_in", "vad", "turn_detector", "asr", "text_agg", "agent", "sentence_agg", "tts", "transport_out")
            dag.add_edge("text_agg", "transport_out")
        else:
            raise ValueError("ASR provider not configured")
        
        logger.debug("✓ DAG created with isolated providers")
        
        return dag
    
    # Create session manager with DAG factory
    timeout = turn_timeout if turn_timeout > 0 else None
    if timeout:
        logger.info(f"Turn timeout enabled: {timeout}s")
    else:
        logger.info("Turn timeout disabled")
    
    manager = SessionManager(
        transport=transport,
        dag_factory=create_dag,
        turn_timeout_seconds=timeout
    )
    
    # Start server
    local_ip = get_local_ip()
    
    logger.info("=" * 70)
    logger.info("Vixio AI Agent Voice Chat Server")
    logger.info("=" * 70)
    logger.info(f"Mode: PIPELINE")
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
    dag_nodes = ["VAD", "TurnDetector"]
    if has_asr:
        dag_nodes.extend(["ASR", "TextAggregator"])
    dag_nodes.extend(["Agent", "SentenceAggregator", "TTS"])
    logger.info(f"DAG: {' -> '.join(dag_nodes)}")
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
