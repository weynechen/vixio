"""
TTSStation - Text to Speech

Input: TEXT (complete sentences, source="agent")
Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP

Note: This station only processes TEXT chunks with source="agent".
Use SentenceSplitterStation before this to convert TEXT_DELTA to TEXT.
"""

from typing import AsyncIterator
from core.station import Station
from core.chunk import Chunk, ChunkType, AudioChunk, EventChunk, is_text_chunk
from providers.tts import TTSProvider


class TTSStation(Station):
    """
    TTS workstation: Synthesizes text to audio.
    
    Input: TEXT (complete sentences, source="agent")
    Output: AUDIO_RAW (streaming) + EVENT_TTS_START/STOP
    
    Note: Only processes TEXT chunks with source="agent".
    Use SentenceSplitterStation to convert TEXT_DELTA to TEXT.
    """
    
    def __init__(self, tts_provider: TTSProvider, name: str = "TTS"):
        """
        Initialize TTS station.
        
        Args:
            tts_provider: TTS provider instance
            name: Station name
        """
        super().__init__(name=name)
        self.tts = tts_provider
        self._is_speaking = False
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through TTS.
        
        Logic:
        - On TEXT: Synthesize to audio, yield AUDIO_RAW chunks
        - On TEXT_DELTA: Passthrough (let SentenceSplitter handle it)
        - Emit EVENT_TTS_START before first audio
        - Emit EVENT_TTS_STOP after last audio
        - On CONTROL_INTERRUPT: Cancel synthesis
        """
        # Handle signals
        if chunk.is_signal():
            # Stop speaking on interrupt
            if chunk.type == ChunkType.CONTROL_INTERRUPT:
                if self._is_speaking:
                    self.tts.cancel()
                    
                    yield EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"reason": "user_interrupt"},
                        source_station=self.name,
                        session_id=chunk.session_id
                    )
                    
                    self._is_speaking = False
                    self.logger.info("TTS cancelled by interrupt")
            
            # Stop speaking when Agent completes (last sentence done)
            elif chunk.type == ChunkType.EVENT_AGENT_STOP:
                if self._is_speaking:
                    self.logger.info("TTS session complete (agent stopped)")
                    yield EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"reason": "agent_complete"},
                        source_station=self.name,
                        session_id=chunk.session_id
                    )
                    self._is_speaking = False
            
            # Passthrough signals
            yield chunk
            return
        
        # Only process TEXT chunks from Agent (complete sentences)
        # Check both type and source
        if chunk.type == ChunkType.TEXT:
            # Check if source is "agent"
            if chunk.source != "agent":
                self.logger.debug(f"Skipping TEXT from source='{chunk.source}' (not agent)")
                yield chunk  # Passthrough
                return
            
            # Extract text content
            if hasattr(chunk, 'content'):
                text = chunk.content
            else:
                text = str(chunk.data) if chunk.data else ""
            
            if not text or not text.strip():
                self.logger.debug("Skipping empty text for TTS")
                yield chunk  # Passthrough
                return
            
            self.logger.info(f"TTS synthesizing: '{text[:50]}...'")
            
            # Emit TTS start event (first time only)
            if not self._is_speaking:
                yield EventChunk(
                    type=ChunkType.EVENT_TTS_START,
                    event_data={"text_length": len(text)},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                self._is_speaking = True
            
            # Emit sentence start event with text (for LLM output display on client)
            yield EventChunk(
                type=ChunkType.EVENT_TTS_SENTENCE_START,
                event_data={"text": text},
                source_station=self.name,
                session_id=chunk.session_id
            )
            
            # Synthesize text to audio (streaming)
            try:
                audio_count = 0
                async for audio_data in self.tts.synthesize(text):
                    if audio_data:
                        audio_count += 1
                        # TTS provider returns PCM audio (complete sentence now)
                        yield AudioChunk(
                            type=ChunkType.AUDIO_RAW,
                            data=audio_data,
                            sample_rate=16000,
                            channels=1,
                            source=self.name,  # Mark as from TTS station for flow control
                            session_id=chunk.session_id
                        )
                
                self.logger.debug(f"TTS generated {audio_count} audio chunks (sentences)")
                
                # Note: TTS STOP event will be sent when receiving EVENT_AGENT_STOP
                # This ensures STOP is only sent after the last sentence
            
            except Exception as e:
                self.logger.error(f"TTS synthesis failed: {e}", exc_info=True)
                
                # Emit error event
                yield EventChunk(
                    type=ChunkType.EVENT_ERROR,
                    event_data={"error": str(e), "source": "TTS"},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                
                # Send TTS stop on error
                if self._is_speaking:
                    yield EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"reason": "error"},
                        source_station=self.name,
                        session_id=chunk.session_id
                    )
                    self._is_speaking = False
            
            # Passthrough original text chunk
            yield chunk
        else:
            # Passthrough non-text data
            yield chunk
