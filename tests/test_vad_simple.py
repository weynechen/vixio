"""
Simple VAD service test

Quick test to verify VAD gRPC service is working correctly.
"""

import asyncio
import sys
import numpy as np
from pathlib import Path

# Add parent directory to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from micro_services.silero_vad.client import VADServiceClient
from loguru import logger


async def test_vad_service(service_url: str = "localhost:50051"):
    """Test basic VAD service functionality"""
    
    logger.info(f"Testing VAD service at {service_url}")
    
    # Create client
    client = VADServiceClient(service_url)
    
    try:
        # Connect
        logger.info("Connecting to VAD service...")
        await client.connect()
        logger.success("✅ Connected successfully")
        
        # Create session
        logger.info("Creating session...")
        session_id = "test-session-001"
        success = await client.create_session(
            session_id=session_id,
            threshold=0.5,
            threshold_low=0.2,
            frame_window_threshold=3
        )
        
        if not success:
            logger.error("❌ Failed to create session")
            return False
        
        logger.success(f"✅ Session created: {session_id}")
        
        # Generate test audio (sine wave, 1 second)
        logger.info("Generating test audio...")
        sample_rate = 16000
        duration = 1.0
        samples = int(duration * sample_rate)
        t = np.linspace(0, duration, samples)
        audio = np.sin(2 * np.pi * 440 * t)  # 440 Hz
        audio_int16 = (audio * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()
        
        # Send START event
        logger.info("Sending START event...")
        await client.detect(session_id=session_id, audio_data=b'', event="start")
        logger.success("✅ START event sent")
        
        # Send audio chunks
        logger.info("Sending audio chunks...")
        chunk_size = 512 * 2  # 512 samples * 2 bytes
        chunks_sent = 0
        has_voice_detected = False
        
        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i:i+chunk_size]
            response = await client.detect(
                session_id=session_id,
                audio_data=chunk,
                event="chunk"
            )
            chunks_sent += 1
            
            if response.has_voice:
                has_voice_detected = True
                if chunks_sent == 1:
                    logger.info(f"Voice detected in chunk {chunks_sent}")
        
        logger.success(f"✅ Sent {chunks_sent} audio chunks")
        
        if has_voice_detected:
            logger.success("✅ Voice activity detected")
        else:
            logger.warning("⚠️  No voice detected (this is OK for simple test)")
        
        # Send END event
        logger.info("Sending END event...")
        await client.detect(session_id=session_id, audio_data=b'', event="end")
        logger.success("✅ END event sent")
        
        # Get service stats
        logger.info("Getting service stats...")
        stats = await client.get_stats()
        logger.info(f"Service stats:")
        logger.info(f"  Active sessions: {stats.active_sessions}")
        logger.info(f"  Total requests: {stats.total_requests}")
        
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
    
    parser = argparse.ArgumentParser(description="Simple VAD Service Test")
    parser.add_argument(
        '--service-url',
        default='localhost:50051',
        help='VAD service URL (default: localhost:50051)'
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
    success = await test_vad_service(args.service_url)
    
    if success:
        logger.success("\n✅ VAD service is working correctly!")
        sys.exit(0)
    else:
        logger.error("\n❌ VAD service test failed")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())

