"""
Transport Stations - Bridge between DAG and Transport

InputStation: Read from read_queue -> Chunk stream (supports audio + optional video)
OutputStation: Chunk stream -> Write to send_queue

Audio Data Flow:
- InputStation outputs AUDIO_DELTA (streaming fragments from Transport)
- VADStation/TurnDetector outputs AUDIO (merged segments)
- OutputStation accepts both AUDIO_DELTA and AUDIO

Design principles:
- InputStation/OutputStation are pure format conversion layers
- NOT responsible for: connection management, flow control, turn management, latency tracking
- These common capabilities are unified by TransportBase's read_worker/send_worker

Vision Support:
- InputStation can optionally receive video frames via video_queue
- Visual context is injected into audio chunks via metadata["visual_context"]
- VAD-triggered frames take priority over heartbeat frames
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, Dict, Any, Union
from vixio.core.station import Station
from vixio.core.protocol import ProtocolBase
from vixio.core.chunk import Chunk, ChunkType
from vixio.providers.vision import ImageContent
from loguru import logger


class InputStation(Station):
    """
    Input format conversion Station - DAG starting point.

    Responsibilities (pure format conversion):
    1. Read raw data from Transport's read_queue
    2. Call Protocol to decode message
    3. Call Protocol to convert to Chunk
    4. Call AudioCodec to decode audio (if needed)
    5. Convert to AUDIO_DELTA (streaming fragments)
    6. Inject visual context into audio chunks (if video_queue provided)
    7. Output Chunk to DAG
    
    Audio Output:
    - Outputs AUDIO_DELTA (streaming audio fragments, ~0.06-0.12s)
    - These are small continuous chunks from Transport
    - VAD/TurnDetector will merge them into AUDIO
    
    Vision Support:
    - Optionally receives video frames via video_queue
    - VAD-triggered frames stored as speech_context (high priority)
    - Heartbeat frames stored as latest_frame (low priority)
    - Visual context injected into audio chunks via metadata["visual_context"]
    
    NOT responsible for:
    - Connection management (handled by Transport's read_worker)
    - Turn management (handled by TransportBase framework)
    - Latency tracking (handled by TransportBase framework)
    """
    
    def __init__(
        self,
        session_id: str,
        read_queue: asyncio.Queue,      # From Transport - audio/control messages
        protocol: ProtocolBase,          # From Transport
        audio_codec: Optional[Any] = None,  # From Transport, can be None
        video_queue: Optional[asyncio.Queue] = None,  # From Transport - video frames
        name: str = "InputStation"
    ):
        """
        Args:
            session_id: Session ID
            read_queue: Transport's read queue (audio/control)
            protocol: Protocol handler
            audio_codec: Audio codec (can be None)
            video_queue: Transport's video queue (optional, for vision support)
            name: Station name
        """
        super().__init__(name=name)
        self._session_id = session_id
        self._read_queue = read_queue
        self._video_queue = video_queue
        self.protocol = protocol
        self.audio_codec = audio_codec
        self._running = True
        
        # Vision context management
        self._speech_context_frame: Optional[ImageContent] = None  # VAD-triggered frame
        self._latest_frame: Optional[ImageContent] = None  # Heartbeat frame
        self._video_task: Optional[asyncio.Task] = None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        InputStation is starting point, doesn't process upstream Chunks.
        
        Actual data generation is in _generate_chunks().
        """
        # If receiving upstream Chunk (shouldn't happen), pass through
        yield chunk
    
    async def _collect_video_frames(self) -> None:
        """
        Background task: Collect video frames from video_queue.
        
        Frame types:
        - trigger="vad_start": Speech context frame (high priority)
        - trigger="heartbeat": Latest frame (low priority, backup)
        """
        self.logger.info(f"Starting video frame collector for session {self._session_id[:8]}")
        
        try:
            while self._running:
                try:
                    frame_data = await asyncio.wait_for(
                        self._video_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if frame_data is None:
                    break
                
                # Parse frame data to ImageContent
                image = self._parse_video_frame(frame_data)
                if image is None:
                    continue
                
                # Store based on trigger type
                if image.trigger == "vad_start":
                    self._speech_context_frame = image
                    self.logger.debug(f"Stored VAD-triggered frame")
                else:
                    self._latest_frame = image
                    self.logger.debug(f"Updated latest frame (heartbeat)")
        
        except asyncio.CancelledError:
            self.logger.debug("Video frame collector cancelled")
        except Exception as e:
            self.logger.error(f"Error in video frame collector: {e}")
        finally:
            self.logger.info(f"Video frame collector ended for session {self._session_id[:8]}")
    
    def _parse_video_frame(self, frame_data: Dict[str, Any]) -> Optional[ImageContent]:
        """
        Parse video frame data to ImageContent.
        
        Expected format:
        {
            "data": bytes or base64 string,
            "trigger": "vad_start" | "heartbeat",
            "timestamp": float,
            "width": int,
            "height": int,
            "format": "jpeg" | "png"
        }
        """
        try:
            return ImageContent(
                image=frame_data.get("data", b""),
                mime_type=f"image/{frame_data.get('format', 'jpeg')}",
                source="camera",
                trigger=frame_data.get("trigger", "heartbeat"),
                capture_time=frame_data.get("timestamp"),
                width=frame_data.get("width", 0),
                height=frame_data.get("height", 0)
            )
        except Exception as e:
            self.logger.error(f"Failed to parse video frame: {e}")
            return None
    
    def get_visual_context(self) -> Optional[ImageContent]:
        """
        Get current visual context.
        
        Priority:
        1. VAD-triggered speech context frame
        2. Latest heartbeat frame
        
        Returns:
            ImageContent or None
        """
        return self._speech_context_frame or self._latest_frame
    
    def clear_speech_context(self) -> None:
        """
        Clear speech context frame.
        
        Called after each turn to reset VAD-triggered frame.
        """
        self._speech_context_frame = None
    
    async def _generate_chunks(self) -> AsyncIterator[Chunk]:
        """
        Generate Chunk stream (true input source).
        
        Flow:
        1. Start video collector (if video_queue provided)
        2. Get raw data from read_queue
        3. protocol.decode_message() decode
        4. protocol.message_to_chunk() convert
        5. audio_codec.decode() decode audio (if needed)
        6. Inject visual context into audio chunks
        7. yield Chunk
        """
        self.logger.info(f"Starting input stream for session {self._session_id[:8]}")
        
        # Start video frame collector if video_queue is provided
        if self._video_queue:
            self._video_task = asyncio.create_task(self._collect_video_frames())
            self.logger.info("Video frame collector started")
        
        try:
            while self._running:
                # 1. Read raw data from queue
                try:
                    raw_data = await asyncio.wait_for(
                        self._read_queue.get(), 
                        timeout=30.0  # Prevent infinite wait
                    )
                except asyncio.TimeoutError:
                    # Timeout but connection may still be alive, continue waiting
                    continue
                
                if raw_data is None:
                    # End of stream signal
                    self.logger.debug(f"End of stream signal received")
                    break
                
                # 2. Protocol decode
                try:
                    message = self.protocol.decode_message(raw_data)
                except Exception as e:
                    self.logger.error(f"Failed to decode message: {e}")
                    continue
                
                # 3. Convert to Chunk
                turn_id = self._get_current_turn_id()
                chunk = self.protocol.message_to_chunk(message, self._session_id, turn_id)
                
                if chunk is None:
                    continue
                
                # 4. Audio decode (Opus -> PCM)
                if chunk.type in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO) and self.audio_codec and chunk.data:
                    try:
                        pcm_data = self.audio_codec.decode(chunk.data)
                        chunk.data = pcm_data
                    except Exception as e:
                        self.logger.error(f"Failed to decode audio: {e}")
                        continue
                
                
                # 5. Inject visual context into audio chunks
                if chunk.type in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO) and self._video_queue:
                    visual_ctx = self.get_visual_context()
                    if visual_ctx:
                        chunk.metadata["visual_context"] = visual_ctx
                
                # 6. Output Chunk
                self.logger.debug(f"Input chunk: {chunk.type.value} from {chunk.source}")
                yield chunk
        
        except asyncio.CancelledError:
            self.logger.debug(f"Input stream cancelled")
        except Exception as e:
            self.logger.error(f"Error in input stream: {e}", exc_info=True)
        finally:
            # Cancel video collector task
            if self._video_task and not self._video_task.done():
                self._video_task.cancel()
                try:
                    await self._video_task
                except asyncio.CancelledError:
                    pass
            self.logger.info(f"Input stream ended for session {self._session_id[:8]}")
    
    def _get_current_turn_id(self) -> int:
        """Get current turn_id"""
        if self.control_bus:
            return self.control_bus.get_current_turn_id()
        return self.current_turn_id
    
    def stop(self) -> None:
        """Stop generating Chunks"""
        self._running = False


