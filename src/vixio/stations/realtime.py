"""
RealtimeStation - End-to-End Voice Conversation Station

Input: AUDIO_DELTA (streaming audio from transport)
Output: AUDIO (streaming) + TEXT_DELTA/TEXT + Events + ToolCallChunk

This station uses a realtime model that integrates VAD + ASR + LLM + TTS,
providing end-to-end voice conversation capability without separate
VAD, ASR, Agent, and TTS stations.

Processing Model: Two-Phase State Machine
==========================================
Phase 1 - INPUT: Continuously collect user audio until speech stops
Phase 2 - OUTPUT: Stream provider response (like AgentStation)

This mirrors the traditional VAD → ASR → Agent → TTS pipeline:
- INPUT phase = VAD + ASR (collecting and transcribing)
- OUTPUT phase = Agent + TTS (generating response)

The phase transition is triggered by provider's speech_stopped event,
similar to how VADStation triggers ASRStation.

Data flow:
    transport_in -> RealtimeStation -> [SentenceAggregator] -> transport_out

The realtime model handles:
- Voice Activity Detection (VAD): Detects when user starts/stops speaking
- Speech Recognition (ASR): Transcribes user speech
- Language Model (LLM): Generates response and handles tool usage
- Text-to-Speech (TTS): Synthesizes response audio

All processing happens server-side, minimizing latency.
"""

import asyncio
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, List
from enum import Enum

from vixio.core.station import StreamStation, StationRole
from vixio.core.chunk import (
    Chunk,
    ChunkType,
    AudioChunk,
    TextDeltaChunk,
    TextChunk,
    EventChunk,
    ToolCallChunk,
    ToolOutputChunk,
)
from vixio.providers.realtime import BaseRealtimeProvider, RealtimeEvent, RealtimeEventType
from vixio.core.tools.types import ToolDefinition
from vixio.utils import get_latency_monitor
from vixio.core.middleware import with_middlewares


class ProcessingPhase(Enum):
    """Processing phase for realtime station."""
    INPUT = "input"    # Collecting user audio
    OUTPUT = "output"  # Streaming provider response

