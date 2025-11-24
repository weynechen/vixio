"""
Simple integration test for Xiaozhi Transport with authentication and OTA
"""

import asyncio
import httpx
from transports.xiaozhi import XiaozhiTransport


async def test_xiaozhi_server():
    """Test Xiaozhi server with all features"""
    
    # Configuration
    config = {
        "server": {
            "host": "127.0.0.1",
            "port": 18888,
            "websocket_path": "/xiaozhi/v1/",
            "auth": {
                "enabled": True,
                "allowed_devices": [],
                "expire_seconds": 3600,
            },
            "auth_key": "test_secret_key_for_integration",
            "timezone_offset": 8,
        }
    }
    
    # Create transport
    print("Creating Xiaozhi transport...")
    transport = XiaozhiTransport(
        host=config["server"]["host"],
        port=config["server"]["port"],
        websocket_path=config["server"]["websocket_path"],
        config=config,
    )
    
    # Start server
    print(f"Starting server on {config['server']['host']}:{config['server']['port']}...")
    await transport.start()
    await asyncio.sleep(1)  # Give server time to start
    
    try:
        # Test HTTP endpoints
        base_url = f"http://{config['server']['host']}:{config['server']['port']}"
        
        async with httpx.AsyncClient() as client:
            # Test root endpoint
            print("\n1. Testing root endpoint...")
            response = await client.get(f"{base_url}/")
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")
            assert response.status_code == 200
            
            # Test health endpoint
            print("\n2. Testing health endpoint...")
            response = await client.get(f"{base_url}/health")
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")
            assert response.status_code == 200
            
            # Test OTA GET endpoint
            print("\n3. Testing OTA GET endpoint...")
            response = await client.get(f"{base_url}/xiaozhi/ota/")
            print(f"   Status: {response.status_code}")
            data = response.json()
            print(f"   WebSocket URL: {data.get('websocket_url')}")
            assert response.status_code == 200
            assert "websocket_url" in data
            
            # Test OTA POST endpoint (WebSocket mode)
            print("\n4. Testing OTA POST endpoint (WebSocket mode)...")
            request_body = {
                "application": {"version": "1.0.0"},
                "device": {"model": "XiaoZhi-ESP32"}
            }
            response = await client.post(
                f"{base_url}/xiaozhi/ota/",
                json=request_body,
                headers={
                    "device-id": "AA:BB:CC:DD:EE:FF",
                    "client-id": "test-client-123"
                }
            )
            print(f"   Status: {response.status_code}")
            data = response.json()
            print(f"   Server time: {data.get('server_time', {}).get('timestamp')}")
            print(f"   WebSocket URL: {data.get('websocket', {}).get('url')}")
            print(f"   Token generated: {'Yes' if data.get('websocket', {}).get('token') else 'No'}")
            assert response.status_code == 200
            assert "websocket" in data
            assert "token" in data["websocket"]
            
            # Test connections endpoint
            print("\n5. Testing connections endpoint...")
            response = await client.get(f"{base_url}/connections")
            print(f"   Status: {response.status_code}")
            data = response.json()
            print(f"   Active connections: {data.get('count')}")
            assert response.status_code == 200
        
        print("\n✅ All integration tests passed!")
        
    finally:
        # Stop server
        print("\nStopping server...")
        await transport.stop()
        await asyncio.sleep(0.5)
        print("Server stopped.")


if __name__ == "__main__":
    print("=" * 60)
    print("Xiaozhi Transport Integration Test")
    print("=" * 60)
    asyncio.run(test_xiaozhi_server())

