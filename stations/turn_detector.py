"""
TurnDetectorStation - Detect when user finishes speaking

Input: EVENT_VAD_END
Output: EVENT_TURN_END (after silence threshold)
"""

import asyncio
import time
from typing import AsyncIterator, Optional
from vixio.core.station import Station
from vixio.core.chunk import Chunk, ChunkType, EventChunk


class TurnDetectorStation(Station):
    """
    Turn detector: Detects when user finishes speaking.
    
    Input: EVENT_VAD_END, AUDIO_RAW
    Output: EVENT_TURN_END (after silence threshold) + passthroughs
    
    Strategy:
    - Wait inline after VAD_END for silence threshold (using cancellable sleep)
    - If silence continues, emit TURN_END
    - If voice resumes (VAD_START) or interrupted, set cancel flag to suppress TURN_END
    """
    
    def __init__(
        self,
        silence_threshold_ms: int = 800,
        name: str = "TurnDetector"
    ):
        """
        Initialize turn detector station.
        
        Args:
            silence_threshold_ms: Silence duration to consider turn ended (default: 800ms)
            name: Station name
        """
        super().__init__(name=name)
        self.silence_threshold = silence_threshold_ms / 1000.0  # Convert to seconds
        self._should_emit_turn_end = False
        self._waiting_session_id: Optional[str] = None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk for turn detection.
        
        Logic:
        - On EVENT_VAD_END: Passthrough, wait for silence threshold, emit TURN_END (if not cancelled)
        - On EVENT_VAD_START: Cancel waiting (set flag to suppress TURN_END)
        - On CONTROL_INTERRUPT: Cancel waiting and reset
        """
        # Handle signals
        if chunk.is_signal():
            # Voice ended - wait for silence then emit turn end
            if chunk.type == ChunkType.EVENT_VAD_END:
                # Passthrough VAD_END first
                yield chunk
                
                # Set flag to emit TURN_END
                self._should_emit_turn_end = True
                self._waiting_session_id = chunk.session_id
                
                # Wait for silence threshold
                try:
                    await asyncio.sleep(self.silence_threshold)
                    
                    # If flag still set (not cancelled), emit TURN_END
                    if self._should_emit_turn_end and self._waiting_session_id == chunk.session_id:
                        self.logger.info(f"Turn ended after {self.silence_threshold:.2f}s silence")
                        
                        yield EventChunk(
                            type=ChunkType.EVENT_TURN_END,
                            event_data={"silence_duration": self.silence_threshold},
                            source_station=self.name,
                            session_id=chunk.session_id
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
            
            # Voice started - cancel waiting
            elif chunk.type == ChunkType.EVENT_VAD_START:
                if self._should_emit_turn_end:
                    self.logger.debug("Turn end cancelled (voice resumed)")
                    self._should_emit_turn_end = False
                    self._waiting_session_id = None
            
            # Reset on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                self._should_emit_turn_end = False
                self._waiting_session_id = None
                self.logger.debug("Turn detector reset by interrupt")
            
            # Passthrough all signals
            yield chunk
            return
        
        # Passthrough all data chunks
        yield chunk
