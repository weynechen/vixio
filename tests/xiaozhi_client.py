"""
Xiaozhi WebSocket client for testing

This is a simple client implementation for testing the Xiaozhi protocol.
It simulates an ESP32 device connecting to the server.
"""

import asyncio
import json
from typing import Optional, List, Dict, Any
import websockets
from websockets.client import WebSocketClientProtocol


class XiaozhiClient:
    """
    Simple Xiaozhi protocol client for testing.
    
    Simulates an ESP32 device connecting to the server via WebSocket.
    """
    
    def __init__(
        self,
        server_url: str = "ws://localhost:8080/xiaozhi/v1/",
        session_id: Optional[str] = None
    ):
        """
        Initialize Xiaozhi client.
        
        Args:
            server_url: WebSocket server URL
            session_id: Optional session identifier
        """
        self.server_url = server_url
        self.session_id = session_id
        self.websocket: Optional[WebSocketClientProtocol] = None
        self._received_messages: List[Dict[str, Any]] = []
    
    async def connect(self) -> bool:
        """
        Connect to WebSocket server.
        
        Returns:
            True if connected successfully
        """
        try:
            self.websocket = await websockets.connect(self.server_url)
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from server."""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
    
    async def send_text(self, text: str) -> None:
        """
        Send text message to server.
        
        Args:
            text: Text content
        """
        if not self.websocket:
            raise ConnectionError("Not connected")
        
        message = {
            "type": "text",
            "content": text
        }
        
        if self.session_id:
            message["session_id"] = self.session_id
        
        await self.websocket.send(json.dumps(message, ensure_ascii=False))
    
    async def send_audio(self, audio_data: bytes) -> None:
        """
        Send audio data to server.
        
        Args:
            audio_data: Opus-encoded audio bytes
        """
        if not self.websocket:
            raise ConnectionError("Not connected")
        
        await self.websocket.send(audio_data)
    
    async def send_control(self, action: str, **params) -> None:
        """
        Send control message.
        
        Args:
            action: Control action (interrupt, stop, etc.)
            **params: Additional parameters
        """
        if not self.websocket:
            raise ConnectionError("Not connected")
        
        message = {
            "type": "control",
            "action": action,
            **params
        }
        
        if self.session_id:
            message["session_id"] = self.session_id
        
        await self.websocket.send(json.dumps(message))
    
    async def receive_message(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        Receive message from server.
        
        Args:
            timeout: Receive timeout in seconds
            
        Returns:
            Parsed message dictionary or None if timeout
        """
        if not self.websocket:
            raise ConnectionError("Not connected")
        
        try:
            data = await asyncio.wait_for(
                self.websocket.recv(),
                timeout=timeout
            )
            
            # Parse message
            if isinstance(data, bytes):
                # Binary audio message
                message = {
                    "type": "audio",
                    "data": data,
                    "size": len(data)
                }
            else:
                # JSON text message
                message = json.loads(data)
            
            # Store received message
            self._received_messages.append(message)
            
            return message
        
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"Receive error: {e}")
            return None
    
    def get_received_messages(self) -> List[Dict[str, Any]]:
        """Get all received messages."""
        return self._received_messages.copy()
    
    def clear_messages(self) -> None:
        """Clear received messages buffer."""
        self._received_messages.clear()


# Example usage
async def example():
    """Example usage of XiaozhiClient"""
    client = XiaozhiClient(server_url="ws://localhost:8080/xiaozhi/v1/")
    
    # Connect
    connected = await client.connect()
    if not connected:
        print("Failed to connect")
        return
    
    print("Connected to server")
    
    # Wait for hello message
    await asyncio.sleep(0.5)
    hello = await client.receive_message()
    if hello:
        print(f"Received hello: {hello}")
    
    # Send text message
    await client.send_text("你好，这是测试消息")
    print("Sent text message")
    
    # Receive responses
    for i in range(10):
        msg = await client.receive_message(timeout=2.0)
        if msg:
            print(f"Received[{i}]: {msg.get('type')}")
            if msg.get('type') == 'tts' and msg.get('state') == 'stop':
                break
    
    # Disconnect
    await client.disconnect()
    print("Disconnected")


if __name__ == "__main__":
    asyncio.run(example())

