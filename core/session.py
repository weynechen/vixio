"""
Session manager - connects Transport to Pipelines

Responsibilities:
1. Listen for new connections from Transport
2. Create a Pipeline instance for each connection
3. Route chunks: Transport input -> Pipeline -> Transport output
4. Manage pipeline lifecycle
5. Handle interrupts via ControlBus
"""

import asyncio
from typing import Callable, Dict, Optional, AsyncIterator
from core.transport import TransportBase
from core.pipeline import Pipeline
from core.control_bus import ControlBus
from core.chunk import Chunk, ChunkType
from loguru import logger


class SessionManager:
    """
    Session manager - connects Transport to Pipelines.
    
    Responsibilities:
    1. Listen for new connections from Transport
    2. Create a Pipeline instance for each connection
    3. Route chunks: Transport input -> Pipeline -> Transport output
    4. Manage pipeline lifecycle
    5. Handle interrupts via ControlBus
    
    Design:
    - Each connection gets its own Pipeline and ControlBus instance
    - Pipeline runs as long as connection is alive
    - ControlBus handles interrupts and turn transitions
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
            pipeline_factory: Factory function (sync or async) that creates a fresh Pipeline for each connection
        """
        self.transport = transport
        self.pipeline_factory = pipeline_factory
        self._is_async_factory = asyncio.iscoroutinefunction(pipeline_factory)
        self._sessions: Dict[str, asyncio.Task] = {}  # connection_id -> pipeline task
        self._control_buses: Dict[str, ControlBus] = {}  # connection_id -> control bus
        self._pipelines: Dict[str, Pipeline] = {}  # connection_id -> pipeline
        self._interrupt_tasks: Dict[str, asyncio.Task] = {}  # connection_id -> interrupt handler task
        self.logger = logger.bind(component="SessionManager")
    
    async def start(self) -> None:
        """
        Start the session manager.
        
        This registers connection handler and starts the transport.
        """
        self.logger.info("Starting session manager...")
        
        # Register for new connections
        await self.transport.on_new_connection(self._handle_connection)
        
        # Register disconnect handler to cancel sessions
        if hasattr(self.transport, 'set_disconnect_handler'):
            self.transport.set_disconnect_handler(self.cancel_session)
        
        # Start transport server
        await self.transport.start()
        
        self.logger.info("Session manager started")
    
    async def stop(self) -> None:
        """
        Stop the session manager.
        
        This cancels all active pipelines, interrupt handlers, and stops transport.
        """
        self.logger.info("Stopping session manager...")
        
        # Cancel all interrupt handler tasks
        for connection_id, task in self._interrupt_tasks.items():
            self.logger.debug(f"Cancelling interrupt handler for connection {connection_id[:8]}")
            task.cancel()
        
        # Cancel all pipeline tasks
        for connection_id, task in self._sessions.items():
            self.logger.debug(f"Cancelling session for connection {connection_id[:8]}")
            task.cancel()
        
        # Wait for all to finish
        all_tasks = list(self._sessions.values()) + list(self._interrupt_tasks.values())
        if all_tasks:
            self.logger.info(f"Waiting for {len(all_tasks)} tasks to finish...")
            await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Stop transport
        await self.transport.stop()
        
        self.logger.info("Session manager stopped")
    
    async def _handle_connection(self, connection_id: str) -> None:
        """
        Handle new client connection.
        
        Creates pipeline, control bus, and interrupt handler for this connection.
        
        Args:
            connection_id: Unique identifier for the connection
        """
        self.logger.info(f"New connection: {connection_id[:8]}")
        
        # Create ControlBus for this session
        control_bus = ControlBus()
        self._control_buses[connection_id] = control_bus
        
        # Create fresh pipeline with ControlBus (support both sync and async factories)
        if self._is_async_factory:
            pipeline = await self.pipeline_factory()
        else:
            pipeline = self.pipeline_factory()
        pipeline.control_bus = control_bus
        self._pipelines[connection_id] = pipeline
        
        self.logger.debug(f"Created pipeline for connection {connection_id[:8]}: {pipeline}")
        
        # Start interrupt handler task
        interrupt_task = asyncio.create_task(
            self._handle_interrupts(connection_id, pipeline, control_bus),
            name=f"interrupt-handler-{connection_id[:8]}"
        )
        self._interrupt_tasks[connection_id] = interrupt_task
        
        # Notify transport that pipeline is ready (so it can send HELLO)
        if hasattr(self.transport, 'on_pipeline_ready'):
            await self.transport.on_pipeline_ready(connection_id)
        
        # Run pipeline in background task
        task = asyncio.create_task(
            self._run_pipeline(connection_id, pipeline),
            name=f"pipeline-{connection_id[:8]}"
        )
        
        self._sessions[connection_id] = task
        
        # Cleanup when done
        task.add_done_callback(lambda _: self._cleanup_session(connection_id))
    
    async def _handle_interrupts(
        self,
        connection_id: str,
        pipeline: Pipeline,
        control_bus: ControlBus
    ) -> None:
        """
        Handle interrupt signals for a connection.
        
        Listens for interrupts on ControlBus and:
        1. Clears pipeline queues (except input queue)
        2. Cancels slow tasks (Agent, TTS)
        3. Logs the interrupt
        
        Args:
            connection_id: Connection ID
            pipeline: Pipeline instance
            control_bus: ControlBus instance
        """
        self.logger.info(f"Starting interrupt handler for connection {connection_id[:8]}")
        
        try:
            while True:
                # Wait for interrupt signal
                interrupt = await control_bus.wait_for_interrupt()
                
                self.logger.warning(
                    f"[{connection_id[:8]}] Interrupt received: {interrupt.source} - {interrupt.reason}"
                )
                
                # Clear pipeline queues (keep input queue)
                # This removes pending chunks from all queues
                pipeline.clear_queues(from_stage=1)
                
                # Note: We do NOT cancel tasks anymore!
                # Tasks continue running and will automatically discard old chunks
                # based on turn_id. This ensures they can process new chunks.
                
                # Clear transport send queue to stop pending audio
                if hasattr(self.transport, 'clear_send_queue'):
                    self.transport.clear_send_queue(connection_id)
                
                # Send control events immediately (bypass queue)
                try:
                    from core.chunk import EventChunk, ChunkType
                    
                    # Send TTS stop event - IMMEDIATE delivery to stop client playback
                    stop_event = EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"reason": "interrupted"},
                        source_station="SessionManager",
                        session_id=connection_id
                    )
                    
                    # Send state transition to LISTENING
                    state_event = EventChunk(
                        type=ChunkType.EVENT_STATE_LISTENING,
                        event_data={"reason": "interrupted"},
                        source_station="SessionManager",
                        session_id=connection_id
                    )
                    
                    # Use send_immediate if available, otherwise fallback to output_chunk
                    if hasattr(self.transport, 'send_immediate'):
                        await self.transport.send_immediate(connection_id, stop_event)
                        await self.transport.send_immediate(connection_id, state_event)
                        self.logger.debug("Sent immediate interrupt events to client")
                    else:
                        await self.transport.output_chunk(connection_id, stop_event)
                        await self.transport.output_chunk(connection_id, state_event)
                        self.logger.debug("Sent interrupt events to client (via queue)")
                        
                except Exception as e:
                    self.logger.warning(f"Failed to send interrupt events: {e}")
                
                # Clear interrupt event flag
                control_bus.clear_interrupt_event()
                
                self.logger.info(
                    f"[{connection_id[:8]}] Interrupt handled, new turn_id={control_bus.get_current_turn_id()}"
                )
        
        except asyncio.CancelledError:
            self.logger.info(f"Interrupt handler cancelled for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error in interrupt handler for connection {connection_id[:8]}: {e}",
                exc_info=True
            )
    
    async def _run_pipeline(self, connection_id: str, pipeline: Pipeline) -> None:
        """
        Run pipeline for a connection.
        
        Flow:
        1. Get input stream from transport
        2. Wrap input stream to detect CONTROL_INTERRUPT chunks
        3. Run pipeline (yields output chunks)
        4. Send output chunks back to transport
        
        Args:
            connection_id: Connection ID to run pipeline for
            pipeline: Pipeline instance to run
        """
        self.logger.info(f"Starting pipeline for connection {connection_id[:8]}")
        
        try:
            # Get input stream from transport
            raw_input_stream = self.transport.input_stream(connection_id)
            
            # Get ControlBus for this connection
            control_bus = self._control_buses.get(connection_id)
            
            # Wrap input stream to detect CONTROL_INTERRUPT and send to ControlBus
            input_stream = self._wrap_input_stream(raw_input_stream, control_bus, connection_id)
            
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
    
    async def _wrap_input_stream(
        self,
        input_stream: AsyncIterator[Chunk],
        control_bus: Optional[ControlBus],
        connection_id: str
    ) -> AsyncIterator[Chunk]:
        """
        Wrap input stream to detect CONTROL_INTERRUPT chunks and send to ControlBus.
        
        This allows client-initiated interrupts (abort) to trigger the interrupt mechanism.
        
        Args:
            input_stream: Raw input stream from transport
            control_bus: ControlBus instance for this connection
            connection_id: Connection identifier
            
        Yields:
            Chunks from input stream (CONTROL_INTERRUPT chunks still passed through)
        """
        async for chunk in input_stream:
            # Detect CONTROL_INTERRUPT chunk and send to ControlBus
            if chunk.type == ChunkType.CONTROL_INTERRUPT and control_bus:
                self.logger.info(
                    f"[{connection_id[:8]}] Client interrupt detected, sending to ControlBus"
                )
                
                # Send interrupt signal to ControlBus
                await control_bus.send_interrupt(
                    source="Transport",
                    reason=chunk.params.get("reason", "client_interrupt"),
                    metadata={
                        "connection_id": connection_id,
                        "command": chunk.command
                    }
                )
            
            # Always yield the chunk (so pipeline can process it too)
            yield chunk
    
    def _cleanup_session(self, connection_id: str) -> None:
        """
        Cleanup session resources.
        
        Args:
            connection_id: Connection to cleanup
        """
        self.logger.debug(f"Cleaning up session for connection {connection_id[:8]}")
        
        # Cancel interrupt handler
        if connection_id in self._interrupt_tasks:
            task = self._interrupt_tasks[connection_id]
            if not task.done():
                task.cancel()
            del self._interrupt_tasks[connection_id]
        
        # Cleanup pipeline resources (including provider cleanup)
        if connection_id in self._pipelines:
            pipeline = self._pipelines[connection_id]
            # Schedule async cleanup in background
            asyncio.create_task(
                self._cleanup_pipeline(connection_id, pipeline),
                name=f"cleanup-pipeline-{connection_id[:8]}"
            )
            del self._pipelines[connection_id]
        
        # Cleanup session resources
        if connection_id in self._sessions:
            del self._sessions[connection_id]
        
        if connection_id in self._control_buses:
            del self._control_buses[connection_id]
    
    async def _cleanup_pipeline(self, connection_id: str, pipeline: Pipeline) -> None:
        """
        Async cleanup for pipeline resources.
        
        Args:
            connection_id: Connection ID (for logging)
            pipeline: Pipeline to cleanup
        """
        try:
            await pipeline.cleanup()
            self.logger.debug(f"Pipeline cleaned up for connection {connection_id[:8]}")
        except Exception as e:
            self.logger.error(
                f"Error cleaning up pipeline for connection {connection_id[:8]}: {e}",
                exc_info=True
            )
    
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
    
    async def cancel_session(self, connection_id: str) -> None:
        """
        Cancel a specific session (e.g., when WebSocket disconnects).
        
        Args:
            connection_id: Connection to cancel
        """
        self.logger.info(f"Cancelling session for connection {connection_id[:8]}")
        
        # Cancel pipeline task
        if connection_id in self._sessions:
            task = self._sessions[connection_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    self.logger.error(f"Error waiting for pipeline cancellation: {e}")
        
        # Cancel interrupt handler
        if connection_id in self._interrupt_tasks:
            task = self._interrupt_tasks[connection_id]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        self.logger.info(f"Session cancelled for connection {connection_id[:8]}")
