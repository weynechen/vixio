"""
Example 3: Complete voice conversation with AI Agent

A full voice chat pipeline with VAD, ASR, Agent, sentence splitting, and TTS.
Demonstrates the complete integration of all components including LLM.

Logger Configuration:
    Logger is auto-configured on import with INFO level, logging to logs/ directory.
    To customize, call configure_logger() before other imports:
    
    from utils import configure_logger
    configure_logger(level="DEBUG", log_dir="my_logs")
"""

import asyncio
import os
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
from providers import (
    # Use regular providers to ensure state isolation in concurrent scenarios
    SileroVADProvider,
    SherpaOnnxLocalProvider,
    EdgeTTSProvider,
    OpenAIAgentProvider
)
from utils import get_local_ip

import dotenv

dotenv.load_dotenv()


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
    logger.info("=== Voice Chat with AI Agent ===")
    
    # Step 1: Prepare provider configurations
    logger.info("Preparing provider configurations...")
    
    # VAD configuration
    vad_config = {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
    }
    
    # ASR configuration
    model_path = os.path.join(
        os.path.dirname(__file__),
        "../models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    )
    asr_config = None
    if not os.path.exists(model_path):
        logger.warning(f"ASR model not found at {model_path}")
        logger.warning("ASR will be disabled for this session")
    else:
        asr_config = {
            "model_path": model_path,
            "sample_rate": 16000,
        }
    
    # Agent configuration (OpenAI)
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL")  # Optional, for custom endpoints
    
    if not api_key:
        logger.error("API_KEY environment variable not set!")
        logger.info("Please set: export API_KEY='your-api-key'")
        return
    
    agent_config = {
        "api_key": api_key,
        "model": os.getenv("LITELLM_MODEL", "deepseek/deepseek-chat"),
        "base_url": base_url,
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    
    agent_system_prompt = (
        "You are a helpful AI voice assistant. "
        "Keep your responses concise and conversational, "
        "as they will be spoken aloud to the user."
    )
    
    # TTS configuration
    tts_config = {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
    }
    
    logger.info("✓ Configurations prepared")
    
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
        
        This ensures each client has completely isolated provider instances,
        preventing state pollution between concurrent sessions.
        
        Returns:
            Pipeline: New pipeline with independent provider instances
        """
        logger.debug("Creating new pipeline with isolated providers...")
        
        # Create independent provider instances for this session
        # Each session gets its own VAD provider with completely isolated state and model
        # This ensures thread-safety and prevents state pollution in concurrent scenarios
        vad_provider = SileroVADProvider(**vad_config)
        
        # Each session gets its own ASR provider with isolated state and model
        # This ensures accurate recognition without cross-session interference
        asr_provider = None
        if asr_config:
            asr_provider = SherpaOnnxLocalProvider(**asr_config)
        
        # Each session gets its own Agent provider with isolated conversation history
        agent_provider = OpenAIAgentProvider(**agent_config)
        await agent_provider.initialize(system_prompt=agent_system_prompt)
        
        # Each session gets its own TTS provider (stateless but good practice)
        tts_provider = EdgeTTSProvider(**tts_config)
        
        # Build station list
        stations = [
            # Stage 1: Voice detection
            VADStation(vad_provider),
            TurnDetectorStation(silence_threshold_ms=800),
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
    logger.info("Pipeline: VAD -> TurnDetector -> ASR -> TextAggregator -> Agent -> SentenceSplitter -> TTS")
    logger.info("=" * 70)
    
    await manager.start()
    
    # Step 6: Run until interrupted
    try:
        await asyncio.Event().wait()  # Wait forever
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        await manager.stop()
        logger.info("Server stopped")
        logger.info("Note: Each session's providers are cleaned up automatically")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nExiting...")

