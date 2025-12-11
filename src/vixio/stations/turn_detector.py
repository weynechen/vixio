"""
TurnDetectorStation - Detect when user finishes speaking and handle interrupts

Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING
       + AUDIO_RAW (optional, when buffer_audio=True)
Output: EVENT_STREAM_COMPLETE (triggers ASR)
        + AUDIO_RAW (optional, when buffer_audio=True)

Modes:
- Events only (buffer_audio=False): Only processes VAD events, outputs completion.
  Use with VAD in buffer mode (VAD handles audio buffering).
- Buffer mode (buffer_audio=True): Also buffers AUDIO_RAW, outputs on turn end.
  Use with VAD in passthrough mode.

Note: Sends interrupt signal to ControlBus when user speaks during bot speaking.
Session's interrupt handler injects CONTROL_STATE_RESET into pipeline.

Completion Contract:
- AWAITS_COMPLETION: False (listens to VAD events, not completion)
- EMITS_COMPLETION: True (emits completion when turn ends, triggers ASR)
"""

import asyncio
import time
from typing import AsyncIterator, Optional, List
from vixio.core.station import DetectorStation
from vixio.core.chunk import Chunk, ChunkType, EventChunk, ControlChunk, AudioChunk
from vixio.utils import get_latency_monitor


class TurnDetectorStation(DetectorStation):
    """
    Turn detector: Detects when user finishes speaking and handles interrupts.
    
    Input: EVENT_VAD_* + EVENT_BOT_* events, optionally AUDIO_RAW
    Output: EVENT_STREAM_COMPLETE, optionally buffered AUDIO_RAW
    
    Modes:
    - Events only (default): Process VAD events, emit completion on silence threshold.
      VAD handles audio buffering in this mode.
    - Buffer mode: Also buffer AUDIO_RAW and output on turn end.
      Use when VAD is in passthrough mode.
    
    Strategy:
    - Wait inline after VAD_END for silence threshold (using cancellable sleep)
    - If silence continues, emit EVENT_STREAM_COMPLETE (+ buffered audio if enabled)
    - If voice resumes (VAD_START) or interrupted, set cancel flag to suppress
    - Track session state (LISTENING vs SPEAKING)
    - Send interrupt signal when user speaks during bot speaking
    
    Turn Management:
    - Sends interrupt signal to ControlBus when user interrupts (speaks during bot speaking)
    - Session's interrupt handler increments turn_id for all interrupts
    - TTS station increments turn_id when bot finishes speaking
    
    Completion Contract:
    - Does NOT await completion (listens to VAD state events)
    - Emits completion when turn ends (triggers ASR via EVENT_STREAM_COMPLETE)
    
    Note: This station doesn't use middlewares due to its complex state machine logic,
    async timers, and conditional branching. It inherits DetectorStation for classification
    but implements its own process_chunk without middleware decoration.
    """
    
    # DetectorStation configuration
    # TurnDetector processes VAD/BOT events (audio optional based on buffer_audio)
    ALLOWED_INPUT_TYPES = [
        ChunkType.AUDIO_RAW,
        ChunkType.EVENT_VAD_START,
        ChunkType.EVENT_VAD_END,
        ChunkType.EVENT_BOT_STARTED_SPEAKING,
        ChunkType.EVENT_BOT_STOPPED_SPEAKING
    ]
    
    # Completion contract: emit completion when turn ends
    EMITS_COMPLETION = True
    AWAITS_COMPLETION = False  # Listens to VAD events, not completion signals
    
    def __init__(
        self,
        silence_threshold_ms: int = 100,
        interrupt_enabled: bool = True,
        buffer_audio: bool = True,
        name: str = "TurnDetector"
    ):
        """
        Initialize turn detector station.
        
        Args:
            silence_threshold_ms: Silence duration to consider turn ended (default: 100ms)
            interrupt_enabled: Whether to enable interrupt detection (default: True)
            buffer_audio: If True, buffer audio and output on turn end.
                         If False, only process VAD events (VAD handles audio buffering).
            name: Station name
        """
        super().__init__(name=name)
        self.silence_threshold = silence_threshold_ms / 1000.0  # Convert to seconds
        self.interrupt_enabled = interrupt_enabled
        self.buffer_audio = buffer_audio
        self._should_emit_turn_end = False
        self._waiting_session_id: Optional[str] = None
        self._bot_is_speaking = False  # Track if bot is currently speaking
        
        # Audio buffering (only used when buffer_audio=True)
        self._audio_buffer: List[AudioChunk] = []
        self._is_collecting_audio = False  # True after VAD_START, False after turn end
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
        
        if buffer_audio:
            self.logger.info("TurnDetector running in buffer mode (outputs audio on turn end)")
        else:
            self.logger.info("TurnDetector running in events-only mode (VAD handles audio)")
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk for turn detection, interrupt handling, and optional audio buffering.
        
        DAG routing rules:
        - Process VAD/BOT events
        - If buffer_audio=True: also buffer AUDIO_RAW, output on turn end
        - If buffer_audio=False: ignore AUDIO_RAW (VAD handles it)
        - Output: EVENT_STREAM_COMPLETE (+ optional buffered AUDIO_RAW)
        
        Logic:
        - On AUDIO_RAW: Buffer if buffer_audio=True and collecting
        - On EVENT_VAD_START: Start collecting (if buffer_audio), check for interrupt
        - On EVENT_VAD_END: Wait for silence threshold, emit completion (+ audio if buffering)
        - On EVENT_BOT_STARTED_SPEAKING: Set bot speaking flag
        - On EVENT_BOT_STOPPED_SPEAKING: Clear bot speaking flag
        
        Turn Management:
        - Sends interrupt signal when user interrupts bot (speaks during bot speaking)
        - Session's interrupt handler increments turn_id for all interrupts
        - TTS station increments turn_id when bot finishes
        """
        # Buffer audio chunks when collecting (only in buffer mode)
        if chunk.type == ChunkType.AUDIO_RAW:
            if self.buffer_audio and self._is_collecting_audio:
                self._audio_buffer.append(chunk)
                self.logger.debug(f"Buffered audio chunk, total: {len(self._audio_buffer)}")
            # Don't passthrough audio
            return
            yield  # Makes this an async generator
        
        # Bot speaking state tracking
        if chunk.type == ChunkType.EVENT_BOT_STARTED_SPEAKING:
            self._bot_is_speaking = True
            self.logger.debug("Bot started speaking")
            return
            yield  # Makes this an async generator
        
        elif chunk.type == ChunkType.EVENT_BOT_STOPPED_SPEAKING:
            self._bot_is_speaking = False
            self.logger.debug("Bot stopped speaking")
            return
            yield  # Makes this an async generator
        
        # Voice started - start collecting audio (if buffering), check for interrupt
        elif chunk.type == ChunkType.EVENT_VAD_START:
            # Start collecting audio (only in buffer mode)
            if self.buffer_audio:
                self._is_collecting_audio = True
                self.logger.debug("Started collecting audio")
            
            # Cancel pending turn end
            if self._should_emit_turn_end:
                self.logger.debug("Turn end cancelled (voice resumed)")
                self._should_emit_turn_end = False
                self._waiting_session_id = None
            
            # Check if should send interrupt (user speaking during bot speaking)
            if self.interrupt_enabled and self._bot_is_speaking and self.control_bus:
                self.logger.warning("User interrupt detected (speaking during bot speaking)")
                
                # Send interrupt signal via ControlBus
                await self.control_bus.send_interrupt(
                    source=self.name,
                    reason="user_speaking_during_bot_speaking",
                    metadata={"session_id": chunk.session_id}
                )
            return
            yield  # Makes this an async generator
        
        # Voice ended - wait for silence then emit completion signal (+ audio if buffering)
        elif chunk.type == ChunkType.EVENT_VAD_END:
            # Record T0: user_speech_end (VAD_END received)
            self._latency_monitor.record(
                chunk.session_id,
                chunk.turn_id,
                "user_speech_end",
                chunk.timestamp
            )
            
            # Set flag to emit completion
            self._should_emit_turn_end = True
            self._waiting_session_id = chunk.session_id
            waiting_turn_id = chunk.turn_id
            
            # Wait for silence threshold
            try:
                await asyncio.sleep(self.silence_threshold)
                
                # If flag still set (not cancelled), emit completion signal
                if self._should_emit_turn_end and self._waiting_session_id == chunk.session_id:
                    if self.buffer_audio:
                        self.logger.info(f"Turn ended after {self.silence_threshold:.2f}s silence, outputting {len(self._audio_buffer)} buffered audio chunks")
                    else:
                        self.logger.info(f"Turn ended after {self.silence_threshold:.2f}s silence")
                    
                    # Record T1: turn_end_detected
                    self._latency_monitor.record(
                        chunk.session_id,
                        waiting_turn_id,
                        "turn_end_detected"
                    )
                    
                    # Output buffered audio chunks (only in buffer mode)
                    if self.buffer_audio:
                        for audio_chunk in self._audio_buffer:
                            # Update source to this station
                            audio_chunk.source = self.name
                            yield audio_chunk
                        
                        # Clear buffer after output
                        self._audio_buffer.clear()
                        self._is_collecting_audio = False
                    
                    # Emit completion event (triggers downstream ASR)
                    yield EventChunk(
                        type=ChunkType.EVENT_STREAM_COMPLETE,
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=waiting_turn_id,
                        event_data={"silence_duration": self.silence_threshold}
                    )
            
            except asyncio.CancelledError:
                self.logger.debug("Turn detector sleep cancelled")
                raise
            
            finally:
                self._should_emit_turn_end = False
                self._waiting_session_id = None
            
            return
            yield  # Makes this an async generator
        
        # Reset on interrupt (from ControlBus via middleware)
        elif chunk.type == ChunkType.CONTROL_STATE_RESET:
            self._should_emit_turn_end = False
            self._waiting_session_id = None
            self._bot_is_speaking = False
            # Clear audio buffer on interrupt (only in buffer mode)
            if self.buffer_audio and self._audio_buffer:
                self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks on interrupt")
                self._audio_buffer.clear()
            self._is_collecting_audio = False
            self.logger.debug("Turn detector reset by interrupt")
            return
            yield  # Makes this an async generator
    
    async def reset_state(self) -> None:
        """Reset turn detector state for new turn."""
        await super().reset_state()
        self._should_emit_turn_end = False
        self._waiting_session_id = None
        self._bot_is_speaking = False
        # Clear audio buffer on state reset (only in buffer mode)
        if self.buffer_audio and self._audio_buffer:
            self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks on state reset")
            self._audio_buffer.clear()
        self._is_collecting_audio = False
        self.logger.debug("Turn detector state reset")
