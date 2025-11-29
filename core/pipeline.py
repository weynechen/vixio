"""
Pipeline - assembly line that connects workstations (stations)

Design (Async Model):
- Each station runs in an independent asyncio task
- Stations connected via asyncio.Queue for non-blocking parallel processing
- ControlBus integration for interrupt handling
- Can clear queues and cancel tasks on interrupt
"""

import asyncio
from typing import List, Optional, AsyncIterator, Set
from core.station import Station
from core.chunk import Chunk
from core.control_bus import ControlBus
from loguru import logger


class Pipeline:
    """
    Pipeline - assembly line that connects workstations (stations).
    
    Async Design:
    - Each station runs in an independent asyncio task
    - Stations connected via asyncio.Queue for non-blocking parallel processing
    - ControlBus integration for interrupt handling
    - Can clear queues and cancel tasks on interrupt
    """
    
    def __init__(
        self,
        stations: List[Station],
        control_bus: Optional[ControlBus] = None,
        name: Optional[str] = None,
        queue_size: int = 100,
        session_id: Optional[str] = None
    ):
        """
        Initialize pipeline.
        
        Args:
            stations: List of stations to chain together
            control_bus: ControlBus for interrupt handling (optional)
            name: Pipeline name for logging
            queue_size: Maximum size for inter-station queues
            session_id: Session ID for logging (optional, can be set later)
        """
        self.stations = stations
        self._control_bus = control_bus
        self._session_id = session_id
        self.name = name or "Pipeline"
        self.queue_size = queue_size
        self.logger = logger.bind(pipeline=self.name)
        
        # Runtime state
        self.queues: List[asyncio.Queue] = []
        self.tasks: List[asyncio.Task] = []
        self.cancellable_task_indices: Set[int] = set()  # Indices of slow tasks (Agent, TTS)
        self._running = False
        
        if not stations:
            self.logger.warning(f"[{self.name}] Created empty pipeline")
        else:
            station_names = [s.name for s in stations]
            self.logger.info(f"[{self.name}] Created with {len(stations)} stations: {' -> '.join(station_names)}")
            
            # Pass ControlBus to all stations (if provided)
            if self._control_bus:
                self._propagate_control_bus()
            
            # Pass session_id to all stations (if provided)
            if self._session_id:
                self._propagate_session_id()
    
    @property
    def control_bus(self) -> Optional[ControlBus]:
        """Get control bus."""
        return self._control_bus
    
    @control_bus.setter
    def control_bus(self, value: Optional[ControlBus]) -> None:
        """
        Set control bus and propagate to all stations.
        
        This ensures stations always have access to control_bus even if set after pipeline creation.
        """
        self._control_bus = value
        if value:
            self._propagate_control_bus()
    
    @property
    def session_id(self) -> Optional[str]:
        """Get session ID."""
        return self._session_id
    
    @session_id.setter
    def session_id(self, value: Optional[str]) -> None:
        """
        Set session ID and propagate to all stations.
        
        This automatically updates logger bindings for all stations in the pipeline.
        """
        self._session_id = value
        if value:
            self._propagate_session_id()
            # Also update pipeline's own logger
            session_id_short = value[:8] if len(value) > 8 else value
            self.logger = logger.bind(pipeline=self.name, session_id=session_id_short)
    
    def _propagate_control_bus(self) -> None:
        """Propagate control_bus to all stations."""
        if self._control_bus:
            for station in self.stations:
                station.control_bus = self._control_bus
            self.logger.info(f"[{self.name}] ControlBus propagated to {len(self.stations)} stations")
    
    def _propagate_session_id(self) -> None:
        """Propagate session_id to all stations."""
        if self._session_id:
            for station in self.stations:
                station.set_session_id(self._session_id)
            self.logger.debug(f"[{self.name}] Session ID propagated to {len(self.stations)} stations")
    
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
        Run the pipeline - all stations in parallel with queues.
        
        Async Flow:
        1. Create queues between stations
        2. Start each station as independent task
        3. Feed input_stream into first queue
        4. Yield from final queue
        
        Args:
            input_stream: Source of chunks (usually from Transport)
            
        Yields:
            Processed chunks from final station
        """
        if not self.stations:
            self.logger.warning(f"[{self.name}] Empty pipeline, no processing")
            async for chunk in input_stream:
                yield chunk
            return
        
        self._running = True
        
        try:
            # Create queues: n stations need n+1 queues
            # Queue[0] gets input, Queue[n] provides output
            self.queues = [asyncio.Queue(maxsize=self.queue_size) for _ in range(len(self.stations) + 1)]
            
            # Start all station tasks
            self.tasks = []
            for i, station in enumerate(self.stations):
                task = asyncio.create_task(
                    self._run_station(station, i, self.queues[i], self.queues[i + 1]),
                    name=f"{self.name}-{station.name}"
                )
                self.tasks.append(task)
                
                # Mark slow tasks as cancellable
                if self._is_slow_station(station):
                    self.cancellable_task_indices.add(i)
            
            # Start input feeder task
            input_task = asyncio.create_task(
                self._feed_input(input_stream, self.queues[0]),
                name=f"{self.name}-input-feeder"
            )
            
            # Yield from output queue
            chunk_count = 0
            try:
                while self._running:
                    try:
                        # get chunk from output queue
                        chunk = await asyncio.wait_for(self.queues[-1].get(), timeout=0.1)
                        
                        # None is used as end-of-stream marker, stop when received
                        if chunk is None:
                            break
                        
                        chunk_count += 1
                        self.logger.debug(f"[{self.name}] Output chunk #{chunk_count}: {chunk}")
                        yield chunk
                    except asyncio.TimeoutError:
                        # Check if input task is done and queue is empty
                        if input_task.done() and self.queues[-1].empty():
                            # Check if all tasks are done
                            all_done = all(task.done() for task in self.tasks)
                            if all_done:
                                break
                        continue
            except Exception as e:
                self.logger.error(f"[{self.name}] Error in output loop: {e}", exc_info=True)
                raise
            
            self.logger.debug(f"[{self.name}] Completed processing {chunk_count} chunks")
        
        finally:
            # Cleanup
            self._running = False
            await self._cleanup_tasks()
    
    async def _feed_input(self, input_stream: AsyncIterator[Chunk], input_queue: asyncio.Queue) -> None:
        """
        Feed input stream into first queue.
        
        Args:
            input_stream: Source of chunks
            input_queue: Queue to feed into
        """
        try:
            async for chunk in input_stream:
                await input_queue.put(chunk)
                self.logger.debug(f"[{self.name}] Fed input chunk: {chunk}")
        except RuntimeError as e:
            # Handle normal disconnection from transport
            if "disconnect" in str(e).lower() or "receive" in str(e).lower():
                self.logger.debug(f"[{self.name}] Input stream closed: {e}")
            else:
                self.logger.error(f"[{self.name}] Runtime error feeding input: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self.name}] Error feeding input: {e}", exc_info=True)
        finally:
            # Signal end of input
            await input_queue.put(None)
            self.logger.debug(f"[{self.name}] Input feeding complete")
    
    async def _run_station(
        self,
        station: Station,
        index: int,
        input_queue: asyncio.Queue,
        output_queue: asyncio.Queue
    ) -> None:
        """
        Run a single station task.
        
        Args:
            station: Station to run
            index: Station index in pipeline
            input_queue: Queue to read from
            output_queue: Queue to write to
        """
        self.logger.debug(f"[{self.name}] Starting station {station.name} (index {index})")
        
        try:
            async for output_chunk in station.process(self._queue_iterator(input_queue)):
                await output_queue.put(output_chunk)
            
            # Signal downstream that this station is done
            await output_queue.put(None)
            
        except asyncio.CancelledError:
            self.logger.info(f"[{self.name}] Station {station.name} cancelled")
            raise
        except Exception as e:
            self.logger.error(f"[{self.name}] Error in station {station.name}: {e}", exc_info=True)
            # Still signal downstream
            await output_queue.put(None)
        finally:
            self.logger.debug(f"[{self.name}] Station {station.name} finished")
    
    async def _queue_iterator(self, queue: asyncio.Queue) -> AsyncIterator[Chunk]:
        """
        Iterate over queue until None is received.
        
        Args:
            queue: Queue to iterate over
            
        Yields:
            Chunks from queue
        """
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    
    def _is_slow_station(self, station: Station) -> bool:
        """
        Check if station is potentially slow (Agent, TTS).
        
        Args:
            station: Station to check
            
        Returns:
            True if station is slow
        """
        # Check station name or class name
        name_lower = station.name.lower()
        return 'agent' in name_lower or 'tts' in name_lower or 'llm' in name_lower
    
    async def _cleanup_tasks(self) -> None:
        """Cleanup all running tasks."""
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        self.tasks.clear()
        self.logger.debug(f"[{self.name}] All tasks cleaned up")
    
    def clear_queues(self, from_stage: int = 1) -> None:
        """
        Clear queues from specified stage onwards.
        
        This is used during interrupt handling to discard pending chunks.
        Stage 0 (input queue) is typically preserved.
        
        Args:
            from_stage: Stage index to start clearing from (default: 1)
        """
        if not self.queues:
            return
        
        cleared_count = 0
        for i in range(from_stage, len(self.queues)):
            queue = self.queues[i]
            while not queue.empty():
                try:
                    queue.get_nowait()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break
        
        self.logger.info(f"[{self.name}] Cleared {cleared_count} chunks from {len(self.queues) - from_stage} queues")
    
    def cancel_slow_tasks(self) -> None:
        """
        Cancel slow/long-running tasks (Agent, TTS).
        
        This is used during interrupt handling to quickly stop ongoing processing.
        """
        if not self.tasks:
            return
        
        cancelled_count = 0
        for i in self.cancellable_task_indices:
            if i < len(self.tasks):
                task = self.tasks[i]
                if not task.done():
                    task.cancel()
                    cancelled_count += 1
                    self.logger.info(f"[{self.name}] Cancelled task: {task.get_name()}")
        
        if cancelled_count > 0:
            self.logger.info(f"[{self.name}] Cancelled {cancelled_count} slow tasks")
    
    def add_station(self, station: Station, position: Optional[int] = None) -> None:
        """
        Add a station to the pipeline.
        
        Args:
            station: Station to add
            position: Position to insert (None = append to end)
        """
        if position is None:
            self.stations.append(station)
            self.logger.info(f"[{self.name}] Appended station: {station.name}")
        else:
            self.stations.insert(position, station)
            self.logger.info(f"[{self.name}] Inserted station at position {position}: {station.name}")
    
    def remove_station(self, station_name: str) -> bool:
        """
        Remove a station by name.
        
        Args:
            station_name: Name of station to remove
            
        Returns:
            True if station was found and removed
        """
        for i, station in enumerate(self.stations):
            if station.name == station_name:
                removed = self.stations.pop(i)
                self.logger.info(f"[{self.name}] Removed station: {removed.name}")
                return True
        self.logger.warning(f"[{self.name}] Station not found: {station_name}")
        return False
    
    def get_station(self, station_name: str) -> Optional[Station]:
        """
        Get a station by name.
        
        Args:
            station_name: Name of station to find
            
        Returns:
            Station if found, None otherwise
        """
        for station in self.stations:
            if station.name == station_name:
                return station
        return None
    
    async def cleanup(self) -> None:
        """
        Cleanup pipeline resources.
        
        Calls cleanup on all stations that have cleanup methods.
        This is important when stations hold stateful providers that need explicit cleanup.
        """
        self.logger.debug(f"[{self.name}] Cleaning up pipeline resources...")
        
        cleanup_count = 0
        for station in self.stations:
            # Check if station has cleanup method
            if hasattr(station, 'cleanup') and callable(getattr(station, 'cleanup')):
                try:
                    cleanup_method = getattr(station, 'cleanup')
                    # Support both sync and async cleanup
                    if asyncio.iscoroutinefunction(cleanup_method):
                        await cleanup_method()
                    else:
                        cleanup_method()
                    cleanup_count += 1
                    self.logger.debug(f"[{self.name}] Cleaned up station: {station.name}")
                except Exception as e:
                    self.logger.error(f"[{self.name}] Error cleaning up station {station.name}: {e}")
        
        if cleanup_count > 0:
            self.logger.info(f"[{self.name}] Cleaned up {cleanup_count} stations")
            
            # Force garbage collection to free memory immediately
            import gc
            gc.collect()
            self.logger.debug(f"[{self.name}] Garbage collection triggered")
    
    def __str__(self) -> str:
        station_names = [s.name for s in self.stations]
        return f"Pipeline({self.name}: {' -> '.join(station_names)})"
    
    def __repr__(self) -> str:
        return self.__str__()
