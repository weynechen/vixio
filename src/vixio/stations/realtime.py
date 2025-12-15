"""
RealtimeStation - End-to-End Voice Conversation Station

Input: AUDIO_RAW (from transport)
Output: AUDIO_RAW (streaming) + TEXT_DELTA (transcription) + Events

This station uses a realtime model that integrates VAD + ASR + LLM + TTS,
providing end-to-end voice conversation capability without separate
VAD, ASR, Agent, and TTS stations.

Data flow:
    transport_in -> RealtimeStation -> transport_out

The realtime model handles:
- Voice Activity Detection (VAD): Detects when user starts/stops speaking
- Speech Recognition (ASR): Transcribes user speech
- Language Model (LLM): Generates response
- Text-to-Speech (TTS): Synthesizes response audio

All processing happens server-side, minimizing latency.
"""

import asyncio
from typing import AsyncIterator, Optional

from vixio.core.station import StreamStation, StationRole
from vixio.core.chunk import (
    Chunk,
    ChunkType,
    AudioChunk,
    TextDeltaChunk,
    EventChunk,
)
from vixio.providers.qwen.qwen_omni_realtime import QwenOmniRealtimeProvider


class RealtimeStation(StreamStation):
    """
    Realtime voice conversation station.
    
    Uses Qwen-Omni-Realtime model for end-to-end voice processing.
    Input audio is sent to the model, output audio and events are streamed back.
    
    Input: AUDIO_RAW (user speech)
    Output: 
        - AUDIO_RAW (model response audio)
        - TEXT_DELTA (response text for display)
        - EVENT_VAD_START/END (speech detection events)
        - EVENT_TTS_START/STOP (response events)
    """
    
    ROLE = StationRole.STREAM
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW]
    LATENCY_METRIC_NAME = "realtime_first_audio"
    LATENCY_OUTPUT_TYPES = [ChunkType.AUDIO_RAW]
    
    # Completion contract
    EMITS_COMPLETION = False  # Realtime model handles turn management
    AWAITS_COMPLETION = False  # Processes audio continuously
    
    def __init__(
        self,
        provider: QwenOmniRealtimeProvider,
        name: str = "realtime",
        emit_text: bool = True,
        emit_events: bool = True,
    ):
        """
        Initialize Realtime station.
        
        Args:
            provider: QwenOmniRealtimeProvider instance
            name: Station name
            emit_text: Whether to emit TEXT_DELTA chunks for response text
            emit_events: Whether to emit VAD/TTS events
        """
        super().__init__(name=name, enable_interrupt_detection=True)
        self.provider = provider
        self.emit_text = emit_text
        self.emit_events = emit_events
        
        # State tracking
        self._is_processing = False
        self._output_task: Optional[asyncio.Task] = None
        self._output_queue: asyncio.Queue = asyncio.Queue()
        
        # Register callbacks
        self.provider.set_callbacks(
            on_speech_started=self._on_speech_started,
            on_speech_stopped=self._on_speech_stopped,
            on_response_started=self._on_response_started,
            on_response_done=self._on_response_done,
        )
    
    def _on_speech_started(self) -> None:
        """Callback when user starts speaking."""
        self.logger.debug("User speech started")
    
    def _on_speech_stopped(self) -> None:
        """Callback when user stops speaking."""
        self.logger.debug("User speech stopped")
    
    def _on_response_started(self) -> None:
        """Callback when model starts responding."""
        self.logger.debug("Model response started")
    
    def _on_response_done(self) -> None:
        """Callback when model finishes responding."""
        self.logger.debug("Model response done")
    
    async def _start_output_collector(self, session_id: str, turn_id: int) -> None:
        """
        Start background task to collect outputs from provider.
        
        This runs continuously to collect audio, text, and events from the
        provider and put them into the output queue.
        """
        if self._output_task and not self._output_task.done():
            return  # Already running
        
        async def collect_outputs():
            while self._is_processing and self.provider.is_connected:
                try:
                    # Collect audio
                    audio = await self.provider.get_audio_async(timeout=0.02)
                    if audio:
                        chunk = AudioChunk(
                            type=ChunkType.AUDIO_RAW,
                            data=audio,
                            sample_rate=self.provider.output_sample_rate,
                            channels=1,
                            source=self.name,
                            session_id=session_id,
                            turn_id=turn_id,
                        )
                        await self._output_queue.put(chunk)
                    
                    # Collect text
                    if self.emit_text:
                        text_result = self.provider.get_text(timeout=0.01)
                        if text_result:
                            text_type, text = text_result
                            if text_type == "delta":
                                chunk = TextDeltaChunk(
                                    type=ChunkType.TEXT_DELTA,
                                    data=text,
                                    source=self.name,
                                    session_id=session_id,
                                    turn_id=turn_id,
                                )
                                await self._output_queue.put(chunk)
                    
                    # Collect events
                    if self.emit_events:
                        event = self.provider.get_event(timeout=0.01)
                        if event:
                            event_chunk = self._create_event_chunk(
                                event, session_id, turn_id
                            )
                            if event_chunk:
                                await self._output_queue.put(event_chunk)
                    
                    await asyncio.sleep(0.005)
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"Error collecting outputs: {e}")
                    await asyncio.sleep(0.1)
        
        self._output_task = asyncio.create_task(collect_outputs())
    
    def _create_event_chunk(
        self, event, session_id: str, turn_id: int
    ) -> Optional[EventChunk]:
        """Create EventChunk from provider event."""
        event_mapping = {
            "speech_started": ChunkType.EVENT_VAD_START,
            "speech_stopped": ChunkType.EVENT_VAD_END,
            "response_started": ChunkType.EVENT_TTS_START,
            "response_done": ChunkType.EVENT_TTS_STOP,
        }
        
        chunk_type = event_mapping.get(event.event_type)
        if not chunk_type:
            return None
        
        event_data = {"event": event.event_type}
        if event.data:
            event_data["data"] = event.data
        
        return EventChunk(
            type=chunk_type,
            event_data=event_data,
            source=self.name,
            session_id=session_id,
            turn_id=turn_id,
        )
    
    async def _stop_output_collector(self) -> None:
        """Stop the output collector task."""
        if self._output_task:
            self._output_task.cancel()
            try:
                await self._output_task
            except asyncio.CancelledError:
                pass
            self._output_task = None
    
    async def reset_state(self) -> None:
        """Reset station state for new turn."""
        self._is_processing = False
        # Clear output queue
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.logger.info("Realtime station state reset")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process audio chunk through realtime model.
        
        For each audio input:
        1. Send to provider
        2. Yield any available outputs (audio, text, events)
        
        Args:
            chunk: Input audio chunk
            
        Yields:
            Output chunks (audio, text, events)
        """
        # Only process AUDIO_RAW
        if chunk.type != ChunkType.AUDIO_RAW:
            return
            yield  # Make this an async generator
        
        # Start output collector if needed
        if not self._is_processing:
            self._is_processing = True
            await self._start_output_collector(chunk.session_id, chunk.turn_id)
        
        # Send audio to provider
        if isinstance(chunk.data, bytes) and len(chunk.data) > 0:
            await self.provider.send_audio_async(chunk.data)
        
        # Yield any available outputs
        while not self._output_queue.empty():
            try:
                output_chunk = self._output_queue.get_nowait()
                yield output_chunk
            except asyncio.QueueEmpty:
                break
    
    async def cleanup(self) -> None:
        """Cleanup station resources."""
        self._is_processing = False
        await self._stop_output_collector()
        
        if self.provider and hasattr(self.provider, 'cleanup'):
            await self.provider.cleanup()
        
        self.logger.debug("Realtime station cleaned up")
