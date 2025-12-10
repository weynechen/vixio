"""
TurnDetectorStation - Detect when user finishes speaking and handle interrupts

Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING
Output: CompletionSignal (after silence threshold, triggers ASR)

Note: Sends interrupt signal to ControlBus when user speaks during bot speaking.
Session's interrupt handler injects CONTROL_STATE_RESET into pipeline.

Completion Contract:
- AWAITS_COMPLETION: False (listens to VAD events, not completion)
- EMITS_COMPLETION: True (emits completion when turn ends, triggers ASR)
"""

import asyncio
import time
from typing import AsyncIterator, Optional
from vixio.core.station import DetectorStation, StationRole
from vixio.core.chunk import Chunk, ChunkType, EventChunk, ControlChunk, CompletionChunk, CompletionSignal
from vixio.utils import get_latency_monitor


class TurnDetectorStation(DetectorStation):
    """
    Turn detector: Detects when user finishes speaking and handles interrupts.
    
    Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING, EVENT_BOT_STOPPED_SPEAKING
    Output: CompletionSignal (after silence threshold, triggers downstream ASR)
    
    Strategy:
    - Wait inline after VAD_END for silence threshold (using cancellable sleep)
    - If silence continues, emit CompletionSignal (triggers ASR)
    - If voice resumes (VAD_START) or interrupted, set cancel flag to suppress
    - Track session state (LISTENING vs SPEAKING)
    - Send interrupt signal when user speaks during bot speaking
    
    Turn Management:
    - Sends interrupt signal to ControlBus when user interrupts (speaks during bot speaking)
    - Session's interrupt handler increments turn_id for all interrupts
    - TTS station increments turn_id when bot finishes speaking
    - This ensures turn_id changes immediately and consistently on completion/interrupt
    
    Completion Contract:
    - Does NOT await completion (listens to VAD state events)
    - Emits completion when turn ends (triggers ASR via CompletionSignal)
    
    Note: This station doesn't use middlewares due to its complex state machine logic,
    async timers, and conditional branching. It inherits DetectorStation for classification
    but implements its own process_chunk without middleware decoration.
    """
    
    # Station role
    ROLE = StationRole.DETECTOR
    
    # DetectorStation configuration
    # TurnDetector processes VAD/BOT events, not audio data
    ALLOWED_INPUT_TYPES = [
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
        name: str = "TurnDetector"
    ):
        """
        Initialize turn detector station.
        
        Args:
            silence_threshold_ms: Silence duration to consider turn ended (default: 100ms)
            interrupt_enabled: Whether to enable interrupt detection (default: True)
            name: Station name
        """
        super().__init__(name=name)
        self.silence_threshold = silence_threshold_ms / 1000.0  # Convert to seconds
        self.interrupt_enabled = interrupt_enabled
        self._should_emit_turn_end = False
        self._waiting_session_id: Optional[str] = None
        self._bot_is_speaking = False  # Track if bot is currently speaking
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk for turn detection and interrupt handling.
        
        DAG routing rules:
        - Only process chunks matching ALLOWED_INPUT_TYPES (VAD/BOT events)
        - Do NOT passthrough - DAG handles routing to downstream nodes
        - Output: CompletionSignal (triggers ASR)
        
        Logic:
        - On EVENT_VAD_START: Check if user interrupted (bot speaking)
          * If yes: send interrupt signal to ControlBus
          * Cancel pending turn end if any
        - On EVENT_VAD_END: Wait for silence threshold, emit TURN_END (if not cancelled)
        - On EVENT_BOT_STARTED_SPEAKING: Set bot speaking flag
        - On EVENT_BOT_STOPPED_SPEAKING: Clear bot speaking flag
        
        Turn Management:
        - Sends interrupt signal when user interrupts bot (speaks during bot speaking)
        - Session's interrupt handler increments turn_id for all interrupts
        - TTS station increments turn_id when bot finishes
        """
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
        
        # Voice started - check for interrupt
        elif chunk.type == ChunkType.EVENT_VAD_START:
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
        
        # Voice ended - wait for silence then emit completion signal
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
                    self.logger.info(f"Turn ended after {self.silence_threshold:.2f}s silence")
                    
                    # Record T1: turn_end_detected
                    self._latency_monitor.record(
                        chunk.session_id,
                        waiting_turn_id,
                        "turn_end_detected"
                    )
                    
                    # Emit completion signal (triggers downstream ASR)
                    yield CompletionChunk(
                        signal=CompletionSignal.COMPLETE,
                        from_station=self.name,
                        source=self.name,
                        session_id=chunk.session_id,
                        turn_id=waiting_turn_id,
                        metadata={"silence_duration": self.silence_threshold}
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
            self.logger.debug("Turn detector reset by interrupt")
            return
            yield  # Makes this an async generator
    
    async def reset_state(self) -> None:
        """Reset turn detector state for new turn."""
        await super().reset_state()
        self._should_emit_turn_end = False
        self._waiting_session_id = None
        self._bot_is_speaking = False
        self.logger.debug("Turn detector state reset")
