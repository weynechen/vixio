"""
Example: Xiaozhi protocol server with authentication and OTA support

Complete example for Xiaozhi device integration.
"""

import asyncio
from typing import AsyncIterator
from loguru import logger

from core.pipeline import Pipeline
from core.session import Session
from core.chunk import Chunk, ChunkType, AudioChunk, TextChunk

from stations.vad import VADStation
from stations.asr import ASRStation
from stations.agent import AgentStation
from stations.tts import TTSStation

from providers.silero_vad.provider import SileroVADProvider
from providers.sherpa_onnx_local.provider import SherpaOnnxASRProvider
from providers.openai_agent.provider import OpenAIAgentProvider
from providers.edge_tts.provider import EdgeTTSProvider

from transports.xiaozhi.transport import XiaozhiTransport


async def session_handler(transport: XiaozhiTransport, session_id: str):
    """
    Handle a client session.
    
    Args:
        transport: Xiaozhi transport instance
        session_id: Session identifier
    """
    session_logger = logger.bind(session=session_id[:8])
    session_logger.info(f"Starting session handler for {session_id}")
    
    try:
        # Create providers
        vad_provider = SileroVADProvider(
            model_path="models/snakers4_silero-vad",
            threshold=0.5,
            min_speech_duration_ms=250,
            min_silence_duration_ms=500,
        )
        
        asr_provider = SherpaOnnxASRProvider(
            model_path="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
            language="auto",
        )
        
        agent_provider = OpenAIAgentProvider(
            api_key="your_openai_api_key",  # Replace with your API key
            model="gpt-4",
            temperature=0.7,
        )
        
        tts_provider = EdgeTTSProvider(
            voice="zh-CN-XiaoxiaoNeural",
            rate="+0%",
            volume="+0%",
        )
        
        # Create stations
        vad_station = VADStation(vad_provider)
        asr_station = ASRStation(asr_provider)
        agent_station = AgentStation(agent_provider)
        tts_station = TTSStation(tts_provider)
        
        # Create pipeline
        pipeline = Pipeline([
            vad_station,
            asr_station,
            agent_station,
            tts_station,
        ])
        
        # Create session
        session = Session(session_id)
        
        # Start pipeline
        await pipeline.start()
        
        # Process input stream
        async def input_generator() -> AsyncIterator[Chunk]:
            """Generate input chunks from transport."""
            async for chunk in transport.input_stream(session_id):
                yield chunk
        
        # Run pipeline
        async for output_chunk in pipeline.process(session, input_generator()):
            # Send output to client
            await transport.output_chunk(session_id, output_chunk)
        
        session_logger.info(f"Session {session_id} completed normally")
        
    except Exception as e:
        session_logger.error(f"Error in session handler: {e}", exc_info=True)
    
    finally:
        # Cleanup
        await pipeline.stop()
        session_logger.info(f"Session {session_id} cleaned up")


async def main():
    """Main server entry point."""
    
    # Server configuration
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "websocket_path": "/xiaozhi/v1/",
            
            # Authentication settings
            "auth": {
                "enabled": True,  # Enable authentication
                "allowed_devices": [],  # Empty = allow all devices (with valid token)
                "expire_seconds": 60 * 60 * 24 * 30,  # 30 days
            },
            "auth_key": "your_secret_key_here",  # Replace with your secret key
            
            # WebSocket URL (optional, will auto-detect if not set)
            # "websocket": "ws://192.168.1.100:8080/xiaozhi/v1/",
            
            # MQTT gateway (optional, if you want to use MQTT instead of WebSocket)
            # "mqtt_gateway": "mqtt://localhost:1883",
            # "mqtt_signature_key": "your_mqtt_signature_key",
            
            # Timezone offset
            "timezone_offset": 8,  # UTC+8
        }
    }
    
    # Create transport
    transport = XiaozhiTransport(
        host=config["server"]["host"],
        port=config["server"]["port"],
        websocket_path=config["server"]["websocket_path"],
        config=config,
    )
    
    # Set connection handler
    async def connection_handler(session_id: str):
        """Handle new WebSocket connection."""
        await session_handler(transport, session_id)
    
    transport.set_connection_handler(connection_handler)
    
    # Start server
    await transport.start()
    
    logger.info("Xiaozhi server is running...")
    logger.info(f"WebSocket URL: ws://{config['server']['host']}:{config['server']['port']}{config['server']['websocket_path']}")
    logger.info("OTA endpoint: http://{}:{}/xiaozhi/ota/".format(
        config['server']['host'],
        config['server']['port']
    ))
    
    # Keep server running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await transport.stop()


if __name__ == "__main__":
    asyncio.run(main())

