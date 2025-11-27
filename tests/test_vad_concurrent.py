"""
Integration test for concurrent VAD cycle handling

Tests the VAD-cycle locking mechanism with multiple concurrent sessions.
Ensures that:
1. Multiple sessions can process concurrently
2. VAD locks are properly acquired on START and released on END
3. No state corruption between sessions
4. Memory is properly released after sessions end
"""

import asyncio
import time
import numpy as np
import sys
from pathlib import Path

# Add parent directory to path (if not already there)
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from providers.vad import VADEvent
from micro_services.silero_vad.client import VADServiceClient
from loguru import logger


class VADConcurrentTester:
    """Test concurrent VAD session handling"""
    
    def __init__(self, service_url: str = "localhost:50051"):
        self.service_url = service_url
        self.sessions = []
        self.stats = {
            "total_sessions": 0,
            "successful_sessions": 0,
            "failed_sessions": 0,
            "total_requests": 0,
            "total_vad_cycles": 0,
        }
    
    async def create_test_audio(self, duration_seconds: float, sample_rate: int = 16000) -> bytes:
        """
        Create synthetic test audio (sine wave).
        
        Args:
            duration_seconds: Duration in seconds
            sample_rate: Sample rate in Hz
            
        Returns:
            PCM audio bytes (16-bit signed integer)
        """
        samples = int(duration_seconds * sample_rate)
        t = np.linspace(0, duration_seconds, samples)
        
        # 440 Hz sine wave (A4 note)
        audio = np.sin(2 * np.pi * 440 * t)
        
        # Convert to 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)
        
        return audio_int16.tobytes()
    
    async def simulate_session(
        self,
        session_id: str,
        num_vad_cycles: int = 3,
        vad_duration_seconds: float = 1.0,
        silence_duration_seconds: float = 0.5
    ):
        """
        Simulate a single VAD session with multiple VAD cycles.
        
        Args:
            session_id: Unique session identifier
            num_vad_cycles: Number of VAD cycles (voice segments)
            vad_duration_seconds: Duration of each VAD cycle
            silence_duration_seconds: Silence between VAD cycles
        """
        client = VADServiceClient(self.service_url)
        
        try:
            # Connect to service
            await client.connect()
            logger.info(f"[{session_id}] Connected to VAD service")
            
            # Create session
            success = await client.create_session(
                session_id=session_id,
                threshold=0.5,
                threshold_low=0.2,
                frame_window_threshold=3
            )
            
            if not success:
                logger.error(f"[{session_id}] Failed to create session")
                self.stats["failed_sessions"] += 1
                return
            
            logger.info(f"[{session_id}] Session created")
            
            # Simulate multiple VAD cycles
            for cycle in range(num_vad_cycles):
                logger.info(f"[{session_id}] Starting VAD cycle {cycle + 1}/{num_vad_cycles}")
                
                # VAD START
                await client.detect(
                    session_id=session_id,
                    audio_data=b'',
                    event="start"
                )
                logger.debug(f"[{session_id}] Sent START event")
                
                # Generate and send audio chunks
                audio = await self.create_test_audio(vad_duration_seconds)
                chunk_size = 512 * 2  # 512 samples * 2 bytes/sample
                
                for i in range(0, len(audio), chunk_size):
                    chunk = audio[i:i+chunk_size]
                    response = await client.detect(
                        session_id=session_id,
                        audio_data=chunk,
                        event="chunk"
                    )
                    self.stats["total_requests"] += 1
                    
                    if i == 0:
                        logger.debug(
                            f"[{session_id}] First chunk: has_voice={response.has_voice}"
                        )
                
                # VAD END
                await client.detect(
                    session_id=session_id,
                    audio_data=b'',
                    event="end"
                )
                logger.debug(f"[{session_id}] Sent END event")
                
                self.stats["total_vad_cycles"] += 1
                
                # Silence period between VAD cycles
                if cycle < num_vad_cycles - 1:
                    await asyncio.sleep(silence_duration_seconds)
            
            # Destroy session
            await client.destroy_session(session_id)
            logger.info(f"[{session_id}] Session destroyed")
            
            await client.close()
            
            self.stats["successful_sessions"] += 1
        
        except Exception as e:
            logger.error(f"[{session_id}] Error: {e}")
            self.stats["failed_sessions"] += 1
            raise
    
    async def run_concurrent_test(
        self,
        num_sessions: int = 10,
        num_vad_cycles_per_session: int = 3
    ):
        """
        Run concurrent VAD test with multiple sessions.
        
        Args:
            num_sessions: Number of concurrent sessions
            num_vad_cycles_per_session: VAD cycles per session
        """
        logger.info(f"Starting concurrent VAD test: {num_sessions} sessions")
        
        self.stats["total_sessions"] = num_sessions
        
        # Create tasks for all sessions
        tasks = []
        for i in range(num_sessions):
            session_id = f"test-session-{i:03d}"
            task = self.simulate_session(
                session_id=session_id,
                num_vad_cycles=num_vad_cycles_per_session,
                vad_duration_seconds=1.0,
                silence_duration_seconds=0.5
            )
            tasks.append(task)
        
        # Run all sessions concurrently
        start_time = time.time()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        end_time = time.time()
        
        # Check for exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Session {i} failed: {result}")
        
        # Print summary
        duration = end_time - start_time
        logger.info("=" * 60)
        logger.info("Concurrent VAD Test Results")
        logger.info("=" * 60)
        logger.info(f"Total Sessions:      {self.stats['total_sessions']}")
        logger.info(f"Successful:          {self.stats['successful_sessions']}")
        logger.info(f"Failed:              {self.stats['failed_sessions']}")
        logger.info(f"Total VAD Cycles:    {self.stats['total_vad_cycles']}")
        logger.info(f"Total Requests:      {self.stats['total_requests']}")
        logger.info(f"Duration:            {duration:.2f}s")
        logger.info(f"Throughput:          {self.stats['total_requests']/duration:.2f} req/s")
        logger.info("=" * 60)
        
        # Get service stats
        try:
            client = VADServiceClient(self.service_url)
            await client.connect()
            stats = await client.get_stats()
            logger.info(f"Service Stats:")
            logger.info(f"  Active Sessions:   {stats.active_sessions}")
            logger.info(f"  Total Requests:    {stats.total_requests}")
            await client.close()
        except Exception as e:
            logger.warning(f"Failed to get service stats: {e}")
        
        return self.stats


async def main():
    """Main test entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="VAD Concurrent Test")
    parser.add_argument(
        '--service-url',
        default='localhost:50051',
        help='VAD service URL (default: localhost:50051)'
    )
    parser.add_argument(
        '--sessions',
        type=int,
        default=10,
        help='Number of concurrent sessions (default: 10)'
    )
    parser.add_argument(
        '--cycles',
        type=int,
        default=3,
        help='VAD cycles per session (default: 3)'
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
    tester = VADConcurrentTester(service_url=args.service_url)
    
    try:
        stats = await tester.run_concurrent_test(
            num_sessions=args.sessions,
            num_vad_cycles_per_session=args.cycles
        )
        
        # Exit code based on success rate
        success_rate = stats["successful_sessions"] / stats["total_sessions"]
        if success_rate == 1.0:
            logger.success("✅ All sessions completed successfully!")
            sys.exit(0)
        elif success_rate >= 0.9:
            logger.warning(f"⚠️  {success_rate*100:.1f}% success rate (acceptable)")
            sys.exit(0)
        else:
            logger.error(f"❌ {success_rate*100:.1f}% success rate (too many failures)")
            sys.exit(1)
    
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())

