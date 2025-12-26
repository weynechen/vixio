"""
TurnDetectorStation - Detect when user finishes speaking and handle interrupts

Input: 
  - AUDIO_DELTA (passthrough from VAD, for streaming ASR)
  - AUDIO (merged segments from VAD)
  - EVENT_VAD_START/END (from VAD)
  - EVENT_BOT_STARTED/STOPPED_SPEAKING (from TTS)

Output: 
  - AUDIO_DELTA (passthrough for streaming ASR)
  - AUDIO (merged turn when turn ends, for batch ASR)
  - (No EVENT_STREAM_COMPLETE - ASR handles that)

Data Flow:
- VAD outputs AUDIO_DELTA (passthrough) and AUDIO (merged segments)
- TurnDetector passthrough AUDIO_DELTA (for StreamingASR)
- TurnDetector collects AUDIO segments
- On VAD_END, waits for silence threshold
- If silence continues, outputs all collected segments as one AUDIO to ASR
- If voice resumes (VAD_START), continues collecting

Future: Can be replaced with SmartTurn for semantic turn detection.

Note: Sends interrupt signal to ControlBus when user speaks during bot speaking.
Session's interrupt handler injects CONTROL_STATE_RESET into pipeline.

Completion Contract:
- AWAITS_COMPLETION: False (listens to VAD events, not completion)
- EMITS_COMPLETION: False (ASR emits completion after transcription)
"""

import asyncio
import time
from collections.abc import AsyncIterator, AsyncGenerator
from typing import Optional, List, cast
from vixio.core.station import DetectorStation
from vixio.core.chunk import Chunk, ChunkType, EventChunk, AudioChunk
from vixio.utils import get_latency_monitor


