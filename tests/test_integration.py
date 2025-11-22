"""
Integration tests for Vixio framework

Prerequisites:
- Server must be running: uv run python examples/agent_chat.py
- Or: uv run python examples/voice_chat.py

Run tests:
- All: uv run pytest tests/test_integration.py -v
- Specific: uv run pytest tests/test_integration.py::TestIntegration::test_websocket_connection -v -s
"""

import asyncio
import pytest
from pathlib import Path

from tests.xiaozhi_client import XiaozhiClient


# Default server URL
DEFAULT_SERVER_URL = "ws://localhost:8080/xiaozhi/v1/"


@pytest.mark.integration
class TestIntegration:
    """
    Integration tests for Vixio server.
    
    These tests simulate real client devices connecting to an external server.
    
    Prerequisites:
    - Start server: uv run python examples/agent_chat.py
    - Server should be running on ws://localhost:8080/xiaozhi/v1/
    """
    
    async def _ensure_server_running(self, client: XiaozhiClient) -> bool:
        """Helper to check if server is running."""
        try:
            connected = await client.connect()
            if not connected:
                pytest.skip(
                    f"Server not running at {client.server_url}. "
                    "Start with: uv run python examples/agent_chat.py"
                )
                return False
            return True
        except Exception as e:
            pytest.skip(
                f"Cannot connect to server: {e}. "
                "Start with: uv run python examples/agent_chat.py"
            )
            return False
    
    @pytest.mark.asyncio
    async def test_websocket_connection(self):
        """Test WebSocket connection to server."""
        client = XiaozhiClient(server_url=DEFAULT_SERVER_URL)
        
        # Connect
        await self._ensure_server_running(client)
        
        # Should receive hello message
        await asyncio.sleep(0.5)
        hello_msg = await client.receive_message()
        assert hello_msg is not None, "No hello message received"
        assert hello_msg.get("type") == "hello", "Expected hello message"
        
        print(f"\nReceived hello: {hello_msg}")
        
        # Cleanup
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_text_message_flow(self):
        """Test complete text message flow with Agent."""
        client = XiaozhiClient(server_url=DEFAULT_SERVER_URL)
        await self._ensure_server_running(client)
        
        # Wait for hello
        await asyncio.sleep(0.5)
        await client.receive_message()
        
        # Send text message
        test_message = "你好，今天天气怎么样？"
        await client.send_text(test_message)
        print(f"\nSent: {test_message}")
        
        # Receive responses until conversation ends
        received_text = False
        received_audio = False
        agent_started = False
        tts_started = False
        conversation_ended = False
        max_messages = 20
        message_count = 0
        
        while not conversation_ended and message_count < max_messages:
            try:
                msg = await asyncio.wait_for(
                    client.receive_message(),
                    timeout=15.0
                )
                if msg is None:
                    break
                
                message_count += 1
                msg_type = msg.get('type')
                
                print(f"[{message_count}] Received: {msg_type}")
                
                if msg_type == "text":
                    received_text = True
                    content = msg.get("content", "")
                    print(f"    Content: {content[:50]}...")
                
                elif msg_type == "tts":
                    state = msg.get('state')
                    if state == "start":
                        tts_started = True
                        print("    TTS started")
                    elif state == "stop":
                        conversation_ended = True
                        print("    TTS stopped - Conversation ended")
                
                elif msg_type == "audio":
                    received_audio = True
                    size = msg.get("size", 0)
                    print(f"    Audio: {size} bytes")
                
                elif msg_type == "state":
                    state = msg.get("state")
                    print(f"    State: {state}")
            
            except asyncio.TimeoutError:
                print("Timeout waiting for response")
                break
        
        # Assertions
        assert received_text or agent_started, "Should receive text response from agent"
        
        print(f"\nSummary:")
        print(f"  Messages received: {message_count}")
        print(f"  Text responses: {received_text}")
        print(f"  Audio responses: {received_audio}")
        print(f"  TTS started: {tts_started}")
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_control_interrupt(self):
        """Test control interrupt message."""
        client = XiaozhiClient(server_url=DEFAULT_SERVER_URL)
        await self._ensure_server_running(client)
        
        # Wait for hello
        await asyncio.sleep(0.5)
        await client.receive_message()
        
        # Send interrupt
        await client.send_control("interrupt")
        print("\nSent interrupt")
        
        await asyncio.sleep(0.5)
        
        # Server should handle interrupt gracefully
        # (May or may not send response)
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_multiple_sessions(self):
        """Test multiple client sessions are isolated."""
        client1 = XiaozhiClient(
            server_url=DEFAULT_SERVER_URL,
            session_id="test-session-1"
        )
        client2 = XiaozhiClient(
            server_url=DEFAULT_SERVER_URL,
            session_id="test-session-2"
        )
        
        # Connect both clients
        await self._ensure_server_running(client1)
        await client2.connect()
        
        # Wait for hello messages
        await asyncio.sleep(0.5)
        await client1.receive_message()
        await client2.receive_message()
        
        # Clear buffers
        client1.clear_messages()
        client2.clear_messages()
        
        # Client1 sends message
        await client1.send_text("Message from client 1")
        print("\nClient1 sent message")
        
        # Client1 should receive responses
        max_wait = 10
        for i in range(max_wait):
            msg = await client1.receive_message(timeout=2.0)
            if msg:
                print(f"Client1 received: {msg.get('type')}")
                if msg.get('type') == 'tts' and msg.get('state') == 'stop':
                    break
        
        client1_messages = client1.get_received_messages()
        assert len(client1_messages) > 0, "Client1 should receive responses"
        
        # Client2 should not receive client1's response
        msg = await client2.receive_message(timeout=1.0)
        if msg:
            print(f"Client2 received: {msg}")
        
        # Sessions should be isolated
        print(f"\nClient1 messages: {len(client1_messages)}")
        print(f"Client2 messages: {len(client2.get_received_messages())}")
        
        await client1.disconnect()
        await client2.disconnect()
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_http_endpoints(self):
        """Test HTTP endpoints."""
        import httpx
        
        base_url = "http://localhost:8080"
        
        async with httpx.AsyncClient() as http_client:
            try:
                # Test root endpoint
                response = await http_client.get(f"{base_url}/", timeout=5.0)
                assert response.status_code == 200
                data = response.json()
                print(f"\nRoot endpoint: {data}")
                assert "name" in data
                assert "status" in data
                assert data["status"] == "running"
                
                # Test health endpoint
                response = await http_client.get(f"{base_url}/health", timeout=5.0)
                assert response.status_code == 200
                data = response.json()
                print(f"Health endpoint: {data}")
                assert "status" in data
                assert data["status"] == "healthy"
                
                # Test connections endpoint
                response = await http_client.get(f"{base_url}/connections", timeout=5.0)
                assert response.status_code == 200
                data = response.json()
                print(f"Connections endpoint: {data}")
                assert "count" in data
                assert "sessions" in data
            
            except httpx.ConnectError:
                pytest.skip(
                    f"HTTP server not running at {base_url}. "
                    "Start with: uv run python examples/agent_chat.py"
                )


