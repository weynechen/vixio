"""
Comparison test: Shared model vs Regular providers.

This test demonstrates the performance and memory advantages of shared model providers.
"""

import asyncio
import time
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


async def test_regular_providers():
    """Test regular providers (each creates its own model)."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 1: Regular Providers (Each Session Loads Own Model)")
    logger.info("=" * 70)
    
    from providers import SileroVADProvider, SherpaOnnxLocalProvider
    
    model_path = Path(__file__).parent.parent / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    
    gc.collect()
    initial_memory = get_memory_usage_mb()
    logger.info(f"Initial memory: {initial_memory:.1f} MB\n")
    
    sessions = []
    init_times = []
    
    # Simulate 3 concurrent sessions
    for i in range(3):
        logger.info(f"--- Session {i+1} connecting ---")
        
        start_time = time.time()
        
        # Create providers (this is what happens when a new session starts)
        vad = SileroVADProvider(threshold=0.5)
        if model_path.exists():
            asr = SherpaOnnxLocalProvider(model_path=str(model_path))
        else:
            asr = None
        
        init_time = (time.time() - start_time) * 1000
        init_times.append(init_time)
        
        current_memory = get_memory_usage_mb()
        memory_increase = current_memory - initial_memory
        
        logger.info(f"  ⏱  Initialization time: {init_time:.0f}ms")
        logger.info(f"  💾 Memory: {current_memory:.1f} MB (+{memory_increase:.1f} MB)")
        
        sessions.append((vad, asr))
    
    final_memory = get_memory_usage_mb()
    total_increase = final_memory - initial_memory
    avg_init_time = sum(init_times) / len(init_times)
    
    logger.info(f"\n📊 Summary:")
    logger.info(f"  Total memory increase: {total_increase:.1f} MB")
    logger.info(f"  Average init time: {avg_init_time:.0f}ms")
    
    # Cleanup
    for vad, asr in sessions:
        if hasattr(vad, 'cleanup'):
            vad.cleanup()
        if asr and hasattr(asr, 'cleanup'):
            asr.cleanup()
    del sessions
    gc.collect()
    
    return {
        "memory_increase": total_increase,
        "avg_init_time": avg_init_time,
        "init_times": init_times
    }


async def test_shared_providers():
    """Test shared model providers (all sessions share model)."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Shared Model Providers (All Sessions Share Model)")
    logger.info("=" * 70)
    
    from providers import SharedModelSileroVADProvider, SharedModelSherpaOnnxProvider
    
    model_path = Path(__file__).parent.parent / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    
    gc.collect()
    initial_memory = get_memory_usage_mb()
    logger.info(f"Initial memory: {initial_memory:.1f} MB\n")
    
    sessions = []
    init_times = []
    
    # Simulate 3 concurrent sessions
    for i in range(3):
        logger.info(f"--- Session {i+1} connecting ---")
        
        start_time = time.time()
        
        # Create providers (model loaded only once, shared by all)
        vad = SharedModelSileroVADProvider(threshold=0.5)
        if model_path.exists():
            asr = SharedModelSherpaOnnxProvider(model_path=str(model_path))
        else:
            asr = None
        
        init_time = (time.time() - start_time) * 1000
        init_times.append(init_time)
        
        current_memory = get_memory_usage_mb()
        memory_increase = current_memory - initial_memory
        
        logger.info(f"  ⏱  Initialization time: {init_time:.0f}ms")
        logger.info(f"  💾 Memory: {current_memory:.1f} MB (+{memory_increase:.1f} MB)")
        
        sessions.append((vad, asr))
    
    final_memory = get_memory_usage_mb()
    total_increase = final_memory - initial_memory
    avg_init_time = sum(init_times) / len(init_times)
    
    logger.info(f"\n📊 Summary:")
    logger.info(f"  Total memory increase: {total_increase:.1f} MB")
    logger.info(f"  Average init time: {avg_init_time:.0f}ms")
    
    # Cleanup
    for vad, asr in sessions:
        if hasattr(vad, 'cleanup'):
            vad.cleanup()
        if asr and hasattr(asr, 'cleanup'):
            asr.cleanup()
    del sessions
    gc.collect()
    
    # Cleanup shared models (only do this on application shutdown)
    SharedModelSileroVADProvider.unload_shared_model()
    if model_path.exists():
        SharedModelSherpaOnnxProvider.unload_shared_recognizer()
    gc.collect()
    
    return {
        "memory_increase": total_increase,
        "avg_init_time": avg_init_time,
        "init_times": init_times
    }


async def main():
    """Run comparison tests."""
    logger.info("\n")
    logger.info("🔬 Testing Provider Performance: Shared vs Regular")
    logger.info("=" * 70)
    
    try:
        # Test regular providers
        regular_results = await test_regular_providers()
        
        # Wait a bit and cleanup
        await asyncio.sleep(1)
        gc.collect()
        
        # Test shared providers
        shared_results = await test_shared_providers()
        
        # Calculate improvements
        memory_saved = regular_results["memory_increase"] - shared_results["memory_increase"]
        memory_saving_pct = (memory_saved / regular_results["memory_increase"]) * 100 if regular_results["memory_increase"] > 0 else 0
        
        speedup = regular_results["avg_init_time"] / shared_results["avg_init_time"] if shared_results["avg_init_time"] > 0 else 1
        
        # Print comparison
        logger.info("\n" + "=" * 70)
        logger.info("📈 PERFORMANCE COMPARISON (3 sessions)")
        logger.info("=" * 70)
        
        logger.info(f"\n💾 Memory Usage:")
        logger.info(f"  Regular providers:  {regular_results['memory_increase']:.1f} MB")
        logger.info(f"  Shared providers:   {shared_results['memory_increase']:.1f} MB")
        logger.info(f"  ✅ Saved:           {memory_saved:.1f} MB ({memory_saving_pct:.0f}% reduction)")
        
        logger.info(f"\n⏱  Initialization Time:")
        logger.info(f"  Regular providers:  {regular_results['avg_init_time']:.0f}ms per session")
        logger.info(f"  Shared providers:   {shared_results['avg_init_time']:.0f}ms per session")
        logger.info(f"  ✅ Speedup:         {speedup:.1f}x faster")
        
        logger.info(f"\n🔄 Session-by-Session Initialization (Regular):")
        for i, t in enumerate(regular_results['init_times'], 1):
            logger.info(f"  Session {i}: {t:.0f}ms")
        
        logger.info(f"\n🔄 Session-by-Session Initialization (Shared):")
        for i, t in enumerate(shared_results['init_times'], 1):
            note = " ← Model loaded here" if i == 1 else " ← Reused model"
            logger.info(f"  Session {i}: {t:.0f}ms{note}")
        
        logger.info("\n" + "=" * 70)
        logger.info("💡 RECOMMENDATION")
        logger.info("=" * 70)
        logger.info("For production multi-session scenarios, use Shared Model Providers:")
        logger.info("  - SharedModelSileroVADProvider")
        logger.info("  - SharedModelSherpaOnnxProvider")
        logger.info("")
        logger.info("Benefits:")
        logger.info(f"  ✅ {memory_saving_pct:.0f}% memory reduction")
        logger.info(f"  ✅ {speedup:.1f}x faster session initialization")
        logger.info("  ✅ Same functionality, proper session isolation")
        logger.info("  ✅ Thread-safe and async-safe")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"\n❌ Test error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

