"""
Transport Stations - Bridge between Transport and Pipeline

InputStation: Read from Transport → Chunk stream
OutputStation: Chunk stream → Send to Transport
"""

import asyncio
from typing import AsyncIterator, Optional, Dict, Any, Union, Callable, Awaitable
from core.station import Station
from core.protocol import ProtocolBase
from core.chunk import Chunk, ChunkType
from loguru import logger


class InputStation(Station):
    """
    Input Station - Starting point of Pipeline.
    
    Responsibilities:
    1. Read raw data from Transport
    2. Decode via Protocol and convert to Chunk
    3. Decode audio via Codec (e.g., Opus → PCM)
    4. Output Chunk stream
    """
    
    def __init__(
        self,
        session_id: str,
        read_func: Callable[[], Awaitable[Union[bytes, str]]],  # Read from Transport
        protocol: ProtocolBase,
        audio_codec: Optional[Any] = None,
        name: str = "InputStation"
    ):
        """
        Args:
            session_id: Session ID
            read_func: Function to read raw data from Transport
            protocol: Protocol instance
            audio_codec: Optional audio codec
            name: Station name
        """
        super().__init__(name=name)
        self._session_id = session_id
        self.read_func = read_func
        self.protocol = protocol
        self.audio_codec = audio_codec
        self._running = True
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        InputStation is the starting point, doesn't process upstream Chunks.
        
        Actual data generation is in _generate_chunks().
        """
        # If receiving upstream Chunk (shouldn't happen), passthrough
        yield chunk
    
    async def _generate_chunks(self) -> AsyncIterator[Chunk]:
        """
        Generate Chunk stream (true input source).
        
        This method is specially handled by Pipeline as the starting point.
        """
        self.logger.info(f"Starting input stream for session {self._session_id[:8]}")
        
        try:
            while self._running:
                # 1. Read raw data from Transport
                try:
                    raw_data = await self.read_func()
                except ConnectionError:
                    self.logger.info(f"Connection closed for session {self._session_id[:8]}")
                    break
                
                if raw_data is None:
                    # End of stream
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
                
                # 4. Audio decode (Opus → PCM)
                if chunk.type == ChunkType.AUDIO_RAW and self.audio_codec and chunk.data:
                    try:
                        pcm_data = self.audio_codec.decode(chunk.data)
                        chunk.data = pcm_data
                    except Exception as e:
                        self.logger.error(f"Failed to decode audio: {e}")
                        continue
                
                # 5. Output Chunk
                self.logger.debug(f"Input chunk: {chunk.type.value} from {chunk.source}")
                yield chunk
        
        except Exception as e:
            self.logger.error(f"Error in input stream: {e}", exc_info=True)
        
        finally:
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
    Output Station - End point of Pipeline.
    
    Responsibilities:
    1. Receive Chunks from upstream
    2. Convert to protocol messages via Protocol business methods
    3. Encode audio via Codec (e.g., PCM → Opus)
    4. Encode messages via Protocol
    5. Apply flow control (if needed)
    6. Send via Transport
    """
    
    def __init__(
        self,
        session_id: str,
        write_func: Callable[[Union[bytes, str]], Awaitable[None]],  # Send to Transport
        protocol: ProtocolBase,
        audio_codec: Optional[Any] = None,
        flow_controller: Optional[Any] = None,
        latency_monitor: Optional[Any] = None,
        name: str = "OutputStation"
    ):
        """
        Args:
            session_id: Session ID
            write_func: Function to write data to Transport
            protocol: Protocol instance
            audio_codec: Optional audio codec
            flow_controller: Optional flow controller
            latency_monitor: Optional latency monitor for recording metrics
            name: Station name
        """
        super().__init__(name=name)
        self._session_id = session_id
        self.write_func = write_func
        self.protocol = protocol
        self.audio_codec = audio_codec
        self.flow_controller = flow_controller
        self.latency_monitor = latency_monitor
        
        # Track first audio sent per turn (for latency monitoring)
        self._first_audio_sent_recorded: set = set()  # Set of "session_id_turn_id" keys
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process Chunk: convert to protocol message and send.
        
        OutputStation is the end point, doesn't produce new Chunks.
        """
        # Special handling for AUDIO_RAW: split into frames and send with flow control
        if chunk.type == ChunkType.AUDIO_RAW and "tts" in chunk.source.lower() and chunk.data:
            await self._send_audio_frames(chunk.data, chunk.session_id, chunk.turn_id)
            return
            yield  # Make it a generator
        
        # 1. Dispatch to Protocol business methods based on Chunk type
        message = await self._chunk_to_message(chunk)
        
        if message is None:
            # Chunk doesn't need sending
            return
            yield  # Make it a generator
        
        # 2. Encode message
        try:
            encoded_data = self.protocol.encode_message(message)
        except Exception as e:
            self.logger.error(f"Failed to encode message: {e}")
            return
            yield  # Make it a generator
        
        # 3. Send immediately (non-audio messages)
        await self._send_immediately(encoded_data)
        
        # 4. Handle TTS_STOP: increment turn after bot finishes speaking (natural completion)
        # Note: Only increment for TTS_STOP from TTS station, not from interrupt handler
        if chunk.type == ChunkType.EVENT_TTS_STOP:
            # Check if this is a natural completion (from TTS station) vs interrupt (from SessionManager)
            is_natural_completion = "tts" in chunk.source.lower()
            
            if is_natural_completion and self.control_bus:
                # Increment turn to allow user to speak
                await self.control_bus.increment_turn(
                    source="Transport",
                    reason="bot_finished"
                )
                self.logger.info(f"Turn incremented after bot finished speaking (session={self._session_id[:8]})")
            elif not is_natural_completion:
                self.logger.debug(f"TTS_STOP from {chunk.source}, not incrementing turn (already incremented by interrupt)")
        
        # OutputStation doesn't produce new Chunks
        return
        yield  # Make it a generator
    
    async def _chunk_to_message(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        """
        Convert Chunk to protocol message (calling Protocol business methods).
        """
        # TEXT chunks - determine type based on source
        if chunk.type == ChunkType.TEXT:
            text = chunk.data
            
            if "asr" in chunk.source.lower():
                return self.protocol.send_stt(self._session_id, text)
            
            elif "agent" in chunk.source.lower():
                return self.protocol.send_llm(self._session_id, text)
        
        # AUDIO chunks - skip here, handled separately in process_chunk
        elif chunk.type == ChunkType.AUDIO_RAW:
            # Audio is handled directly in process_chunk() with frame splitting
            return None
        
        # TTS events
        elif chunk.type == ChunkType.EVENT_TTS_START:
            text = None
            if hasattr(chunk, 'event_data') and isinstance(chunk.event_data, dict):
                text = chunk.event_data.get("text")
            return self.protocol.send_tts_event(self._session_id, "start", text)
        
        elif chunk.type == ChunkType.EVENT_TTS_SENTENCE_START:
            text = None
            if hasattr(chunk, 'event_data') and isinstance(chunk.event_data, dict):
                text = chunk.event_data.get("text")
            return self.protocol.send_tts_event(self._session_id, "sentence_start", text)
        
        elif chunk.type == ChunkType.EVENT_TTS_SENTENCE_END:
            return self.protocol.send_tts_event(self._session_id, "sentence_end")
        
        elif chunk.type == ChunkType.EVENT_TTS_STOP:
            return self.protocol.send_tts_event(self._session_id, "stop")
        
        # State events - notify client of state changes
        elif chunk.type == ChunkType.EVENT_STATE_LISTENING:
            return self.protocol.start_listen(self._session_id)
        
        elif chunk.type == ChunkType.EVENT_STATE_IDLE:
            return self.protocol.stop_listen(self._session_id)
        
        elif chunk.type == ChunkType.EVENT_STATE_SPEAKING:
            return self.protocol.start_speaker(self._session_id)
        
        # Other Chunks - try default conversion
        else:
            return self.protocol.chunk_to_message(chunk)
    
    async def _send_audio_frames(self, pcm_data: bytes, session_id: str, turn_id: int) -> None:
        """
        Split audio into frames, encode each frame, and send with flow control.
        
        Args:
            pcm_data: PCM audio data (may be large, from TTS)
            session_id: Session ID for latency monitoring
            turn_id: Turn ID for latency monitoring
        """
        # Split PCM into 60ms frames (16kHz, mono, 16-bit = 1920 bytes per 60ms)
        frame_size = 1920  # 60ms at 16kHz mono
        frames = []
        for i in range(0, len(pcm_data), frame_size):
            frames.append(pcm_data[i:i + frame_size])
        
        self.logger.debug(f"Sending {len(frames)} audio frames with flow control")
        
        # Get current turn_id at start
        current_turn_id = self._get_current_turn_id()
        
        # Encode and send each frame
        for frame_idx, frame in enumerate(frames):
            if not frame:
                continue
            
            # Check if turn has changed (interrupt occurred)
            if self._get_current_turn_id() != current_turn_id:
                self.logger.info(f"Turn changed during audio transmission, stopping (was {current_turn_id}, now {self._get_current_turn_id()})")
                break
            
            # Encode frame (PCM → Opus)
            if self.audio_codec:
                try:
                    opus_data = self.audio_codec.encode(frame)
                except Exception as e:
                    self.logger.error(f"Failed to encode audio frame: {e}")
                    continue
            else:
                # No codec, send raw PCM
                opus_data = frame
            
            # Create audio message
            message = self.protocol.send_tts_audio(self._session_id, opus_data)
            if not message:
                continue
            
            # Encode message
            try:
                encoded_data = self.protocol.encode_message(message)
            except Exception as e:
                self.logger.error(f"Failed to encode message: {e}")
                continue
            
            # Send with flow control
            if self.flow_controller:
                await self.flow_controller.wait_for_next_frame()
            
            await self._send_immediately(encoded_data)
            
            # Record latency for first audio frame sent (T6: first_audio_sent)
            if frame_idx == 0 and self.latency_monitor:
                session_turn_key = f"{session_id}_{turn_id}"
                if session_turn_key not in self._first_audio_sent_recorded:
                    self.latency_monitor.record(session_id, turn_id, "first_audio_sent")
                    self._first_audio_sent_recorded.add(session_turn_key)
                    self.logger.debug("Recorded first audio sent to client")
                    
                    # Generate and log latency report for this turn
                    self.latency_monitor.log_report(session_id, turn_id)
                    self.latency_monitor.print_summary(session_id, turn_id)
    
    async def _send_immediately(self, data: Union[bytes, str]) -> None:
        """Send data immediately"""
        try:
            await self.write_func(data)
        except Exception as e:
            self.logger.error(f"Failed to send data: {e}")
    
    def _get_current_turn_id(self) -> int:
        """Get current turn_id"""
        if self.control_bus:
            return self.control_bus.get_current_turn_id()
        return self.current_turn_id