# Manual test for development
async def manual_test():
    """
    Manual test function for development.
    
    Run with: uv run python tests/test_integration.py
    
    Prerequisites: Server must be running (uv run python examples/agent_chat.py)
    """
    print("="*70)
    print("Vixio Integration Test")
    print("="*70)
    print(f"Target: {DEFAULT_SERVER_URL}")
    print("Make sure server is running: uv run python examples/agent_chat.py")
    print("="*70)
    
    await asyncio.sleep(1)
    
    client = XiaozhiClient(server_url=DEFAULT_SERVER_URL)
    
    try:
        # Connect
        print("\n1. Connecting...")
        connected = await client.connect()
        
        if not connected:
            print("❌ Failed to connect. Is server running?")
            return
        
        print("✅ Connected!")
        
        # Wait for hello
        print("\n2. Waiting for hello message...")
        await asyncio.sleep(0.5)
        hello_msg = await client.receive_message()
        if hello_msg:
            print(f"✅ Received hello: {hello_msg}")
        else:
            print("⚠️  No hello message received")
        
        # Send text message
        print("\n3. Sending text message...")
        await client.send_text("你好，这是一个测试消息")
        print("✅ Message sent")
        
        # Receive responses
        print("\n4. Receiving responses...")
        conversation_ended = False
        max_messages = 20
        message_count = 0
        
        while not conversation_ended and message_count < max_messages:
            try:
                msg = await asyncio.wait_for(
                    client.receive_message(),
                    timeout=20.0
                )
                
                if msg:
                    message_count += 1
                    msg_type = msg.get('type')
                    print(f"   [{message_count}] {msg_type}", end="")
                    
                    if msg_type == "text":
                        content = msg.get('content', '')
                        print(f" - {content[:50]}...")
                    elif msg_type == "tts":
                        state = msg.get('state')
                        print(f" - {state}")
                        if state == "stop":
                            conversation_ended = True
                    elif msg_type == "audio":
                        size = msg.get('size', 0)
                        print(f" - {size} bytes")
                    else:
                        print()
            
            except asyncio.TimeoutError:
                print("   ⏱️  Timeout")
                break
        
        print(f"\n✅ Received {message_count} messages")
    
    finally:
        await client.disconnect()
        print("\n" + "="*70)
        print("Test completed")
        print("="*70)


if __name__ == "__main__":
    asyncio.run(manual_test())
