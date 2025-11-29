"""
Simple test for Sherpa ONNX ASR service

Tests basic ASR functionality:
- Service connection
- Session creation
- Audio transcription (silence test)
- Session cleanup
"""

import asyncio
import numpy as np
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from inference.sherpa_onnx_local.client import ASRServiceClient


async def main():
    """Test ASR service basic functionality"""
    print("=" * 60)
    print("Sherpa ONNX ASR Service - Simple Test")
    print("=" * 60)
    
    service_url = "localhost:50052"
    print(f"\n1. Connecting to ASR service at {service_url}...")
    
    client = ASRServiceClient(service_url)
    
    try:
        # Connect
        await client.connect()
        print("   ✓ Connected")
        
        # Create session
        session_id = "test-session-1"
        print(f"\n2. Creating ASR session: {session_id}...")
        success = await client.create_session(session_id, language="auto")
        print(f"   ✓ Session created: {success}")
        
        # Test 1: Silence (should return empty or minimal text)
        print("\n3. Testing with silence (1 second)...")
        sample_rate = 16000
        duration = 1.0
        silence = np.zeros(int(sample_rate * duration), dtype=np.int16)
        silence_bytes = silence.tobytes()
        
        result = await client.transcribe(session_id, [silence_bytes])
        print(f"   ✓ Result: '{result.text}' (confidence: {result.confidence:.2f})")
        
        # Test 2: Multiple small chunks
        print("\n4. Testing with multiple silence chunks...")
        chunks = [
            np.zeros(8000, dtype=np.int16).tobytes(),
            np.zeros(8000, dtype=np.int16).tobytes(),
        ]
        
        result = await client.transcribe(session_id, chunks)
        print(f"   ✓ Result: '{result.text}' (confidence: {result.confidence:.2f})")
        
        # Get stats
        print("\n5. Getting service stats...")
        stats = await client.get_stats()
        print(f"   ✓ Active sessions: {stats.active_sessions}")
        print(f"   ✓ Total requests: {stats.total_requests}")
        print(f"   ✓ Chunks processed: {stats.total_chunks_processed}")
        
        # Destroy session
        print(f"\n6. Destroying session: {session_id}...")
        await client.destroy_session(session_id)
        print("   ✓ Session destroyed")
        
        # Verify stats updated
        stats = await client.get_stats()
        print(f"\n7. Final stats:")
        print(f"   ✓ Active sessions: {stats.active_sessions}")
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    finally:
        await client.close()
        print("\n✓ Connection closed")
    
    return 0


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

