"""
Realtime mode server runner.

Extracted from examples/realtime_chat.py for reusability.
"""

import asyncio
import os
import signal
from typing import Optional
from loguru import logger

from vixio.core.dag import DAG
from vixio.core.session import SessionManager
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.stations import RealtimeStation
from vixio.providers.factory import ProviderFactory
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


async def run_realtime_server(
    config_path: str,
    env: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8000,
    turn_timeout: float = 30.0,
    prompt: Optional[str] = None,
):
    """
    Run realtime mode server (Qwen Omni Realtime).
    
    Args:
        config_path: Path to provider config file
        env: Environment name in config (e.g., "dev-realtime")
        host: Server host address
        port: Server port
        turn_timeout: Turn inactivity timeout in seconds (0 to disable)
        prompt: Custom system prompt/instructions
    """
    logger.info("=== Real-time Voice Conversation (Realtime Mode) ===")
    if env:
        logger.info(f"Environment: {env}")
    
    # Load provider configurations
    logger.info("Loading provider configurations...")
    
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return
    
    # Load raw config for transport
    import yaml
    with open(config_path) as f:
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
    
    # Set prompt if provided (before loading providers so config can use it)
    if prompt:
        os.environ["VIXIO_PROMPT"] = prompt
        logger.info(f"Custom prompt set: {prompt[:50]}..." if len(prompt) > 50 else f"Custom prompt set: {prompt}")
    else:
        current_prompt = os.getenv("VIXIO_PROMPT")
        if current_prompt:
            logger.info(f"Using prompt from environment: {current_prompt[:50]}..." if len(current_prompt) > 50 else f"Using prompt from environment: {current_prompt}")
    
    try:
        # Load all providers from config
        providers_dict = ProviderFactory.create_from_config_file(
            config_path=config_path,
            env=env
        )
        
        logger.info(f"✓ Loaded providers: {list(providers_dict.keys())}")
        
        # Check required providers
        if "realtime" not in providers_dict:
            logger.error("Realtime provider not configured!")
            logger.error("Make sure your config has a 'realtime' provider")
            return
        
    except Exception as e:
        logger.error(f"Failed to load provider configurations: {e}")
        import traceback
        traceback.print_exc()
        return
    
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
        
        realtime_provider = session_providers["realtime"]
        await realtime_provider.initialize()
        
        # Build simple DAG
        dag = DAG("RealtimeVoiceChat")
        
        # Add realtime station
        dag.add_node("realtime", RealtimeStation(realtime_provider))
        
        # Simple edge: transport_in -> realtime -> transport_out
        dag.add_edge("transport_in", "realtime", "transport_out")
        
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
    logger.info("Vixio Realtime Voice Chat Server")
    logger.info("=" * 70)
    logger.info(f"Mode: REALTIME (End-to-End)")
    if env:
        logger.info(f"Environment: {env.upper()}")
    logger.info(f"")
    logger.info(f"Providers:")
    for category, provider in providers_dict.items():
        if provider.is_local:
            provider_type = "Local"
        else:
            provider_type = "Remote (API)"
        logger.info(f"  - {category.upper():10s}: {provider.name:20s} [{provider_type}]")
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
    logger.info(f"DAG: RealtimeStation (VAD + ASR + LLM + TTS integrated)")
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