class OutputStation(Station):
    """
    Output format conversion Station - DAG endpoint.

    Responsibilities (pure format conversion):
    1. Receive Chunk from DAG
    2. Call AudioCodec to encode audio (if needed)
    3. Call Protocol to convert to message (passing type + source for routing)
    4. Call Protocol to encode message
    5. Write to Transport's queues (priority_queue for control, send_queue for data)
    
    DAG decoupling:
    - OutputStation does NOT check chunk.source
    - Passes (chunk.type, chunk.source) to Protocol
    - Protocol decides how to format the message based on type + source
    
    Dual queue design:
    - priority_queue: Control messages (TTS_STOP, STATE changes) - sent immediately
    - send_queue: Normal data (audio) - may be delayed by flow control
    
    NOT responsible for:
    - Flow control (handled by TransportBase.send_worker)
    - Turn management (handled by TransportBase.send_worker)
    - Latency tracking (handled by TransportBase.send_worker)
    """
    
    
    def __init__(
        self,
        session_id: str,
        send_queue: asyncio.Queue,      # From Transport - for normal data (audio)
        priority_queue: asyncio.Queue,  # From Transport - for control messages
        protocol: ProtocolBase,          # From Transport
        audio_codec: Optional[Any] = None,  # From Transport, can be None
        name: str = "OutputStation"
    ):
        """
        Args:
            session_id: Session ID
            send_queue: Transport's send queue for normal data
            priority_queue: Transport's priority queue for control messages
            protocol: Protocol handler
            audio_codec: Audio codec (can be None)
            name: Station name
            
        Note: Queue data format for send_queue is (data, metadata) tuple.
        metadata contains turn_id for latency tracking by send_worker.
        """
        super().__init__(name=name)
        self._session_id = session_id
        self._send_queue = send_queue
        self._priority_queue = priority_queue
        self.protocol = protocol
        self.audio_codec = audio_codec
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process Chunk: convert to protocol message and put into appropriate queue.
        
        DAG routing rules:
        - OutputStation does NOT check chunk.source
        - Passes type + source to Protocol for message formatting
        
        Queue selection based on ChunkType.is_high_priority():
        - High priority (CONTROL_STATE_RESET, STATE changes) -> priority_queue (immediate)
        - Normal (audio, TTS events) -> send_queue (may be delayed by flow control)
        
        Special handling:
        - CONTROL_TURN_SWITCH: converted to TTS_STOP message
        - AUDIO: split into frames and encoded
        
        OutputStation is endpoint, doesn't produce new Chunks.
        """
        # Log chunk reception for debugging
        self.logger.debug(f"OutputStation.process_chunk: type={chunk.type.value}, turn={chunk.turn_id}")
        
        # Special handling for AUDIO chunks: delegate to protocol for processing
        if chunk.type in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO) and chunk.data:
            # Extract audio metadata from chunk
            sample_rate = getattr(chunk, 'sample_rate', 16000)
            channels = getattr(chunk, 'channels', 1)
            await self._send_audio_frames(chunk.data, chunk.turn_id, sample_rate, channels)
            return
            yield  # Keep as generator
        
        # Special handling for CONTROL_TURN_SWITCH: send TTS_STOP
        if chunk.type == ChunkType.CONTROL_TURN_SWITCH:
            await self.handle_turn_switch()
            return
            yield  # Keep as generator
        
        # Special handling for EVENT_TTS_STOP: flush audio buffer first
        if chunk.type == ChunkType.EVENT_TTS_STOP:
            # Step 1: Flush remaining audio buffer BEFORE sending TTS_STOP
            if hasattr(self.protocol, 'flush_audio_buffer'):
                remaining_frames = self.protocol.flush_audio_buffer(self._session_id)
                
                if remaining_frames:
                    self.logger.info(
                        f"Flushing {len(remaining_frames)} remaining audio frames "
                        f"BEFORE TTS_STOP (turn={chunk.turn_id})"
                    )
                    
                    for frame_idx, frame in enumerate(remaining_frames):
                        if not frame:
                            continue
                        
                        # Encode frame
                        if self.audio_codec:
                            try:
                                encoded_data = self.audio_codec.encode(frame)
                            except Exception as e:
                                self.logger.error(f"Failed to encode flushed frame: {e}")
                                continue
                        else:
                            encoded_data = frame
                        
                        # Create audio message
                        message = self.protocol.send_tts_audio(self._session_id, encoded_data)
                        if not message:
                            continue
                        
                        # Encode message
                        try:
                            encoded_message = self.protocol.encode_message(message)
                        except Exception as e:
                            self.logger.error(f"Failed to encode flushed audio message: {e}")
                            continue
                        
                        # Put into send queue
                        metadata = {
                            "type": "audio",
                            "turn_id": chunk.turn_id,
                            "is_first": False,
                            "is_flushed": True  # Mark as flushed frame
                        }
                        await self._send_queue.put((encoded_message, metadata))
            
            # Step 2: Continue to send TTS_STOP message (falls through to normal handling)
        
        # 1. Call Protocol business methods based on Chunk type
        message = await self._chunk_to_message(chunk)
        
        if message is None:
            return
            yield  # Keep as generator
        
        # 2. Encode message
        try:
            encoded_data = self.protocol.encode_message(message)
        except Exception as e:
            self.logger.error(f"Failed to encode message: {e}")
            return
            yield  # Keep as generator
        
        # 3. Choose queue based on chunk type priority
        use_priority = chunk.type.is_high_priority()
        
        # Create metadata for tracking
        metadata = {
            "type": chunk.type.value,
            "turn_id": chunk.turn_id,
            "is_tts_stop": chunk.type == ChunkType.EVENT_TTS_STOP,
        }
        
        if use_priority:
            # Priority queue: just data (no metadata needed for control messages)
            await self._priority_queue.put(encoded_data)
            self.logger.debug(f"Queued priority message: {chunk.type.value}")
        else:
            # Send queue: (data, metadata) tuple for latency tracking
            await self._send_queue.put((encoded_data, metadata))
        
        # OutputStation doesn't produce new Chunks
        return
        yield  # Keep as generator
    
    async def handle_turn_switch(self) -> None:
        """
        Handle turn switch: clear queues and send TTS_STOP.
        
        Steps:
        1. Clear send_queue (discard pending audio frames)
        2. Send TTS_STOP via priority_queue (immediate delivery)
        
        Can be called:
        - From process_chunk() when receiving CONTROL_TURN_SWITCH chunk
        - Directly from DAG when handling CONTROL_STATE_RESET (bypasses queue)
        """
        # 1. Clear send_queue to discard pending audio frames
        cleared_count = 0
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
                cleared_count += 1
            except asyncio.QueueEmpty:
                break
        
        if cleared_count > 0:
            self.logger.info(f"Cleared {cleared_count} items from send_queue")
        
        # 2. Send TTS_STOP via priority_queue for immediate delivery
        tts_stop_msg = self.protocol.send_tts_event(self._session_id, "stop")
        if tts_stop_msg:
            try:
                encoded = self.protocol.encode_message(tts_stop_msg)
                await self._priority_queue.put(encoded)
                self.logger.debug("Sent TTS_STOP via priority queue")
            except Exception as e:
                self.logger.error(f"Failed to send TTS_STOP: {e}")
    
    async def _chunk_to_message(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        """
        Convert Chunk to protocol message.
        
        DAG decoupling:
        - Does NOT check chunk.source for routing decisions
        - Passes type + source to Protocol methods
        - Protocol decides message format based on type + source
        """
        # TEXT chunks - pass source to Protocol for routing decision
        if chunk.type == ChunkType.TEXT:
            text = chunk.data
            source = chunk.source or ""
            role = chunk.role if hasattr(chunk, 'role') else "user"
            return self.protocol.send_text(self._session_id, text, source, role)
        
        # TEXT_DELTA chunks - pass role to Protocol
        elif chunk.type == ChunkType.TEXT_DELTA:
            text = chunk.data
            source = chunk.source or ""
            role = chunk.role if hasattr(chunk, 'role') else "user"
            return self.protocol.send_text_delta(self._session_id, text, source, role)
        
        # AUDIO chunks - skip here, handled separately in process_chunk
        elif chunk.type in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO):
            return None
        
        # Bot thinking event - device can switch to speaker early
        elif chunk.type == ChunkType.EVENT_BOT_THINKING:
            self.logger.info(f"OutputStation received EVENT_BOT_THINKING (turn={chunk.turn_id})")
            # Send TTS start event early so device can switch to speaker while processing
            return self.protocol.send_bot_thinking_event(self._session_id)
        
        # TTS events
        elif chunk.type == ChunkType.EVENT_TTS_START:
            text = None
            if hasattr(chunk, 'event_data') and isinstance(chunk.event_data, dict):
                text = chunk.event_data.get("text")
            self.logger.info(f"OutputStation received EVENT_TTS_START (turn={chunk.turn_id})")
            # Note: If EVENT_BOT_THINKING was sent, this is a duplicate but harmless
            return self.protocol.send_tts_event(self._session_id, "start", text)
        
        elif chunk.type == ChunkType.EVENT_TTS_SENTENCE_START:
            text = None
            if hasattr(chunk, 'event_data') and isinstance(chunk.event_data, dict):
                text = chunk.event_data.get("text")
            return self.protocol.send_tts_event(self._session_id, "sentence_start", text)
        
        elif chunk.type == ChunkType.EVENT_TTS_SENTENCE_END:
            return self.protocol.send_tts_event(self._session_id, "sentence_end")
        
        elif chunk.type == ChunkType.EVENT_TTS_STOP:
            self.logger.info(f"OutputStation received EVENT_TTS_STOP (turn={chunk.turn_id})")
            return self.protocol.send_tts_event(self._session_id, "stop")
        
        # Timeout event - reset client to Listen state
        elif chunk.type == ChunkType.EVENT_TIMEOUT:
            self.logger.warning(
                f"OutputStation received EVENT_TIMEOUT (turn={chunk.turn_id}), "
                f"sending TTS stop to reset client"
            )
            # Send TTS stop to let client return to Listen state
            return self.protocol.send_tts_event(self._session_id, "stop")
        
        # Other Chunks - try default conversion
        else:
            return self.protocol.chunk_to_message(chunk)
    
    async def _send_audio_frames(
        self, 
        pcm_data: bytes, 
        turn_id: int, 
        sample_rate: int = 16000,
        channels: int = 1
    ) -> None:
        """
        Process and send audio frames.
        
        Delegates to Protocol for transport-specific processing:
        - Resampling (if needed)
        - Frame buffering (keeps incomplete tail for next chunk)
        - Frame splitting (protocol-specific requirements)
        
        Then encodes each frame and puts into send queue.
        
        Queue data format: (encoded_message, metadata)
        - metadata = {"type": "audio", "turn_id": turn_id, "is_first": bool}
        
        Args:
            pcm_data: PCM audio data (may be large, from TTS)
            turn_id: Turn ID for latency tracking
            sample_rate: Input sample rate in Hz
            channels: Number of audio channels
        """
        # Delegate to Protocol for transport-specific audio processing
        # Protocol handles resampling + frame buffering + frame splitting
        frames = self.protocol.prepare_audio_data(
            pcm_data, 
            sample_rate, 
            channels,
            session_id=self._session_id
        )
        
        self.logger.debug(f"Sending {len(frames)} audio frames for turn {turn_id}")
        
        # Encode and put into queue
        for frame_idx, frame in enumerate(frames):
            if not frame:
                continue
            
            # Encode frame (PCM -> ex:Opus)
            if self.audio_codec:
                try:
                    encoded_data = self.audio_codec.encode(frame)
                except Exception as e:
                    self.logger.error(f"Failed to encode audio frame: {e}")
                    continue
            else:
                # No codec, send raw PCM
                encoded_data = frame
            
            # Create audio message
            message = self.protocol.send_tts_audio(self._session_id, encoded_data)
            if not message:
                continue
            
            # Encode message
            try:
                encoded_message = self.protocol.encode_message(message)
            except Exception as e:
                self.logger.error(f"Failed to encode message: {e}")
                continue
            
            # Put into send queue with metadata for latency tracking
            # Format: (data, metadata) where metadata contains turn_id and frame info
            metadata = {
                "type": "audio",
                "turn_id": turn_id,
                "is_first_frame": frame_idx == 0,
            }
            await self._send_queue.put((encoded_message, metadata))
    
    def _get_current_turn_id(self) -> int:
        """Get current turn_id"""
        if self.control_bus:
            return self.control_bus.get_current_turn_id()
        return self.current_turn_id
