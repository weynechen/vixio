"""
Example 1: Simple echo server

This is the simplest possible pipeline - just echoes audio back to the client.
Demonstrates basic Transport and Pipeline integration.
"""

import asyncio
from loguru import logger
from core.pipeline import Pipeline
from core.station import PassthroughStation
from core.session import SessionManager
from transports.xiaozhi import XiaozhiTransport

async def main():
    """
    Simple echo server example.
    
    This server:
    1. Receives audio from Xiaozhi client
    2. Passes it through unchanged (echo)
    3. Sends it back to the client
    """
    logger.info("=== Simple Echo Server ===")
    
    # Step 1: Create transport
    transport = XiaozhiTransport(
        host="0.0.0.0",
        port=8080
    )
    
    # Step 2: Create pipeline factory (just passthrough)
    def create_pipeline():
        return Pipeline(
            stations=[
                PassthroughStation(name="Echo"),
            ],
            name="EchoVoiceBuffer"
        )
    
    # Step 3: Create session manager
    manager = SessionManager(
        transport=transport,
        pipeline_factory=create_pipeline
    )
    
    # Step 4: Start server
    logger.info("Starting echo server on ws://0.0.0.0:8080")
    await manager.start()
    
    # Step 5: Run until interrupted
    try:
        logger.info("Server running. Press Ctrl+C to stop.")
        await asyncio.Event().wait()  # Wait forever
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        await manager.stop()
        logger.info("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())