@with_middlewares(
    # Note: StreamStation base class automatically provides:
    # - InputValidatorMiddleware (validates ALLOWED_INPUT_TYPES)
    # - LatencyMonitorMiddleware (monitors first AUDIO output)
    # - InterruptDetectorMiddleware (if enable_interrupt_detection=True)
    # - ErrorHandlerMiddleware (graceful error handling)
)
class RealtimeStation(StreamStation):
    """
    Realtime voice conversation station.
    
    Uses a BaseRealtimeProvider (e.g. Qwen, OpenAI) for end-to-end voice processing.
    Input audio is sent to the model, output audio and events are streamed back.
    
    This station now uses the AsyncIterator pattern (stream_response) for clean,
    AgentStation-like code, instead of callback-based queue management.
    
    Input: AUDIO_DELTA (user speech), TOOL_OUTPUT (tool results)
    Output: 
        - AUDIO (model response audio)
        - TEXT_DELTA (response text for display)
        - EVENT_VAD_START/END (speech detection events)
        - TOOL_CALL (tool execution request)
    """
    
    ROLE = StationRole.STREAM
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_DELTA, ChunkType.AUDIO, ChunkType.TOOL_OUTPUT]  # Support both audio types
    # Use "tts_first_audio_ready" to integrate with LatencyMonitor's standard metrics
    # For realtime station, this represents the first audio output from the integrated model
    LATENCY_METRIC_NAME = "tts_first_audio_ready"
    LATENCY_OUTPUT_TYPES = [ChunkType.AUDIO]
    
    # Completion contract
    EMITS_COMPLETION = True   # Emit completion to flush downstream aggregators
    AWAITS_COMPLETION = False  # Processes audio continuously
    
    def __init__(
        self,
        provider: BaseRealtimeProvider,
        name: str = "realtime",
        emit_text: bool = True,
        emit_events: bool = True,
        tools: Optional[List[ToolDefinition]] = None,
        instructions: Optional[str] = None,
    ):
        """
        Initialize Realtime station.
        
        Args:
            provider: Realtime provider instance
            name: Station name
            emit_text: Whether to emit TEXT_DELTA chunks for response text
            emit_events: Whether to emit VAD/TTS events
            tools: Optional list of tools to register with the provider
            instructions: Optional system instructions
        """
        super().__init__(name=name, output_role=None, enable_interrupt_detection=True)
        self.provider = provider
        self.emit_text = emit_text
        self.emit_events = emit_events
        self.tools = tools or []
        self.instructions = instructions
        
        # State machine
        self._phase = ProcessingPhase.INPUT
        self._speech_stopped = False
        self._pending_events: asyncio.Queue = asyncio.Queue()
        
        # Latency monitoring for realtime station
        self._latency_monitor = get_latency_monitor()
        
        # Register event handler for phase detection
        self.provider.set_event_handler(self._handle_provider_event)
    
    def _convert_event_to_chunk(self, event: RealtimeEvent) -> Optional[Chunk]:
        """
        Convert RealtimeEvent to Chunk for OUTPUT phase.
        
        These are response events: audio, text, tool calls.
        Note: RESPONSE_START is handled separately before streaming starts.
        
        Args:
            event: RealtimeEvent from provider
            
        Returns:
            Corresponding Chunk or None
        """
        chunk = None
        
        # 1. Audio Delta -> AUDIO
        if event.type == RealtimeEventType.AUDIO_DELTA:
            chunk = AudioChunk(
                type=ChunkType.AUDIO,  # Realtime model outputs complete audio frames
                data=event.data,
                sample_rate=self.provider.output_sample_rate if hasattr(self.provider, 'output_sample_rate') else 24000,
                channels=1,
                source=self.name,  # Use station name for latency monitoring
                role="bot",  # Bot response audio
                session_id=self.current_session_id,
                turn_id=self.current_turn_id,
            )

        # 2. Text Delta -> TEXT_DELTA
        elif event.type == RealtimeEventType.TEXT_DELTA:
            if self.emit_text and event.text:
                self.logger.debug(
                    f"[REALTIME] Emitting TEXT_DELTA: '{event.text}' "
                    f"(session={self.current_session_id[:8]}, turn={self.current_turn_id})"
                )
                chunk = TextDeltaChunk(
                    type=ChunkType.TEXT_DELTA,
                    data=event.text,
                    source=self.name,  # Use station name for consistency
                    role="bot",  # Bot response text
                    session_id=self.current_session_id,
                    turn_id=self.current_turn_id,
                )

        # 3. Tool Call -> TOOL_CALL
        elif event.type == RealtimeEventType.TOOL_CALL:
            chunk = ToolCallChunk(
                type=ChunkType.TOOL_CALL,
                call_id=event.tool_call_id,
                tool_name=event.tool_name,
                arguments=event.tool_args,
                source=self.name,  # Use station name for consistency
                session_id=self.current_session_id,
                turn_id=self.current_turn_id,
            )
        
        # 4. RESPONSE_DONE -> TTS_STOP
        elif event.type == RealtimeEventType.RESPONSE_DONE:
            if self.emit_events:
                chunk = EventChunk(
                    type=ChunkType.EVENT_TTS_STOP,
                    source=self.name,
                    session_id=self.current_session_id,
                    turn_id=self.current_turn_id,
                )
        
        # Note: RESPONSE_START is not handled here, it's emitted before streaming starts
        
        elif event.type == RealtimeEventType.ERROR:
            self.logger.error(f"Realtime error: {event.text}")
            # Optionally emit error chunk
        
        return chunk

    # Track current context for event handling
    current_session_id: str = ""
    current_turn_id: int = 0
    
    def _handle_provider_event(self, event: RealtimeEvent) -> None:
        """
        Handle events from provider (callback thread).
        
        This is used to detect phase transitions and collect immediate events
        (VAD, user transcript) during INPUT phase.
        """
        try:
            # Detect phase transition: INPUT -> OUTPUT
            if event.type == RealtimeEventType.SPEECH_STOP:
                self._speech_stopped = True
                
                # Record T0: user_speech_end IMMEDIATELY when VAD detects speech stop
                # This must be done here (not in _convert_immediate_event) to capture
                # the exact moment when speech stops, not when the event is processed
                self._latency_monitor.record(
                    self.current_session_id,
                    self.current_turn_id,
                    "user_speech_end",
                )
                self.logger.info(
                    f"Speech stopped detected, recorded user_speech_end "
                    f"(session={self.current_session_id[:8]}, turn={self.current_turn_id})"
                )
            
            # Enqueue immediate events (VAD, user transcript) for INPUT phase
            if event.type in [
                RealtimeEventType.SPEECH_START,
                RealtimeEventType.SPEECH_STOP,
                RealtimeEventType.TRANSCRIPT_COMPLETE
            ]:
                # Thread-safe: put_nowait is safe from callback thread
                self._pending_events.put_nowait(event)
                
        except Exception as e:
            self.logger.error(f"Error handling provider event: {e}")

    async def reset_state(self) -> None:
        """Reset station state for new turn."""
        self._phase = ProcessingPhase.INPUT
        self._speech_stopped = False
        
        # Clear pending events
        while not self._pending_events.empty():
            try:
                self._pending_events.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        self.logger.info("Realtime station state reset")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process incoming chunks with two-phase state machine.
        
        Phase 1 - INPUT: Send audio, yield immediate events (VAD, transcript)
        Phase 2 - OUTPUT: Stream full response (triggered by speech_stopped)
        
        This mimics the traditional VAD → Agent flow within a single station.
        
        Args:
            chunk: Input chunk (AUDIO_DELTA or TOOL_OUTPUT)
            
        Yields:
            Output chunks (immediate events in INPUT, full response in OUTPUT)
        """
        # Update context
        self.current_session_id = chunk.session_id
        self.current_turn_id = chunk.turn_id
        
        # Initialize session on first chunk
        # tools not supported yet
        if self._phase == ProcessingPhase.INPUT:
            if self.tools:
                await self.provider.update_session(
                    instructions=self.instructions,
                    tools=self.tools
                )
        
        # ===== PHASE 1: INPUT (like VAD + ASR) =====
        if self._phase == ProcessingPhase.INPUT:
            if chunk.type in (ChunkType.AUDIO_DELTA, ChunkType.AUDIO):
                if isinstance(chunk.data, bytes) and len(chunk.data) > 0:
                    # Send audio to provider (non-blocking)
                    await self.provider.send_audio(chunk.data)
                    self.logger.debug(f"Sent {len(chunk.data)} bytes audio to provider")
                    
                    # Yield immediate events (VAD, user transcript)
                    while not self._pending_events.empty():
                        try:
                            event = self._pending_events.get_nowait()
                            immediate_chunk = self._convert_immediate_event(event)
                            if immediate_chunk:
                                yield immediate_chunk
                        except asyncio.QueueEmpty:
                            break
                    
                    # Check if speech stopped -> switch to OUTPUT phase
                    if self._speech_stopped:
                        self.logger.info("=== Switching to OUTPUT phase ===")
                        self._phase = ProcessingPhase.OUTPUT
                        
                        # Emit TTS_START to signal client to switch to speaker mode
                        if self.emit_events:
                            self.logger.info("Emitting TTS_START before streaming response")
                            yield EventChunk(
                                type=ChunkType.EVENT_TTS_START,
                                source=self.name,
                                session_id=chunk.session_id,
                                turn_id=chunk.turn_id,
                            )
                        
                        # ===== PHASE 2: OUTPUT (like Agent) =====
                        # Now stream the full response (blocks until complete)
                        self.logger.info("Streaming provider response...")
                        async for event in self.provider.stream_response():
                            output_chunk = self._convert_event_to_chunk(event)
                            if output_chunk:
                                yield output_chunk
                            
                            # Check for completion
                            if event.type == RealtimeEventType.RESPONSE_DONE:
                                self.logger.info("Response complete, resetting to INPUT phase")
                                
                                # Emit completion signal to flush downstream aggregators
                                completion_chunk = self.emit_completion(
                                    session_id=self.current_session_id,
                                    turn_id=self.current_turn_id
                                )
                                self.logger.debug(f"[REALTIME] Emitting completion event to flush aggregators")
                                yield completion_chunk
                                
                                # Reset for next turn
                                self._phase = ProcessingPhase.INPUT
                                self._speech_stopped = False
                                break
        
        # Handle tool output (less common, but supported)
        elif chunk.type == ChunkType.TOOL_OUTPUT and isinstance(chunk, ToolOutputChunk):
            self.logger.info(f"Sending tool output: {chunk.call_id}")
            await self.provider.send_tool_output(chunk.call_id, chunk.output)
            
            # Stream response after tool output
            async for event in self.provider.stream_response():
                output_chunk = self._convert_event_to_chunk(event)
                if output_chunk:
                    yield output_chunk

                if event.type == RealtimeEventType.RESPONSE_DONE:
                    # Emit completion signal to flush downstream aggregators
                    completion_chunk = self.emit_completion(
                        session_id=self.current_session_id,
                        turn_id=self.current_turn_id
                    )
                    self.logger.debug(f"[REALTIME] Emitting completion event after tool output")
                    yield completion_chunk
                    break
    
    def _convert_immediate_event(self, event: RealtimeEvent) -> Optional[Chunk]:
        """
        Convert immediate events (VAD, transcript) to chunks.
        
        These are yielded during INPUT phase.
        """
        if event.type == RealtimeEventType.SPEECH_START:
            # Cancel turn timeout if configured
            if self.control_bus:
                self.control_bus.cancel_turn_timeout()
            
            if self.emit_events:
                return EventChunk(
                    type=ChunkType.EVENT_VAD_START,
                    source=self.name,
                    session_id=self.current_session_id,
                    turn_id=self.current_turn_id,
                )
        
        elif event.type == RealtimeEventType.SPEECH_STOP:
            # Note: user_speech_end is already recorded in _handle_provider_event
            # for accurate timing (at the moment speech stops, not when event is processed)
            
            if self.emit_events:
                return EventChunk(
                    type=ChunkType.EVENT_VAD_END,
                    source=self.name,
                    session_id=self.current_session_id,
                    turn_id=self.current_turn_id,
                )
        
        elif event.type == RealtimeEventType.TRANSCRIPT_COMPLETE:
            if self.emit_text and event.text:
                return TextChunk(
                    type=ChunkType.TEXT,
                    data=event.text,
                    source="asr",
                    role="user",  # User transcript
                    session_id=self.current_session_id,
                    turn_id=self.current_turn_id,
                )
        
        return None
    
    async def cleanup(self) -> None:
        """Cleanup station resources."""
        self._phase = ProcessingPhase.INPUT
        self._speech_stopped = False
        
        if hasattr(self.provider, 'cleanup'):
            await self.provider.cleanup()
        elif hasattr(self.provider, 'disconnect'):
            await self.provider.disconnect()
        
        self.logger.debug("Realtime station cleaned up")
