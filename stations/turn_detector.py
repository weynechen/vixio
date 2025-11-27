"""
TurnDetectorStation - Detect when user finishes speaking and handle interrupts

Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING
Output: EVENT_TURN_END (after silence threshold), CONTROL_INTERRUPT (on valid interrupt)
"""

import asyncio
import time
from typing import AsyncIterator, Optional
from core.station import Station
from core.chunk import Chunk, ChunkType, EventChunk, ControlChunk
from utils import get_latency_monitor


class TurnDetectorStation(Station):
    """
    Turn detector: Detects when user finishes speaking and handles interrupts.
    
    Input: EVENT_VAD_END, EVENT_VAD_START, EVENT_BOT_STARTED_SPEAKING, EVENT_BOT_STOPPED_SPEAKING
    Output: EVENT_TURN_END (after silence threshold), CONTROL_INTERRUPT (on valid interrupt)
    
    Strategy:
    - Wait inline after VAD_END for silence threshold (using cancellable sleep)
    - If silence continues, emit TURN_END
    - If voice resumes (VAD_START) or interrupted, set cancel flag to suppress TURN_END
    - Track session state (LISTENING vs SPEAKING)
    - Send CONTROL_INTERRUPT when user speaks during bot speaking
    
    Turn Management:
    - Increments turn_id when user interrupts (speaks during bot speaking)
    - TTS station increments turn_id when bot finishes speaking
    - This ensures turn_id changes immediately on completion/interrupt
    """
    
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
          * If yes: increment turn_id immediately, send CONTROL_INTERRUPT
          * Cancel pending turn end if any
        - On EVENT_VAD_END: Wait for silence threshold, emit TURN_END (if not cancelled)
        - On EVENT_BOT_STARTED_SPEAKING: Set bot speaking flag
        - On EVENT_BOT_STOPPED_SPEAKING: Clear bot speaking flag
        - On CONTROL_INTERRUPT: Cancel waiting and reset
        
        Turn Management:
        - Increments turn_id immediately when user interrupts bot
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
                    
                    # Increment turn immediately - user interrupted bot
                    new_turn_id = await self.control_bus.increment_turn(
                        source=self.name,
                        reason="user_interrupted"
                    )
                    
                    # Update chunk's turn_id to new turn
                    chunk.turn_id = new_turn_id
                    
                    # Send interrupt signal via ControlBus
                    await self.control_bus.send_interrupt(
                        source=self.name,
                        reason="user_speaking_during_bot_speaking",
                        metadata={"session_id": chunk.session_id, "new_turn_id": new_turn_id}
                    )
                    
                    # Emit CONTROL_INTERRUPT chunk for pipeline
                    yield ControlChunk(
                        type=ChunkType.CONTROL_INTERRUPT,
                        command="interrupt",
                        params={"reason": "user_speaking"},
                        session_id=chunk.session_id,
                        turn_id=new_turn_id
                    )
                
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
                            source_station=self.name,
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
