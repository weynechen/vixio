"""
Session manager - connects Transport to Pipelines

Responsibilities:
1. Listen for new connections from Transport
2. Create a Pipeline instance for each connection
3. Route chunks: Transport input -> Pipeline -> Transport output
4. Manage pipeline lifecycle
"""

import asyncio
from typing import Callable, Dict
from vixio.core.transport import TransportBase
from vixio.core.pipeline import Pipeline
import logging

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Session manager - connects Transport to Pipelines.
    
    Responsibilities:
    1. Listen for new connections from Transport
    2. Create a Pipeline instance for each connection
    3. Route chunks: Transport input -> Pipeline -> Transport output
    4. Manage pipeline lifecycle
    
    Design:
    - Each connection gets its own Pipeline instance (isolated state)
    - Pipeline runs as long as connection is alive
    - Automatically cleanup on disconnect
    """
    
    def __init__(
        self,
        transport: TransportBase,
        pipeline_factory: Callable[[], Pipeline]
    ):
        """
        Initialize session manager.
        
        Args:
            transport: Transport to manage connections for
            pipeline_factory: Factory function that creates a fresh Pipeline for each connection
        """
        self.transport = transport
        self.pipeline_factory = pipeline_factory
        self._sessions: Dict[str, asyncio.Task] = {}  # connection_id -> pipeline task
        self.logger = logging.getLogger("vixio.SessionManager")
    
    async def start(self) -> None:
        """
        Start the session manager.
        
        This registers connection handler and starts the transport.
        """
        self.logger.info("Starting session manager...")
        
        # Register for new connections
        await self.transport.on_new_connection(self._handle_connection)
        
        # Start transport server
        await self.transport.start()
        
        self.logger.info("Session manager started")
    
    async def stop(self) -> None:
        """
        Stop the session manager.
        
        This cancels all active pipelines and stops transport.
        """
        self.logger.info("Stopping session manager...")
        
        # Cancel all pipeline tasks
        for connection_id, task in self._sessions.items():
            self.logger.debug(f"Cancelling session for connection {connection_id[:8]}")
            task.cancel()
        
        # Wait for all to finish
        if self._sessions:
            self.logger.info(f"Waiting for {len(self._sessions)} sessions to finish...")
            await asyncio.gather(*self._sessions.values(), return_exceptions=True)
        
        # Stop transport
        await self.transport.stop()
        
        self.logger.info("Session manager stopped")
    
    async def _handle_connection(self, connection_id: str) -> None:
        """
        Handle new client connection.
        
        Creates and runs a pipeline for this connection.
        
        Args:
            connection_id: Unique identifier for the connection
        """
        self.logger.info(f"New connection: {connection_id[:8]}")
        
        # Create fresh pipeline for this session
        pipeline = self.pipeline_factory()
        self.logger.debug(f"Created pipeline for connection {connection_id[:8]}: {pipeline}")
        
        # Run pipeline in background task
        task = asyncio.create_task(
            self._run_pipeline(connection_id, pipeline)
        )
        
        self._sessions[connection_id] = task
        
        # Cleanup when done
        task.add_done_callback(lambda _: self._cleanup_session(connection_id))
    
    async def _run_pipeline(self, connection_id: str, pipeline: Pipeline) -> None:
        """
        Run pipeline for a connection.
        
        Flow:
        1. Get input stream from transport
        2. Run pipeline (yields output chunks)
        3. Send output chunks back to transport
        
        Args:
            connection_id: Connection ID to run pipeline for
            pipeline: Pipeline instance to run
        """
        self.logger.info(f"Starting pipeline for connection {connection_id[:8]}")
        
        try:
            # Get input stream from transport
            input_stream = self.transport.input_stream(connection_id)
            
            # Run pipeline and send outputs
            chunk_count = 0
            async for output_chunk in pipeline.run(input_stream):
                await self.transport.output_chunk(connection_id, output_chunk)
                chunk_count += 1
            
            self.logger.info(f"Pipeline completed for connection {connection_id[:8]}, processed {chunk_count} chunks")
        
        except asyncio.CancelledError:
            # Session cancelled (normal shutdown)
            self.logger.info(f"Pipeline cancelled for connection {connection_id[:8]}")
        
        except Exception as e:
            # Pipeline error
            self.logger.error(f"Pipeline error for connection {connection_id[:8]}: {e}", exc_info=True)
        
        finally:
            # Ensure cleanup
            self._cleanup_session(connection_id)
    
    def _cleanup_session(self, connection_id: str) -> None:
        """
        Cleanup session resources.
        
        Args:
            connection_id: Connection to cleanup
        """
        if connection_id in self._sessions:
            self.logger.debug(f"Cleaning up session for connection {connection_id[:8]}")
            del self._sessions[connection_id]
    
    def get_active_session_count(self) -> int:
        """
        Get number of active sessions.
        
        Returns:
            Number of active sessions
        """
        return len(self._sessions)
    
    def get_active_sessions(self) -> list:
        """
        Get list of active session IDs.
        
        Returns:
            List of connection IDs
        """
        return list(self._sessions.keys())
