"""
Transport Base - Transport layer framework

Provides common capabilities:
- read_worker / send_worker management
- Turn management (auto increment after TTS_STOP)
- Latency tracking (first_audio_sent)
- Output control support (FlowController / PlayoutTracker)

Concrete protocols only need to implement _do_read / _do_write and other abstract methods.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Any, Dict, Union, Callable, Awaitable, TYPE_CHECKING

from loguru import logger

from vixio.core.output_controller import (
    FlowControllerBase, 
    PlayoutTrackerBase, 
    PlayoutResult
)

if TYPE_CHECKING:
    from vixio.core.protocol import ProtocolBase
    from vixio.core.control_bus import ControlBus
    from vixio.core.station import Station


# Type alias for connection handler
ConnectionHandler = Callable[[str], Awaitable[None]]


class TransportBase(ABC):
    """
    Transport base class.
    
    Framework layer implements common capabilities, concrete protocols only need
    to implement _do_read / _do_write and other abstract methods.
    
    Design principles:
    1. DAG and Transport decoupled through queues
    2. Common capabilities (Turn management, Latency tracking) unified by framework
    3. Protocol implementers only care about connection read/write and protocol encoding
    """
    
    def __init__(self, name: str = "Transport"):
        self._name = name
        self.logger = logger.bind(component=name)
        
        # ============ Queue management (framework maintained) ============
        self._read_queues: Dict[str, asyncio.Queue] = {}   # session_id -> read queue
        self._send_queues: Dict[str, asyncio.Queue] = {}   # session_id -> send queue (audio, etc.)
        self._priority_queues: Dict[str, asyncio.Queue] = {}  # session_id -> priority queue (control messages)
        self._video_queues: Dict[str, asyncio.Queue] = {}  # session_id -> video queue (vision support)
        
        # ============ Worker tasks (framework maintained) ============
        self._read_workers: Dict[str, asyncio.Task] = {}   # session_id -> read_worker task
        self._send_workers: Dict[str, asyncio.Task] = {}   # session_id -> send_worker task
        
        # ============ Output controllers (provided by protocol) ============
        self._output_controllers: Dict[str, Any] = {}      # session_id -> FlowController/PlayoutTracker
        
        # ============ ControlBus references (injected by SessionManager) ============
        self._control_buses: Dict[str, 'ControlBus'] = {}
        
        # ============ Latency tracking ============
        self._latency_monitor: Optional[Any] = None
        self._first_audio_sent_recorded: Dict[str, set] = {}  # session_id -> set of turn_ids
        
        # ============ Protocol (provided by protocol) ============
        self._protocol: Optional['ProtocolBase'] = None
        
        # ============ Connection callbacks ============
        self._connection_handler: Optional[ConnectionHandler] = None
        self._disconnect_handler: Optional[ConnectionHandler] = None
        
        # ============ Running state ============
        self._running = False
    
    # ============ Lifecycle management ============
    
    @abstractmethod
    async def start(self) -> None:
        """
        Start Transport service (e.g., WebSocket server).
        
        Protocol implementation: Start underlying server, listen for connections, etc.
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """
        Stop Transport service.
        
        Protocol implementation: Close all connections, stop underlying server.
        """
        pass
    
    # ============ Interfaces exposed to DAG ============
    
    def get_read_queue(self, session_id: str) -> asyncio.Queue:
        """
        Get read queue (for InputStation use).
        
        Framework implementation: Internal read_worker puts _do_read() data into this queue.
        
        Args:
            session_id: Session ID
            
        Returns:
            asyncio.Queue: Queue containing raw data (bytes/str)
        """
        if session_id not in self._read_queues:
            self._read_queues[session_id] = asyncio.Queue()
        return self._read_queues[session_id]
    
    def get_send_queue(self, session_id: str) -> asyncio.Queue:
        """
        Get send queue for normal data (audio, etc.).
        
        Framework implementation: OutputStation puts data into this queue,
        internal send_worker takes it and calls _do_write() to send.
        
        Args:
            session_id: Session ID
            
        Returns:
            asyncio.Queue: Queue for OutputStation to write raw data
        """
        if session_id not in self._send_queues:
            self._send_queues[session_id] = asyncio.Queue()
        return self._send_queues[session_id]
    
    def get_priority_queue(self, session_id: str) -> asyncio.Queue:
        """
        Get priority queue for control messages (TTS_STOP, STATE changes, etc.).
        
        Control messages are sent immediately, not blocked by audio data.
        
        Args:
            session_id: Session ID
            
        Returns:
            asyncio.Queue: High priority queue for control messages
        """
        if session_id not in self._priority_queues:
            self._priority_queues[session_id] = asyncio.Queue()
        return self._priority_queues[session_id]
    
    def get_video_queue(self, session_id: str) -> asyncio.Queue:
        """
        Get video queue for vision frames (vision support).
        
        Video frames from device are stored here for InputStation to inject
        as visual context into audio chunks.
        
        Args:
            session_id: Session ID
            
        Returns:
            asyncio.Queue: Video frame queue (limited size to drop old frames)
        """
        if session_id not in self._video_queues:
            # Limited size queue - drop old frames if full
            self._video_queues[session_id] = asyncio.Queue(maxsize=10)
        return self._video_queues[session_id]
    
    def get_protocol(self) -> 'ProtocolBase':
        """
        Get protocol handler (for InputStation/OutputStation use).
        
        Returns:
            ProtocolBase: Protocol instance
        """
        return self._protocol
    
    def get_audio_codec(self, session_id: str) -> Optional[Any]:
        """
        Get audio codec (for InputStation/OutputStation use).
        
        Args:
            session_id: Session ID
            
        Returns:
            AudioCodec: Codec instance, or None (if protocol doesn't need it)
        """
        return self._create_audio_codec(session_id)
    
    def get_input_station(self, session_id: str) -> 'Station':
        """
        Get InputStation (for SessionManager use).
        
        Framework implementation: Creates InputStation bound to read_queue and video_queue.
        
        Args:
            session_id: Session ID
            
        Returns:
            InputStation instance
        """
        from vixio.stations.transport_stations import InputStation
        
        read_queue = self.get_read_queue(session_id)
        video_queue = self.get_video_queue(session_id)
        protocol = self.get_protocol()
        audio_codec = self.get_audio_codec(session_id)
        
        return InputStation(
            session_id=session_id,
            read_queue=read_queue,
            protocol=protocol,
            audio_codec=audio_codec,
            video_queue=video_queue,
            name=f"InputStation-{session_id[:8]}"
        )
    
    def get_output_station(self, session_id: str) -> 'Station':
        """
        Get OutputStation (for SessionManager use).
        
        Framework implementation: Creates OutputStation bound to send_queue and priority_queue.
        
        Args:
            session_id: Session ID
            
        Returns:
            OutputStation instance
        """
        from vixio.stations.transport_stations import OutputStation
        
        send_queue = self.get_send_queue(session_id)
        priority_queue = self.get_priority_queue(session_id)
        protocol = self.get_protocol()
        audio_codec = self.get_audio_codec(session_id)
        
        return OutputStation(
            session_id=session_id,
            send_queue=send_queue,
            priority_queue=priority_queue,
            protocol=protocol,
            audio_codec=audio_codec,
            name=f"OutputStation-{session_id[:8]}"
        )
    
    # ============ Factory methods for SessionContext ============
    
    def create_input_station(
        self,
        session_id: str,
        read_queue: 'asyncio.Queue',
        video_queue: Optional['asyncio.Queue'] = None,
    ) -> 'Station':
        """
        Factory method: Create InputStation with external queues.
        
        Used by SessionContext to create stations with queues owned by the context.
        
        Args:
            session_id: Session ID
            read_queue: Queue for incoming data (owned by SessionContext)
            video_queue: Queue for video frames (owned by SessionContext)
            
        Returns:
            InputStation instance
        """
        from vixio.stations.transport_stations import InputStation
        
        protocol = self.get_protocol()
        audio_codec = self.get_audio_codec(session_id)
        
        return InputStation(
            session_id=session_id,
            read_queue=read_queue,
            protocol=protocol,
            audio_codec=audio_codec,
            video_queue=video_queue,
            name=f"InputStation-{session_id[:8]}"
        )
    
    def create_output_station(
        self,
        session_id: str,
        send_queue: 'asyncio.Queue',
        priority_queue: 'asyncio.Queue',
    ) -> 'Station':
        """
        Factory method: Create OutputStation with external queues.
        
        Used by SessionContext to create stations with queues owned by the context.
        
        Args:
            session_id: Session ID
            send_queue: Queue for outgoing data (owned by SessionContext)
            priority_queue: Queue for priority messages (owned by SessionContext)
            
        Returns:
            OutputStation instance
        """
        from vixio.stations.transport_stations import OutputStation
        
        protocol = self.get_protocol()
        audio_codec = self.get_audio_codec(session_id)
        
        return OutputStation(
            session_id=session_id,
            send_queue=send_queue,
            priority_queue=priority_queue,
            protocol=protocol,
            audio_codec=audio_codec,
            name=f"OutputStation-{session_id[:8]}"
        )
    
    # ============ ControlBus integration ============
    
    def set_control_bus(self, session_id: str, control_bus: 'ControlBus') -> None:
        """
        Set ControlBus (called by SessionManager).
        
        Framework uses for:
        - Turn management: Calls control_bus.increment_turn() after TTS_STOP
        
        Args:
            session_id: Session ID
            control_bus: ControlBus instance
        """
        self._control_buses[session_id] = control_bus
        self.logger.debug(f"ControlBus set for session {session_id[:8]}")
    
    def get_control_bus(self, session_id: str) -> Optional['ControlBus']:
        """Get ControlBus for session"""
        return self._control_buses.get(session_id)
    
    # ============ Latency tracking integration ============
    
    def set_latency_monitor(self, latency_monitor: Any) -> None:
        """
        Set Latency Monitor (called by SessionManager).
        
        Framework uses for:
        - Recording first_audio_sent timestamp
        - Auto-generating latency report
        
        Args:
            latency_monitor: LatencyMonitor instance
        """
        self._latency_monitor = latency_monitor
    
    # ============ Connection callbacks ============
    
    def on_new_connection(self, handler: ConnectionHandler) -> None:
        """
        Register new connection callback.
        
        Args:
            handler: async def handler(session_id: str) -> None
        """
        self._connection_handler = handler
    
    def on_disconnect(self, handler: ConnectionHandler) -> None:
        """
        Register disconnect callback.
        
        Args:
            handler: async def handler(session_id: str) -> None
        """
        self._disconnect_handler = handler
    
    async def on_pipeline_ready(self, session_id: str) -> None:
        """
        DAG ready callback (send handshake message, etc.).
        
        Framework implementation: Calls protocol.handshake() and sends.
        Protocol can override this method to add custom logic.
        
        Args:
            session_id: Session ID
        """
        protocol = self.get_protocol()
        handshake_msg = protocol.handshake(session_id)
        
        if handshake_msg:
            encoded = protocol.encode_message(handshake_msg)
            await self._do_write(session_id, encoded)
            self.logger.info(f"Handshake sent for session {session_id[:8]}")
    
    # ============ Worker management (framework implementation) ============
    
    async def start_workers(self, session_id: str) -> None:
        """
        Start read_worker and send_worker for session.
        
        Automatically called by framework, before InputStation starts.
        
        Args:
            session_id: Session ID
        """
        # Ensure queues are created
        self.get_read_queue(session_id)
        self.get_send_queue(session_id)
        self.get_priority_queue(session_id)
        
        # Create output controller
        self._output_controllers[session_id] = self._create_output_controller(session_id)
        
        # Initialize latency tracking
        self._first_audio_sent_recorded[session_id] = set()
        
        # Start workers
        self._read_workers[session_id] = asyncio.create_task(
            self._read_worker(session_id)
        )
        self._send_workers[session_id] = asyncio.create_task(
            self._send_worker(session_id)
        )
        
        # Call session start hook
        await self._on_session_start(session_id)
        
        self.logger.info(f"Workers started for session {session_id[:8]}")
    
    async def stop_workers(self, session_id: str) -> None:
        """
        Stop workers and cleanup resources for session.
        
        This method is idempotent - safe to call multiple times.
        
        Args:
            session_id: Session ID
        """
        # Check if already cleaned up (idempotent)
        if session_id not in self._read_queues and session_id not in self._send_queues:
            self.logger.debug(f"Session {session_id[:8]} already cleaned up, skipping")
            return
        
        # Call session end hook
        await self._on_session_end(session_id)
        
        # Stop read_worker
        read_worker = self._read_workers.pop(session_id, None)
        if read_worker:
            read_worker.cancel()
            try:
                await read_worker
            except asyncio.CancelledError:
                pass
        
        # Stop send_worker (send None signal)
        send_queue = self._send_queues.get(session_id)
        if send_queue:
            await send_queue.put(None)
        
        send_worker = self._send_workers.pop(session_id, None)
        if send_worker:
            try:
                await asyncio.wait_for(send_worker, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                send_worker.cancel()
        
        # Cleanup resources
        self._read_queues.pop(session_id, None)
        self._send_queues.pop(session_id, None)
        self._priority_queues.pop(session_id, None)
        self._video_queues.pop(session_id, None)
        self._output_controllers.pop(session_id, None)
        self._control_buses.pop(session_id, None)
        self._first_audio_sent_recorded.pop(session_id, None)
        
        self.logger.info(f"Workers stopped for session {session_id[:8]}")
    
    async def _read_worker(self, session_id: str) -> None:
        """
        Read Worker (framework implementation).
        
        Responsibilities:
        1. Call _do_read() to read from connection
        2. Call _on_message_received() hook for special message handling
        3. Put unhandled messages into read_queue for DAG
        
        Subclasses should NOT override this method. Instead, override
        _on_message_received() to handle protocol-specific messages.
        """
        read_queue = self._read_queues[session_id]
        
        try:
            while True:
                # Read from connection
                data = await self._do_read(session_id)
                
                if data is None:
                    # Connection closed
                    break
                
                # Hook: let subclass handle special messages (e.g., MCP, control)
                # Returns True if message was handled and should NOT go to DAG
                if await self._on_message_received(session_id, data):
                    continue

                # Normal messages go to DAG
                await read_queue.put(data)
        
        except asyncio.CancelledError:
            self.logger.debug(f"Read worker cancelled for session {session_id[:8]}")
        except ConnectionError as e:
            self.logger.debug(f"Connection closed for session {session_id[:8]}: {e}")
        except Exception as e:
            self.logger.error(f"Error in read worker for session {session_id[:8]}: {e}")
        
        finally:
            # Put None to signal stream end
            await read_queue.put(None)
    
    async def _on_message_received(self, session_id: str, data: Union[bytes, str]) -> bool:
        """
        Hook for handling special messages before they enter DAG.

        Override in subclass to handle protocol-specific messages that:
        - Need special routing (e.g., MCP messages to DeviceToolClient)
        - Should bypass the DAG entirely
        - Require side effects before DAG processing

        Args:
            session_id: Session ID
            data: Raw message data (bytes for audio, str for JSON)
            
        Returns:
            True if message was fully handled (skip DAG)
            False if message should continue to DAG

        Example:
            async def _on_message_received(self, session_id, data):
                if isinstance(data, str):
                    msg = json.loads(data)
                    if msg.get("type") == "mcp":
                        self._route_mcp_message(session_id, msg)
                        return True  # Handled, skip DAG
                return False  # Pass to DAG
        """
        return False  # Default: pass all messages to DAG
    
    async def _send_worker(self, session_id: str) -> None:
        """
        Send Worker (framework implementation).
        
        Responsibilities:
        1. Get data from queues (priority_queue first, then send_queue)
        2. Output control (mutually exclusive strategies)
        3. Send data
        4. Latency tracking (first_audio_sent) - after actual network send
        5. Turn management (TTS_STOP detection)
        
        Dual queue design:
        - priority_queue: Control messages (STATE changes, interrupt TTS_STOP) - just data
        - send_queue: Normal data (audio, TTS events) - (data, metadata) tuple
        
        Latency tracking:
        - FlowController: record after first audio frame sent
        - PlayoutTracker: record after first playout_finished callback
        """
        send_queue = self._send_queues[session_id]
        priority_queue = self._priority_queues[session_id]
        output_controller = self._output_controllers.get(session_id)
        control_bus = self._control_buses.get(session_id)
        
        # Determine output control strategy
        is_flow_control = isinstance(output_controller, FlowControllerBase)
        is_playout_track = isinstance(output_controller, PlayoutTrackerBase)
        
        # Latency tracking state
        recorded_turns = self._first_audio_sent_recorded.get(session_id, set())
        
        try:
            while True:
                # 1. Get data from queues - priority first
                data = None
                metadata = None
                is_priority = False
                got_data = False
                
                # Check priority queue first (non-blocking) - just data, no metadata
                try:
                    data = priority_queue.get_nowait()
                    is_priority = True
                    got_data = True
                except asyncio.QueueEmpty:
                    pass
                
                # If no priority data, wait on send_queue with periodic priority check
                # send_queue contains (data, metadata) tuples
                if not got_data:
                    try:
                        queue_item = await asyncio.wait_for(send_queue.get(), timeout=0.1)
                        got_data = True
                        
                        # Unpack (data, metadata) tuple
                        if queue_item is not None and isinstance(queue_item, tuple):
                            data, metadata = queue_item
                        else:
                            data = queue_item  # None or legacy format
                    except asyncio.TimeoutError:
                        # Timeout - check priority queue again
                        continue
                
                # None from queue means shutdown signal
                if got_data and data is None:
                    break
                
                # Extract info from metadata
                is_audio = metadata and metadata.get("type") == "audio"
                is_first_audio_frame = metadata and metadata.get("is_first_frame", False)
                turn_id = metadata.get("turn_id") if metadata else None
                is_tts_stop = metadata and metadata.get("is_tts_stop", False)

                # 2. Output control (Strategy A: Upper layer active control)
                # Only apply to audio data, not priority messages
                if not is_priority and is_flow_control and is_audio:
                    await output_controller.wait_for_next()
                
                # 2.5. Special handling for TTS_STOP: delay to allow client buffer playback
                # Client doesn't wait for buffer completion, so delay before sending TTS_STOP
                if is_tts_stop:
                    delay_ms = 150
                    await asyncio.sleep(delay_ms / 1000.0)
                
                # 3. Send data
                try:
                    await self._do_write(session_id, data)
                except ConnectionError as e:
                    # Connection closed - expected when client disconnects
                    self.logger.debug(f"Connection closed, stopping send worker: {e}")
                    break
                except Exception as e:
                    # Check if session is still active
                    if session_id not in self._send_queues:
                        self.logger.debug(f"Session {session_id[:8]} disconnected, stopping send worker")
                        break
                    self.logger.warning(f"Failed to send data: {e}")
                    continue
                
                # 4. Latency tracking: record first_audio_sent AFTER network send
                if is_first_audio_frame and turn_id is not None:
                    if turn_id not in recorded_turns:
                        # For FlowController: record immediately after send
                        if is_flow_control and self._latency_monitor:
                            self._latency_monitor.record(session_id, turn_id, "first_audio_sent")
                            recorded_turns.add(turn_id)
                            self._first_audio_sent_recorded[session_id] = recorded_turns
                            self.logger.debug(f"Recorded first_audio_sent for turn {turn_id}")
                            
                            # Generate latency report
                            self._latency_monitor.log_report(session_id, turn_id)
                            self._latency_monitor.print_summary(session_id, turn_id)
                        
                        # For PlayoutTracker: wait for playout_finished before recording
                        elif is_playout_track and self._latency_monitor:
                            result = await output_controller.wait_for_playout()
                            self._latency_monitor.record(session_id, turn_id, "first_audio_sent")
                            recorded_turns.add(turn_id)
                            self._first_audio_sent_recorded[session_id] = recorded_turns
                            self.logger.debug(f"Recorded first_audio_sent for turn {turn_id} (after playout)")
                            
                            # Generate latency report
                            self._latency_monitor.log_report(session_id, turn_id)
                            self._latency_monitor.print_summary(session_id, turn_id)
                
                # 5. Turn management: detect TTS_STOP, increment_turn
                # Check both metadata and message content
                should_increment_turn = is_tts_stop or (is_priority and self._is_tts_stop_message(data))
                
                if should_increment_turn:
                    # Strategy B: wait for playout to complete (if not already waited in step 4)
                    if is_playout_track and not is_first_audio_frame:
                        result = await output_controller.wait_for_playout()
                        self.logger.debug(f"Playout finished: position={result.playback_position:.2f}s, interrupted={result.interrupted}")
                    
                    if control_bus:
                        await control_bus.increment_turn(
                            source="Transport",
                            reason="bot_finished"
                        )
                        self.logger.info(f"Turn incremented after bot finished (session={session_id[:8]})")
                    
                    # Reset flow controller state for next turn
                    if is_flow_control:
                        output_controller.reset()
                    elif is_playout_track:
                        output_controller.flush()
        
        except asyncio.CancelledError:
            self.logger.debug(f"Send worker cancelled for session {session_id[:8]}")
        except Exception as e:
            self.logger.error(f"Error in send worker for session {session_id[:8]}: {e}")
    
    # ============ Abstract methods protocol must implement ============
    
    @abstractmethod
    async def _do_read(self, session_id: str) -> Optional[Union[bytes, str]]:
        """
        Read one message from physical connection.
        
        Protocol implementation: Read raw data from WebSocket/TCP/etc.
        
        Args:
            session_id: Session ID
            
        Returns:
            raw data (bytes or str), None when connection closed
            
        Raises:
            ConnectionError: Connection exception
        """
        pass
    
    @abstractmethod
    async def _do_write(self, session_id: str, data: Union[bytes, str]) -> None:
        """
        Write one message to physical connection.
        
        Protocol implementation: Write raw data to WebSocket/TCP/etc.
        
        Args:
            session_id: Session ID
            data: raw data to send
            
        Raises:
            ConnectionError: Connection exception
        """
        pass
    
    @abstractmethod
    def _create_protocol(self) -> 'ProtocolBase':
        """
        Create protocol handler.
        
        Protocol implementation: Return concrete protocol's Protocol instance.
        
        Returns:
            ProtocolBase: Protocol instance (e.g., XiaozhiProtocol)
        """
        pass
    
    @abstractmethod
    def _create_audio_codec(self, session_id: str) -> Optional[Any]:
        """
        Create audio codec.
        
        Protocol implementation: Return audio codec needed by the protocol.
        Return None if protocol doesn't need audio encoding.
        
        Args:
            session_id: Session ID
            
        Returns:
            AudioCodec instance, or None
        """
        pass
    
    # ============ Optional methods protocol can implement ============
    
    def _create_output_controller(self, session_id: str) -> Optional[Any]:
        """
        Create output controller (mutually exclusive strategies).
        
        Protocol implementation: Choose based on underlying capabilities:
        - FlowControllerBase: Upper layer active control (e.g., Xiaozhi 60ms audio frames)
        - PlayoutTrackerBase: Lower layer control (e.g., WebRTC)
        - None: No output control needed
        
        Strategies are mutually exclusive, framework adapts behavior based on return type.
        
        Args:
            session_id: Session ID
            
        Returns:
            FlowControllerBase | PlayoutTrackerBase | None
        """
        return None  # Default: no output control
    
    async def _on_session_start(self, session_id: str) -> None:
        """
        Session start hook (optional).
        
        Protocol can override: Execute custom logic when session starts.
        
        Args:
            session_id: Session ID
        """
        pass  # Default empty implementation
    
    async def _on_session_end(self, session_id: str) -> None:
        """
        Session end hook (optional).
        
        Protocol can override: Execute cleanup logic when session ends.
        
        Args:
            session_id: Session ID
        """
        pass  # Default empty implementation
    
    def _is_audio_message(self, data: Union[bytes, str]) -> bool:
        """
        Check if message is audio.
        
        Framework default: Try to parse with protocol and check.
        Protocol can override for more efficient checking.
        
        Args:
            data: raw data
            
        Returns:
            True if audio message
        """
        # Default: bytes type is audio
        if isinstance(data, bytes):
            return True
        
        # JSON message: try to parse
        try:
            protocol = self.get_protocol()
            message = protocol.decode_message(data)
            msg_type = message.get("type", "")
            return msg_type in ["tts", "audio", "opus"]
        except Exception:
            return False
    
    def _is_tts_stop_message(self, data: Union[bytes, str]) -> bool:
        """
        Check if message is TTS_STOP.
        
        Framework default: Try to parse with protocol and check.
        Protocol can override for more efficient checking.
        
        Used for Turn management: send_worker auto increment_turn after detecting TTS_STOP.
        
        Args:
            data: raw data to send
            
        Returns:
            True if TTS_STOP message
        """
        if isinstance(data, bytes):
            return False
        
        try:
            protocol = self.get_protocol()
            message = protocol.decode_message(data)
            msg_type = message.get("type", "")
            
            # Check if TTS stop message
            if msg_type == "tts":
                state = message.get("state", "")
                return state == "stop"
            
            return False
        except Exception:
            return False