class TurnDetectorStation(DetectorStation):
    """
    Turn detector: Detects when user finishes speaking and handles interrupts.
    
    Input: 
      - AUDIO_DELTA (passthrough from VAD)
      - AUDIO (merged segments from VAD)
      - EVENT_VAD_* + EVENT_BOT_*
    Output: 
      - AUDIO_DELTA (passthrough for StreamingASR)
      - AUDIO (merged turn for batch ASR)
    
    Data Flow:
    - Receives AUDIO_DELTA (passthrough) and AUDIO (segments) from VAD
    - Passthrough AUDIO_DELTA for StreamingASR downstream
    - Collects AUDIO segments (may span multiple VAD segments)
    - On VAD_END, waits for silence threshold
    - If silence continues, outputs all collected segments as one AUDIO → ASR
    - If voice resumes (VAD_START), continues collecting
    
    Strategy:
    - Passthrough AUDIO_DELTA immediately
    - Collect AUDIO segments from VAD
    - Wait inline after VAD_END for silence threshold
    - If silence continues, emit merged AUDIO
    - If voice resumes, cancel and continue collecting
    - Track bot speaking state for interrupt detection
    
    Completion Contract:
    - Does NOT emit completion (ASR handles that after transcription)
    - Only outputs collected audio when turn is determined to be complete
    """
    
    # DetectorStation configuration
    ALLOWED_INPUT_TYPES = [
        ChunkType.AUDIO_DELTA,      # Passthrough for streaming ASR
        ChunkType.AUDIO,   # Segments from VAD
        ChunkType.EVENT_VAD_START,
        ChunkType.EVENT_VAD_END,
        ChunkType.EVENT_BOT_STARTED_SPEAKING,
        ChunkType.EVENT_BOT_STOPPED_SPEAKING
    ]
    
    # Completion contract: ASR emits completion, not TurnDetector
    EMITS_COMPLETION = False
    AWAITS_COMPLETION = False
    
    def __init__(
        self,
        silence_threshold_ms: int = 100,
        interrupt_enabled: bool = True,
        name: str = "turn_detector"
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
        
        # State
        self._should_emit_turn_end = False
        self._waiting_session_id: Optional[str] = None
        self._bot_is_speaking = False
        
        # Audio collection (merged segments from VAD)
        self._audio_segments: List[AudioChunk] = []
        
        # Latency monitoring
        self._latency_monitor = get_latency_monitor()
        
        self.logger.info(f"TurnDetector initialized (silence_threshold={silence_threshold_ms}ms)")
    
    async def reset_state(self) -> None:
        """Reset turn detector state for new turn."""
        await super().reset_state()
        self._should_emit_turn_end = False
        self._waiting_session_id = None
        self._bot_is_speaking = False
        
        if self._audio_segments:
            self.logger.debug(f"Clearing {len(self._audio_segments)} audio segments on state reset")
            self._audio_segments.clear()
        
        self.logger.debug("Turn detector state reset")
    
    def _merge_audio_segments(self, session_id: str, turn_id: int) -> Optional[AudioChunk]:
        """
        Merge all collected audio segments into a single AudioChunk.
        
        Returns:
            Merged AudioChunk (AUDIO) or None if no segments
        """
        if not self._audio_segments:
            return None
        
        # Merge all segment data
        merged_audio = b''.join(seg.data for seg in self._audio_segments)
        sample_rate = self._audio_segments[0].sample_rate if self._audio_segments else 16000
        channels = self._audio_segments[0].channels if self._audio_segments else 1
        
        return AudioChunk(
            type=ChunkType.AUDIO,  # Output complete turn
            data=merged_audio,
            source=self.name,
            session_id=session_id,
            turn_id=turn_id,
            sample_rate=sample_rate,
            channels=channels
        )
    
    async def process_chunk(self, chunk: Chunk) -> AsyncGenerator[Chunk, None]:
        """
        Process chunk for turn detection and interrupt handling.
        
        Logic:
        - On AUDIO_DELTA: Passthrough (for StreamingASR)
        - On AUDIO: Collect segment from VAD
        - On EVENT_VAD_START: Check for interrupt, cancel pending turn end
        - On EVENT_VAD_END: Wait for silence, then output collected audio
        - On EVENT_BOT_*: Track bot speaking state
        """
        # Passthrough AUDIO_DELTA for StreamingASR
        if chunk.type == ChunkType.AUDIO_DELTA:
            # Passthrough immediately for streaming ASR downstream
            yield chunk
            return
        
        # Collect AUDIO segments from VAD
        if chunk.type is ChunkType.AUDIO:
            self._audio_segments.append(cast(AudioChunk, chunk))
            self.logger.debug(f"Collected audio segment ({len(chunk.data)} bytes), total segments: {len(self._audio_segments)}")
            return
            yield  # Makes this an async generator
        
        # Bot speaking state tracking
        if chunk.type == ChunkType.EVENT_BOT_STARTED_SPEAKING:
            self._bot_is_speaking = True
            self.logger.debug("Bot started speaking")
            return
            yield
        
        elif chunk.type == ChunkType.EVENT_BOT_STOPPED_SPEAKING:
            self._bot_is_speaking = False
            self.logger.debug("Bot stopped speaking")
            return
            yield
        
        # Voice started - cancel pending turn end, check for interrupt
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
            yield
        
        # Voice ended - wait for silence then output collected audio
        elif chunk.type == ChunkType.EVENT_VAD_END:
            # Record T0: user_speech_end (VAD_END received)
            self._latency_monitor.record(
                chunk.session_id,
                chunk.turn_id,
                "user_speech_end",
                chunk.timestamp
            )
            
            # Set flag to emit turn end
            self._should_emit_turn_end = True
            self._waiting_session_id = chunk.session_id
            waiting_turn_id = chunk.turn_id
            
            # Wait for silence threshold
            try:
                await asyncio.sleep(self.silence_threshold)
                
                # If flag still set (not cancelled), emit collected audio
                if self._should_emit_turn_end and self._waiting_session_id == chunk.session_id:
                    self.logger.info(f"Turn ended after {self.silence_threshold:.2f}s silence")
                    
                    # Record T1: turn_end_detected
                    self._latency_monitor.record(
                        chunk.session_id,
                        waiting_turn_id,
                        "turn_end_detected"
                    )
                    
                    # Output merged audio
                    merged_audio = self._merge_audio_segments(chunk.session_id, waiting_turn_id)
                    if merged_audio:
                        self.logger.info(f"Outputting {len(merged_audio.data)} bytes merged audio to ASR")
                        yield merged_audio
                    else:
                        self.logger.warning("No audio segments collected for turn")
                    
                    # Clear collected segments
                    self._audio_segments.clear()
            
            except asyncio.CancelledError:
                self.logger.debug("Turn detector sleep cancelled")
                raise
            
            finally:
                self._should_emit_turn_end = False
                self._waiting_session_id = None
            
            return
            yield
        
        # Reset on interrupt (from ControlBus via middleware)
        elif chunk.type == ChunkType.CONTROL_STATE_RESET:
            self._should_emit_turn_end = False
            self._waiting_session_id = None
            self._bot_is_speaking = False
            
            if self._audio_segments:
                self.logger.debug(f"Clearing {len(self._audio_segments)} audio segments on interrupt")
                self._audio_segments.clear()
            
            self.logger.debug("Turn detector reset by interrupt")
            return
            yield
