"""
Example 3: Complete voice conversation with AI Agent

A full voice chat pipeline with VAD, ASR, Agent, sentence splitting, and TTS.
Demonstrates the complete integration of all components including LLM.
"""

import asyncio
import logging
import os
from core.pipeline import Pipeline
from core.session import SessionManager
from transports.xiaozhi import XiaozhiTransport
from stations import (
    VADStation,
    TurnDetectorStation,
    ASRStation,
    AgentStation,
    SentenceSplitterStation,
    TTSStation
)
from providers import (
    SileroVADProvider,
    SherpaOnnxLocalProvider,
    EdgeTTSProvider,
    OpenAIAgentProvider
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    
    # Step 1: Initialize providers
    logger.info("Initializing providers...")
    
    # VAD Provider
    vad_provider = SileroVADProvider(
        threshold=0.5,
        min_speech_duration_ms=250,
    )
    logger.info("✓ VAD Provider initialized")
    
    # ASR Provider
    model_path = os.path.join(
        os.path.dirname(__file__),
        "../models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    )
    if not os.path.exists(model_path):
        logger.warning(f"ASR model not found at {model_path}")
        logger.warning("Using mock ASR for demo")
        asr_provider = None
    else:
        asr_provider = SherpaOnnxLocalProvider(
            model_path=model_path,
            sample_rate=16000,
        )
        logger.info("✓ ASR Provider initialized")
    
    # Agent Provider (OpenAI)
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")  # Optional, for custom endpoints
    
    if not api_key:
        logger.error("OPENAI_API_KEY environment variable not set!")
        logger.info("Please set: export OPENAI_API_KEY='your-api-key'")
        return
    
    agent_provider = OpenAIAgentProvider(
        api_key=api_key,
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        base_url=base_url,
        temperature=0.7,
        max_tokens=2000,
    )
    
    # Initialize agent with system prompt
    await agent_provider.initialize(
        system_prompt=(
            "You are a helpful AI voice assistant. "
            "Keep your responses concise and conversational, "
            "as they will be spoken aloud to the user."
        )
    )
    logger.info("✓ Agent Provider initialized")
    
    # TTS Provider
    tts_provider = EdgeTTSProvider(
        voice="zh-CN-XiaoxiaoNeural",
        rate="+0%",
        volume="+0%",
    )
    logger.info("✓ TTS Provider initialized")
    
    # Step 2: Create transport
    # WebSocket endpoint: ws://0.0.0.0:8080/xiaozhi/v1/
    # HTTP endpoints for monitoring
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8080,
        websocket_path="/xiaozhi/v1/"
    )
    
    # Step 3: Create pipeline factory
    def create_pipeline():
        """
        Factory function to create a fresh pipeline for each connection.
        
        This ensures each client has isolated state.
        """
        stations = [
            # Stage 1: Voice detection
            VADStation(vad_provider),
            TurnDetectorStation(silence_threshold_ms=800),
        ]
        
        # Stage 2: Speech recognition (if available)
        if asr_provider:
            stations.append(ASRStation(asr_provider))
        
        # Stage 3: AI Agent processing
        stations.append(AgentStation(agent_provider))
        
        # Stage 4: Sentence splitting for streaming TTS
        stations.append(SentenceSplitterStation(min_sentence_length=5))
        
        # Stage 5: Speech synthesis
        stations.append(TTSStation(tts_provider))
        
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
    logger.info("=" * 70)
    logger.info("Vixio AI Agent Voice Chat Server")
    logger.info("=" * 70)
    logger.info(f"WebSocket endpoint: ws://0.0.0.0:8080/xiaozhi/v1/")
    logger.info(f"HTTP endpoints:")
    logger.info(f"  - Health check: http://0.0.0.0:8080/health")
    logger.info(f"  - Server info:  http://0.0.0.0:8080/")
    logger.info(f"  - Connections:  http://0.0.0.0:8080/connections")
    logger.info("")
    logger.info("Pipeline: VAD -> TurnDetector -> ASR -> Agent -> SentenceSplitter -> TTS")
    logger.info("=" * 70)
    
    await manager.start()
    
    # Step 6: Run until interrupted
    try:
        await asyncio.Event().wait()  # Wait forever
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        await manager.stop()
        await agent_provider.shutdown()
        logger.info("Server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nExiting...")

