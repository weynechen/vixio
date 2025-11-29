"""
TurnDetectorStation - Detect when user finishes speaking and handle interrupts

Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING
Output: EVENT_TURN_END (after silence threshold)

Note: Sends interrupt signal to ControlBus when user speaks during bot speaking.
Session's interrupt handler injects CONTROL_INTERRUPT into pipeline.
"""

import asyncio
import time
from typing import AsyncIterator, Optional
from core.station import DetectorStation
from core.chunk import Chunk, ChunkType, EventChunk, ControlChunk
from utils import get_latency_monitor


class TurnDetectorStation(DetectorStation):
    """
    Turn detector: Detects when user finishes speaking and handles interrupts.
    
    Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING, EVENT_BOT_STOPPED_SPEAKING
    Output: EVENT_TURN_END (after silence threshold)
    
    Strategy:
    - Wait inline after VAD_END for silence threshold (using cancellable sleep)
    - If silence continues, emit TURN_END
    - If voice resumes (VAD_START) or interrupted, set cancel flag to suppress TURN_END
    - Track session state (LISTENING vs SPEAKING)
    - Send interrupt signal when user speaks during bot speaking
    
    Turn Management:
    - Sends interrupt signal to ControlBus when user interrupts (speaks during bot speaking)
    - Session's interrupt handler increments turn_id for all interrupts
    - TTS station increments turn_id when bot finishes speaking
    - This ensures turn_id changes immediately and consistently on completion/interrupt
    
    Note: This station doesn't use middlewares due to its complex state machine logic,
    async timers, and conditional branching. It inherits DetectorStation for classification
    but implements its own process_chunk without middleware decoration.
    """
    
    # DetectorStation configuration
    # TurnDetector processes VAD/BOT events, not audio data
    ALLOWED_INPUT_TYPES = [
        ChunkType.EVENT_VAD_START,
        ChunkType.EVENT_VAD_END,
        ChunkType.EVENT_BOT_STARTED_SPEAKING,
        ChunkType.EVENT_BOT_STOPPED_SPEAKING
    ]
    
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
        
        Logic:
        - On EVENT_VAD_START: Check if user interrupted (bot speaking)
          * If yes: send interrupt signal to ControlBus (Session will handle turn increment)
          * Cancel pending turn end if any
        - On EVENT_VAD_END: Wait for silence threshold, emit TURN_END (if not cancelled)
        - On EVENT_BOT_STARTED_SPEAKING: Set bot speaking flag
        - On EVENT_BOT_STOPPED_SPEAKING: Clear bot speaking flag
        - On CONTROL_INTERRUPT: Cancel waiting and reset
        
        Turn Management:
        - Sends interrupt signal when user interrupts bot (speaks during bot speaking)
        - Session's interrupt handler increments turn_id for all interrupts
        - TTS station increments turn_id when bot finishes
        """
        # Handle signals
        if chunk.is_signal():
            # Bot speaking state tracking
            if chunk.type == ChunkType.EVENT_BOT_STARTED_SPEAKING:
                self._bot_is_speaking = True
                self.logger.debug("Bot started speaking")
                yield chunk
                return
            
            elif chunk.type == ChunkType.EVENT_BOT_STOPPED_SPEAKING:
                self._bot_is_speaking = False
                self.logger.debug("Bot stopped speaking")
                yield chunk
                return
            
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
                    # Session will handle turn increment for all interrupts
                    await self.control_bus.send_interrupt(
                        source=self.name,
                        reason="user_speaking_during_bot_speaking",
                        metadata={"session_id": chunk.session_id}
                    )
                    
                    # Note: Don't emit CONTROL_INTERRUPT chunk here anymore
                    # Session's interrupt handler will send it after incrementing turn
                    # This ensures proper turn_id synchronization
                
                # Passthrough VAD_START
                yield chunk
                return
            
            # Voice ended - wait for silence then emit turn end
            elif chunk.type == ChunkType.EVENT_VAD_END:
                # Wait for silence threshold and emit TURN_END
                # Turn increment happens when bot finishes (TTS) or user interrupts (above)
                
                # Record T0: user_speech_end (VAD_END received)
                self._latency_monitor.record(
                    chunk.session_id,
                    chunk.turn_id,
                    "user_speech_end",
                    chunk.timestamp
                )
                
                # Passthrough VAD_END first
                yield chunk
                
                # Set flag to emit TURN_END
                self._should_emit_turn_end = True
                self._waiting_session_id = chunk.session_id
                waiting_turn_id = chunk.turn_id  # Capture turn_id for TURN_END event
                
                # Wait for silence threshold
                try:
                    await asyncio.sleep(self.silence_threshold)
                    
                    # If flag still set (not cancelled), emit TURN_END
                    if self._should_emit_turn_end and self._waiting_session_id == chunk.session_id:
                        self.logger.info(f"Turn ended after {self.silence_threshold:.2f}s silence")
                        
                        # Record T1: turn_end_detected (TURN_END emitted)
                        self._latency_monitor.record(
                            chunk.session_id,
                            waiting_turn_id,
                            "turn_end_detected"
                        )
                        
                        yield EventChunk(
                            type=ChunkType.EVENT_TURN_END,
                            event_data={"silence_duration": self.silence_threshold},
                            source=self.name,
                            session_id=chunk.session_id,
                            turn_id=waiting_turn_id
                        )
                
                except asyncio.CancelledError:
                    # Sleep was cancelled externally
                    self.logger.debug("Turn detector sleep cancelled")
                    raise
                
                finally:
                    # Clear state
                    self._should_emit_turn_end = False
                    self._waiting_session_id = None
                
                return
            
            # Reset on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._should_emit_turn_end = False
                self._waiting_session_id = None
                self._bot_is_speaking = False
                self.logger.debug("Turn detector reset by interrupt")
            
            # Passthrough all signals
            yield chunk
            return
        
        # Passthrough all data chunks
        yield chunk
    
    async def reset_state(self) -> None:
        """Reset turn detector state for new turn."""
        await super().reset_state()
        self._should_emit_turn_end = False
        self._waiting_session_id = None
        self._bot_is_speaking = False
        self.logger.debug("Turn detector state reset")
