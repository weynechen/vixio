"""
Test memory leak fix.

This test verifies that provider resources are properly released when sessions end.
"""

import asyncio
import gc
import psutil
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger


def get_memory_usage_mb():
    """Get current process memory usage in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024


async def test_memory_cleanup():
    """Test that provider resources are cleaned up properly."""
    logger.info("=== Testing Memory Cleanup ===")
    
    from providers import SileroVADProvider, SherpaOnnxLocalProvider, OpenAIAgentProvider
    from core.pipeline import Pipeline
    from stations import VADStation, ASRStation, AgentStation
    
    # Get initial memory
    gc.collect()  # Clean up before starting
    initial_memory = get_memory_usage_mb()
    logger.info(f"Initial memory: {initial_memory:.1f} MB")
    
    # Test configuration
    model_path = Path(__file__).parent.parent / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    
    # Create and cleanup multiple pipelines to simulate multiple sessions
    logger.info("\n[Test 1] Creating and cleaning up 3 pipelines...")
    
    memories = []
    for i in range(3):
        logger.info(f"\n--- Creating pipeline {i+1} ---")
        
        # Create providers (simulate session start)
        vad_provider = SileroVADProvider(threshold=0.5)
        
        if model_path.exists():
            asr_provider = SherpaOnnxLocalProvider(
                model_path=str(model_path),
                sample_rate=16000
            )
        else:
            logger.warning("ASR model not found, skipping ASR in test")
            asr_provider = None
        
        agent_provider = OpenAIAgentProvider(
            api_key="test-key",
            model="test-model"
        )
        
        # Create pipeline
        stations = [VADStation(vad_provider)]
        if asr_provider:
            stations.append(ASRStation(asr_provider))
        stations.append(AgentStation(agent_provider))
        
        pipeline = Pipeline(
            stations=stations,
            name=f"TestPipeline{i+1}"
        )
        
        current_memory = get_memory_usage_mb()
        logger.info(f"Memory after creation: {current_memory:.1f} MB (+{current_memory - initial_memory:.1f} MB)")
        memories.append(current_memory)
        
        # Cleanup (simulate session end)
        logger.info(f"Cleaning up pipeline {i+1}...")
        await pipeline.cleanup()
        
        # Force garbage collection
        gc.collect()
        
        after_cleanup = get_memory_usage_mb()
        logger.info(f"Memory after cleanup: {after_cleanup:.1f} MB")
    
    # Final memory check
    gc.collect()
    final_memory = get_memory_usage_mb()
    
    logger.info("\n" + "=" * 60)
    logger.info("Memory Usage Summary:")
    logger.info("=" * 60)
    logger.info(f"Initial:       {initial_memory:.1f} MB")
    for i, mem in enumerate(memories):
        logger.info(f"After pipe {i+1}: {mem:.1f} MB (+{mem - initial_memory:.1f} MB)")
    logger.info(f"Final:         {final_memory:.1f} MB")
    logger.info(f"Net increase:  {final_memory - initial_memory:.1f} MB")
    logger.info("=" * 60)
    
    # Check if memory was properly released
    memory_increase = final_memory - initial_memory
    
    if model_path.exists():
        # With ASR model, expect < 100MB residual (some Python overhead is normal)
        threshold = 100
    else:
        # Without ASR, expect < 50MB residual
        threshold = 50
    
    if memory_increase < threshold:
        logger.info(f"\n✅ Memory leak test PASSED!")
        logger.info(f"   Net increase ({memory_increase:.1f} MB) is within acceptable range (< {threshold} MB)")
        return True
    else:
        logger.error(f"\n❌ Memory leak detected!")
        logger.error(f"   Net increase ({memory_increase:.1f} MB) exceeds threshold ({threshold} MB)")
        logger.error(f"   This indicates provider resources are not being fully released")
        return False


async def test_provider_cleanup_directly():
    """Test that individual provider cleanup methods work."""
    logger.info("\n=== Testing Provider Cleanup Methods ===")
    
    from providers import SileroVADProvider, SherpaOnnxLocalProvider
    
    # Test VAD cleanup
    logger.info("\n[Test 2] Testing VAD provider cleanup...")
    gc.collect()
    before_vad = get_memory_usage_mb()
    
    vad = SileroVADProvider(threshold=0.5)
    after_create = get_memory_usage_mb()
    logger.info(f"VAD created: {after_create - before_vad:.1f} MB increase")
    
    # Use the VAD to load model
    test_audio = b'\x00' * 1024
    vad.detect(test_audio)
    
    # Cleanup
    vad.cleanup()
    del vad
    gc.collect()
    
    after_cleanup = get_memory_usage_mb()
    logger.info(f"VAD cleaned up: {after_cleanup - before_vad:.1f} MB net increase")
    
    vad_leak = (after_cleanup - before_vad)
    if vad_leak < 10:  # Allow 10MB overhead
        logger.info("✓ VAD cleanup working")
    else:
        logger.warning(f"⚠ VAD cleanup may have issue: {vad_leak:.1f} MB residual")
    
    # Test ASR cleanup
    model_path = Path(__file__).parent.parent / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    
    if model_path.exists():
        logger.info("\n[Test 3] Testing ASR provider cleanup...")
        gc.collect()
        before_asr = get_memory_usage_mb()
        
        asr = SherpaOnnxLocalProvider(
            model_path=str(model_path),
            sample_rate=16000
        )
        after_create = get_memory_usage_mb()
        logger.info(f"ASR created: {after_create - before_asr:.1f} MB increase")
        
        # Cleanup
        asr.cleanup()
        del asr
        gc.collect()
        
        after_cleanup = get_memory_usage_mb()
        logger.info(f"ASR cleaned up: {after_cleanup - before_asr:.1f} MB net increase")
        
        asr_leak = (after_cleanup - before_asr)
        if asr_leak < 50:  # Allow 50MB overhead for ASR
            logger.info("✓ ASR cleanup working")
        else:
            logger.warning(f"⚠ ASR cleanup may have issue: {asr_leak:.1f} MB residual")
    
    logger.info("\n✅ Provider cleanup methods test completed")


async def main():
    """Run all memory tests."""
    try:
        # Test provider cleanup methods
        await test_provider_cleanup_directly()
        
        # Test full pipeline cleanup
        result = await test_memory_cleanup()
        
        if result:
            logger.info("\n" + "=" * 60)
            logger.info("🎉 All memory tests passed!")
            logger.info("=" * 60)
            logger.info("\nMemory cleanup is working correctly.")
            logger.info("Providers are properly releasing resources when sessions end.")
        else:
            logger.error("\n" + "=" * 60)
            logger.error("❌ Memory leak test failed!")
            logger.error("=" * 60)
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"\n❌ Test error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

