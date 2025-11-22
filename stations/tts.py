"""
TTSStation - Text to Speech

Input: TEXT, TEXT_DELTA
Output: AUDIO_ENCODED (streaming) + EVENT_TTS_START/STOP
"""

from typing import AsyncIterator
from vixio.core.station import Station
from vixio.core.chunk import Chunk, ChunkType, AudioChunk, EventChunk, is_text_chunk
from vixio.providers.tts import TTSProvider


class TTSStation(Station):
    """
    TTS workstation: Synthesizes text to audio.
    
    Input: TEXT, TEXT_DELTA
    Output: AUDIO_ENCODED (streaming) + EVENT_TTS_START/STOP
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
        - On TEXT/TEXT_DELTA: Synthesize to audio, yield AUDIO_ENCODED chunks
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
            
            # Passthrough signals
            yield chunk
            return
        
        # Process text chunks
        if is_text_chunk(chunk):
            # Extract text content
            if hasattr(chunk, 'content'):
                text = chunk.content
            elif hasattr(chunk, 'delta'):
                text = chunk.delta
            else:
                text = str(chunk.data) if chunk.data else ""
            
            if not text or not text.strip():
                self.logger.debug("Skipping empty text for TTS")
                yield chunk  # Passthrough
                return
            
            self.logger.info(f"TTS synthesizing: '{text[:50]}...'")
            
            # Emit TTS start event (first time)
            if not self._is_speaking:
                yield EventChunk(
                    type=ChunkType.EVENT_TTS_START,
                    event_data={"text_length": len(text)},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                self._is_speaking = True
            
            # Synthesize text to audio (streaming)
            try:
                audio_count = 0
                async for audio_data in self.tts.synthesize(text):
                    if audio_data:
                        audio_count += 1
                        yield AudioChunk(
                            type=ChunkType.AUDIO_ENCODED,
                            data=audio_data,
                            sample_rate=16000,  # Note: Edge TTS returns MP3, may need conversion
                            channels=1,
                            session_id=chunk.session_id
                        )
                
                self.logger.debug(f"TTS generated {audio_count} audio chunks")
                
                # Emit TTS stop event
                if self._is_speaking:
                    yield EventChunk(
                        type=ChunkType.EVENT_TTS_STOP,
                        event_data={"audio_chunks": audio_count},
                        source_station=self.name,
                        session_id=chunk.session_id
                    )
                    self._is_speaking = False
            
            except Exception as e:
                self.logger.error(f"TTS synthesis failed: {e}", exc_info=True)
                
                # Emit error event
                yield EventChunk(
                    type=ChunkType.EVENT_ERROR,
                    event_data={"error": str(e), "source": "TTS"},
                    source_station=self.name,
                    session_id=chunk.session_id
                )
                
                self._is_speaking = False
            
            # Passthrough original text chunk
            yield chunk
        else:
            # Passthrough non-text data
            yield chunk
