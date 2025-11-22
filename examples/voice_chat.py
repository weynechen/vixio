"""
Example 2: Voice conversation

A complete voice chat pipeline with VAD, ASR, and TTS.
Demonstrates full integration of all components.
"""

import asyncio
import logging
import os
from vixio.core.pipeline import Pipeline
from vixio.core.session import SessionManager
from vixio.transports.xiaozhi import XiaozhiTransport
from vixio.stations import VADStation, TurnDetectorStation, ASRStation, TTSStation
from vixio.providers import SileroVADProvider, SherpaOnnxLocalProvider, EdgeTTSProvider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """
    Complete voice conversation server.
    
    Pipeline flow:
    1. Client sends audio via WebSocket
    2. VAD detects voice activity
    3. TurnDetector waits for silence
    4. ASR transcribes to text
    5. TTS synthesizes response (echo in this example)
    6. Audio sent back to client via WebSocket
    """
    logger.info("=== Voice Conversation Server ===")
    
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
        logger.warning("Using mock ASR provider for demo")
        # In production, use: asr_provider = SherpaOnnxLocalProvider(model_path=model_path)
        asr_provider = None
    else:
        asr_provider = SherpaOnnxLocalProvider(
            model_path=model_path,
            sample_rate=16000,
        )
        logger.info("✓ ASR Provider initialized")
    
    # TTS Provider
    tts_provider = EdgeTTSProvider(
        voice="zh-CN-XiaoxiaoNeural",
        rate="+0%",
        volume="+0%",
    )
    logger.info("✓ TTS Provider initialized")
    
    # Step 2: Create transport
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8080
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
        
        # Add ASR if available
        if asr_provider:
            stations.append(ASRStation(asr_provider))
        
        # Stage 2: Speech synthesis (echo back the transcribed text)
        stations.append(TTSStation(tts_provider))
        
        return Pipeline(
            stations=stations,
            name="VoiceChat"
        )
    
    # Step 4: Create session manager
    manager = SessionManager(
        transport=transport,
        pipeline_factory=create_pipeline
    )
    
    # Step 5: Start server
    logger.info("=" * 60)
    logger.info("Starting Voice Chat server on ws://0.0.0.0:8080")
    logger.info("=" * 60)
    logger.info("Pipeline: VAD -> TurnDetector -> ASR -> TTS")
    logger.info("Ready to accept connections...")
    logger.info("=" * 60)
    
    await manager.start()
    
    # Step 6: Run until interrupted
    try:
        await asyncio.Event().wait()  # Wait forever
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        await manager.stop()
        logger.info("Server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nExiting...")
