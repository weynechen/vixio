"""
Xiaozhi client simulator for testing.

This module simulates an ESP32 client connecting to the Vixio server.
Can be used both as a standalone test script and as a test module.
"""

import asyncio
import json
from pathlib import Path
import sys
from typing import Optional, Callable, Any
import websockets
from loguru import logger

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vixio.protocol.xiaozhi.messages import (
    XiaozhiMessageType,
    XiaozhiControlAction,
    XiaozhiStateType,
)


class XiaozhiClient:
    """Simulated Xiaozhi ESP32 client for testing."""
    
    def __init__(
        self,
        server_url: str = "ws://localhost:8000/xiaozhi/v1/",
        session_id: Optional[str] = None,
        on_message: Optional[Callable] = None,
    ):
        """
        Initialize client.
        
        Args:
            server_url: WebSocket server URL
            session_id: Optional session ID
            on_message: Optional callback for received messages
        """
        self.server_url = server_url
        self.session_id = session_id or "test-client-session"
        self.on_message = on_message
        self.websocket: Optional[Any] = None
        self.connected = False
        self.received_messages = []
        
    async def connect(self) -> bool:
        """
        Connect to server.
        
        Returns:
            True if connected successfully
        """
        try:
            logger.info(f"Connecting to {self.server_url}")
            self.websocket = await websockets.connect(self.server_url)
            self.connected = True
            logger.success("Connected to server")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from server."""
        if self.websocket:
            await self.websocket.close()
            self.connected = False
            logger.info("Disconnected from server")
    
    async def send_text(self, content: str, **kwargs):
        """
        Send text message.
        
        Args:
            content: Text content
            **kwargs: Additional parameters
        """
        if not self.connected or not self.websocket:
            logger.error("Not connected to server")
            return
        
        message = {
            "type": XiaozhiMessageType.TEXT.value,
            "content": content,
            "session_id": self.session_id,
        }
        message.update(kwargs)
        
        logger.info(f"Sending text: {content}")
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
    
    async def send_audio(self, audio_data: bytes):
        """
        Send audio data.
        
        Args:
            audio_data: Raw audio bytes (Opus-encoded)
        """
        if not self.connected or not self.websocket:
            logger.error("Not connected to server")
            return
        
        # logger.info(f"Sending audio: {len(audio_data)} bytes")
        await self.websocket.send(audio_data)
    
    async def send_control(self, action: str, **kwargs):
        """
        Send control message.
        
        Args:
            action: Control action (abort, restart, pause, resume, wakeup)
            **kwargs: Additional parameters
        """
        if not self.connected or not self.websocket:
            logger.error("Not connected to server")
            return
        
        message = {
            "type": XiaozhiMessageType.CONTROL.value,
            "action": action,
            "session_id": self.session_id,
        }
        message.update(kwargs)
        
        logger.info(f"Sending control: {action}")
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
    
    async def send_hello(self, **kwargs):
        """
        Send hello message (client-side handshake).
        
        Args:
            **kwargs: Additional parameters
        """
        if not self.connected or not self.websocket:
            logger.error("Not connected to server")
            return
        
        message = {
            "type": XiaozhiMessageType.HELLO.value,
            "version": 1,
            "session_id": self.session_id,
            "device_type": "ESP32",
            "firmware_version": "1.0.0",
        }
        message.update(kwargs)
        
        logger.info("Sending hello message")
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
    
    async def receive_message(self) -> Optional[dict]:
        """
        Receive one message from server.
        
        Returns:
            Parsed message dict or None
        """
        if not self.connected or not self.websocket:
            logger.error("Not connected to server")
            return None
        
        try:
            data = await self.websocket.recv()
            
            # Parse message
            if isinstance(data, str):
                message = json.loads(data)
                logger.debug(f"Received text message: {message.get('type', 'unknown')}")
                logger.debug(f"Message content: {message}")
            elif isinstance(data, bytes):
                message = {
                    "type": "audio",
                    "audio_data": data,
                    "size": len(data),
                }
                logger.debug(f"Received audio: {len(data)} bytes")
            else:
                logger.warning(f"Unknown message type: {type(data)}")
                return None
            
            # Store message
            self.received_messages.append(message)
            
            # Call callback if provided
            if self.on_message:
                await self.on_message(message)
            
            return message
            
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            return None
    
    async def receive_loop(self):
        """Continuously receive messages from server."""
        logger.info("Starting receive loop")
        while self.connected:
            try:
                message = await self.receive_message()
                if message is None:
                    break
            except websockets.exceptions.ConnectionClosed:
                logger.info("Connection closed by server")
                self.connected = False
                break
            except Exception as e:
                logger.error(f"Receive loop error: {e}")
                break
    
    def get_received_messages(self, message_type: Optional[str] = None):
        """
        Get received messages, optionally filtered by type.
        
        Args:
            message_type: Optional message type filter
            
        Returns:
            List of messages
        """
        if message_type:
            return [m for m in self.received_messages if m.get("type") == message_type]
        return self.received_messages
    
    def clear_messages(self):
        """Clear received messages buffer."""
        self.received_messages.clear()


async def test_basic_connection():
    """Test basic connection and text message exchange."""
    logger.info("=== Test: Basic Connection ===")
    
    client = XiaozhiClient()
    
    # Connect
    connected = await client.connect()
    assert connected, "Failed to connect"
    
    # Wait for hello message from server
    await asyncio.sleep(0.5)
    hello_msg = await client.receive_message()
    assert hello_msg is not None, "No hello message received"
    assert hello_msg.get("type") == "hello", "Expected hello message"
    logger.success("✓ Received hello message")
    
    # Send text message
    await client.send_text("Hello from test client!")
    await asyncio.sleep(0.5)
    
    # Receive response
    response = await client.receive_message()
    assert response is not None, "No response received"
    logger.success("✓ Received response")
    
    # Disconnect
    await client.disconnect()
    logger.success("✓ Test completed successfully")


async def test_multiple_messages():
    """Test sending multiple messages."""
    logger.info("=== Test: Multiple Messages ===")
    
    client = XiaozhiClient()
    await client.connect()
    
    # Wait for hello
    await asyncio.sleep(0.5)
    await client.receive_message()
    
    # Send multiple text messages
    messages = ["Message 1", "Message 2", "Message 3"]
    for msg in messages:
        await client.send_text(msg)
        await asyncio.sleep(0.2)
    
    # Receive responses
    for _ in range(len(messages)):
        response = await client.receive_message()
        assert response is not None
    
    logger.success("✓ All messages sent and received")
    
    await client.disconnect()


async def test_control_messages():
    """Test control messages."""
    logger.info("=== Test: Control Messages ===")
    
    client = XiaozhiClient()
    await client.connect()
    
    # Wait for hello
    await asyncio.sleep(0.5)
    await client.receive_message()
    
    # Send control messages
    await client.send_control(XiaozhiControlAction.WAKEUP.value)
    await asyncio.sleep(0.2)
    
    await client.send_control(XiaozhiControlAction.ABORT.value)
    await asyncio.sleep(0.2)
    
    logger.success("✓ Control messages sent")
    
    await client.disconnect()


async def interactive_mode():
    """Interactive mode for manual testing."""
    logger.info("=== Interactive Mode ===")
    logger.info("Commands:")
    logger.info("  text <message>  - Send text message")
    logger.info("  control <action> - Send control message (wakeup, abort, etc.)")
    logger.info("  hello           - Send hello message")
    logger.info("  messages        - Show received messages")
    logger.info("  clear           - Clear message buffer")
    logger.info("  quit            - Exit")
    logger.info("")
    
    client = XiaozhiClient()
    await client.connect()
    
    # Start receive loop in background
    receive_task = asyncio.create_task(client.receive_loop())
    
    # Wait a bit for initial hello
    await asyncio.sleep(0.5)
    
    try:
        while True:
            # Get user input
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, input, "\n> "
                )
            except EOFError:
                break
            
            if not user_input.strip():
                continue
            
            parts = user_input.strip().split(maxsplit=1)
            command = parts[0].lower()
            
            if command == "quit":
                break
            elif command == "text" and len(parts) > 1:
                await client.send_text(parts[1])
            elif command == "control" and len(parts) > 1:
                await client.send_control(parts[1])
            elif command == "hello":
                await client.send_hello()
            elif command == "messages":
                messages = client.get_received_messages()
                logger.info(f"Received {len(messages)} messages:")
                for i, msg in enumerate(messages):
                    logger.info(f"  [{i}] {msg.get('type')}: {msg}")
            elif command == "clear":
                client.clear_messages()
                logger.info("Message buffer cleared")
            else:
                logger.warning(f"Unknown command: {command}")
    
    finally:
        receive_task.cancel()
        await client.disconnect()


async def main():
    """Main function."""
    # Setup logger
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
    )
    
    import argparse
    parser = argparse.ArgumentParser(description="Xiaozhi ESP32 Client Simulator")
    parser.add_argument(
        "--mode",
        choices=["test", "interactive"],
        default="test",
        help="Run mode: test (run automated tests) or interactive (manual testing)",
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/xiaozhi/v1/",
        help="Server WebSocket URL",
    )
    
    args = parser.parse_args()
    
    # Update default URL
    XiaozhiClient.server_url = args.url
    
    if args.mode == "interactive":
        await interactive_mode()
    else:
        # Run tests
        try:
            await test_basic_connection()
            logger.info("")
            
            await test_multiple_messages()
            logger.info("")
            
            await test_control_messages()
            logger.info("")
            
            logger.success("=" * 50)
            logger.success("All tests passed! ✓")
            logger.success("=" * 50)
        except AssertionError as e:
            logger.error(f"Test failed: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Test error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

