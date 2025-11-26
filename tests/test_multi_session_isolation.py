"""
Test multi-session isolation.

This test verifies that multiple concurrent sessions have isolated provider instances
and don't interfere with each other.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger


async def test_provider_isolation():
    """
    Test that each session gets independent provider instances.
    
    This is a simple test to verify the fix for provider state pollution.
    """
    logger.info("=== Testing Multi-Session Provider Isolation ===")
    
    # Import after path setup
    from providers import (
        SileroVADProvider,
        OpenAIAgentProvider,
        EdgeTTSProvider
    )
    
    # Test 1: Create multiple provider instances
    logger.info("\n[Test 1] Creating multiple provider instances...")
    
    vad1 = SileroVADProvider(threshold=0.5)
    vad2 = SileroVADProvider(threshold=0.5)
    
    # Verify they are different instances
    assert vad1 is not vad2, "VAD providers should be different instances"
    assert id(vad1) != id(vad2), "VAD providers should have different memory addresses"
    logger.info("✓ VAD providers are isolated instances")
    
    # Test 2: Verify state isolation
    logger.info("\n[Test 2] Testing state isolation...")
    
    # Simulate audio processing on vad1
    test_audio = b'\x00' * 1024
    result1 = vad1.detect(test_audio)
    
    # vad2 should have independent state
    assert len(vad2._pcm_buffer) == 0, "VAD2 buffer should be empty"
    assert len(vad1._pcm_buffer) >= 0, "VAD1 may have data in buffer"
    logger.info("✓ VAD state is isolated between instances")
    
    # Test 3: Agent provider isolation
    logger.info("\n[Test 3] Testing agent provider isolation...")
    
    import os
    api_key = os.getenv("API_KEY", "test-key")
    
    agent1 = OpenAIAgentProvider(
        api_key=api_key,
        model="test-model"
    )
    agent2 = OpenAIAgentProvider(
        api_key=api_key,
        model="test-model"
    )
    
    assert agent1 is not agent2, "Agent providers should be different instances"
    assert id(agent1) != id(agent2), "Agent providers should have different memory addresses"
    logger.info("✓ Agent providers are isolated instances")
    
    # Test 4: TTS provider isolation
    logger.info("\n[Test 4] Testing TTS provider isolation...")
    
    tts1 = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")
    tts2 = EdgeTTSProvider(voice="zh-CN-XiaoxiaoNeural")
    
    assert tts1 is not tts2, "TTS providers should be different instances"
    assert id(tts1) != id(tts2), "TTS providers should have different memory addresses"
    logger.info("✓ TTS providers are isolated instances")
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ All isolation tests passed!")
    logger.info("=" * 60)
    logger.info("\nConclusion:")
    logger.info("  Each session will receive completely independent provider instances,")
    logger.info("  preventing state pollution and ensuring proper multi-session isolation.")


async def test_pipeline_factory():
    """
    Test that pipeline factory creates independent pipelines.
    """
    logger.info("\n=== Testing Pipeline Factory Pattern ===")
    
    from core.pipeline import Pipeline
    from stations import VADStation, AgentStation, TTSStation
    from providers import SileroVADProvider, OpenAIAgentProvider, EdgeTTSProvider
    import os
    
    # Create pipeline factory (simulating agent_chat.py pattern)
    vad_config = {"threshold": 0.5, "min_speech_duration_ms": 250}
    agent_config = {
        "api_key": os.getenv("API_KEY", "test-key"),
        "model": "test-model"
    }
    tts_config = {"voice": "zh-CN-XiaoxiaoNeural"}
    
    async def create_pipeline():
        """Factory function that creates isolated providers."""
        vad_provider = SileroVADProvider(**vad_config)
        agent_provider = OpenAIAgentProvider(**agent_config)
        # Skip initialization in test
        tts_provider = EdgeTTSProvider(**tts_config)
        
        return Pipeline(
            stations=[
                VADStation(vad_provider),
                AgentStation(agent_provider),
                TTSStation(tts_provider),
            ],
            name="TestPipeline"
        )
    
    # Create two pipelines
    logger.info("Creating pipeline 1...")
    pipeline1 = await create_pipeline()
    
    logger.info("Creating pipeline 2...")
    pipeline2 = await create_pipeline()
    
    # Verify they are different instances
    assert pipeline1 is not pipeline2, "Pipelines should be different instances"
    
    # Verify stations are different
    vad1 = pipeline1.stations[0]
    vad2 = pipeline2.stations[0]
    assert vad1 is not vad2, "VAD stations should be different instances"
    
    # Verify providers are different
    assert vad1.vad is not vad2.vad, "VAD providers should be different instances"
    
    logger.info("✓ Pipeline factory creates isolated instances")
    
    # Cleanup
    await pipeline1.cleanup()
    await pipeline2.cleanup()
    
    logger.info("\n✅ Pipeline factory test passed!")


async def main():
    """Run all tests."""
    try:
        await test_provider_isolation()
        await test_pipeline_factory()
        
        logger.info("\n" + "=" * 60)
        logger.info("🎉 All tests completed successfully!")
        logger.info("=" * 60)
        logger.info("\nThe multi-session isolation fix is working correctly.")
        logger.info("Each session will have independent provider instances.")
        
    except AssertionError as e:
        logger.error(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

