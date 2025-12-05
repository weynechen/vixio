"""
Simple Kokoro TTS service test

Quick test to verify Kokoro TTS gRPC service is working correctly.
"""

import asyncio
import sys
import wave
from pathlib import Path

# Add parent directory to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from inference.kokoro_cn_tts_local.client import TTSServiceClient
from loguru import logger


async def test_kokoro_service(service_url: str = "localhost:50053"):
    """Test basic Kokoro TTS service functionality"""
    
    logger.info(f"Testing Kokoro TTS service at {service_url}")
    
    # Create client
    client = TTSServiceClient(service_url)
    
    try:
        # Connect
        logger.info("Connecting to Kokoro TTS service...")
        await client.connect()
        logger.success("✅ Connected successfully")
        
        # Get available voices
        logger.info("Getting available voices...")
        voices = await client.get_voices()
        logger.success(f"✅ Found {len(voices)} voices:")
        for voice in voices:
            logger.info(f"  - {voice['id']}: {voice['name']} ({voice['lang']})")
        
        # Create session
        logger.info("Creating session...")
        session_id = "test-session-001"
        voice = "zf_001"
        success = await client.create_session(
            session_id=session_id,
            voice=voice,
            speed=1.0,
            lang="zh",
            sample_rate=24000
        )
        
        if not success:
            logger.error("❌ Failed to create session")
            return False
        
        logger.success(f"✅ Session created: {session_id}")
        
        # Synthesize test text
        test_text = "你好，这是Kokoro语音合成测试。"
        logger.info(f"Synthesizing text: {test_text}")
        
        audio_chunks = []
        sample_rate = 24000
        chunk_count = 0
        
        async for sr, audio_data, is_final in client.synthesize(
            session_id=session_id,
            text=test_text,
            join_sentences=True
        ):
            if is_final:
                logger.info("Received final marker")
                break
            
            chunk_count += 1
            sample_rate = sr
            audio_chunks.append(audio_data)
            logger.debug(f"Received audio chunk {chunk_count}: {len(audio_data)} samples")
        
        logger.success(f"✅ Synthesis completed: {chunk_count} chunks")
        
        # Save audio to file
        if audio_chunks:
            import numpy as np
            
            combined_audio = np.concatenate(audio_chunks)
            logger.info(f"Total audio samples: {len(combined_audio)}")
            logger.info(f"Duration: {len(combined_audio) / sample_rate:.2f}s")
            
            # Convert float32 to int16 for WAV file
            audio_int16 = (combined_audio * 32767).astype(np.int16)
            
            output_file = Path(__file__).parent / "output_kokoro_test.wav"
            with wave.open(str(output_file), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_int16.tobytes())
            
            logger.success(f"✅ Audio saved to: {output_file}")
        
        # Get service stats
        logger.info("Getting service stats...")
        stats = await client.get_stats()
        logger.info(f"Service stats:")
        logger.info(f"  Active sessions: {stats['active_sessions']}")
        logger.info(f"  Total requests: {stats['total_requests']}")
        
        # Destroy session
        logger.info("Destroying session...")
        await client.destroy_session(session_id)
        logger.success("✅ Session destroyed")
        
        # Close connection
        await client.close()
        logger.success("✅ Connection closed")
        
        logger.success("\n" + "="*60)
        logger.success("✅ All tests passed!")
        logger.success("="*60)
        
        return True
    
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Simple Kokoro TTS Service Test")
    parser.add_argument(
        '--service-url',
        default='localhost:50053',
        help='Kokoro TTS service URL (default: localhost:50053)'
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        help='Log level (default: INFO)'
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=args.log_level
    )
    
    # Run test
    success = await test_kokoro_service(args.service_url)
    
    if success:
        logger.success("\n✅ Kokoro TTS service is working correctly!")
        sys.exit(0)
    else:
        logger.error("\n❌ Kokoro TTS service test failed")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())

