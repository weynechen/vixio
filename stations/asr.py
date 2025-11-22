"""
ASRStation - Speech to Text

Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
Output: TEXT (transcription result)
"""

from typing import AsyncIterator, List
from core.station import Station
from core.chunk import Chunk, ChunkType, TextChunk, is_audio_chunk
from providers.asr import ASRProvider


class ASRStation(Station):
    """
    ASR workstation: Transcribes audio to text.
    
    Input: AUDIO_RAW (collect), EVENT_TURN_END (trigger)
    Output: TEXT (transcription result)
    """
    
    def __init__(self, asr_provider: ASRProvider, name: str = "ASR"):
        """
        Initialize ASR station.
        
        Args:
            asr_provider: ASR provider instance
            name: Station name
        """
        super().__init__(name=name)
        self.asr = asr_provider
        self._audio_buffer: List[bytes] = []
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        Process chunk through ASR.
        
        Logic:
        - Collect AUDIO_RAW chunks into buffer
        - On EVENT_TURN_END: Transcribe buffered audio, yield TEXT, clear buffer
        - On CONTROL_INTERRUPT: Clear buffer
        """
        # Handle signals
        if chunk.is_signal():
            # Passthrough signal first (important for downstream)
            yield chunk
            
            # Then process side effects
            # Transcribe when turn ends
            if chunk.type == ChunkType.EVENT_TURN_END:
                if self._audio_buffer:
                    self.logger.info(f"Transcribing {len(self._audio_buffer)} audio chunks...")
                    
                    try:
                        text = await self.asr.transcribe(self._audio_buffer)
                        
                        if text:
                            self.logger.info(f"ASR result: '{text}'")
                            yield TextChunk(
                                type=ChunkType.TEXT,
                                content=text,
                                session_id=chunk.session_id
                            )
                        else:
                            self.logger.warning("ASR returned empty text")
                    
                    except Exception as e:
                        self.logger.error(f"ASR transcription failed: {e}", exc_info=True)
                    
                    finally:
                        # Clear buffer regardless of success/failure
                        self._audio_buffer.clear()
                else:
                    self.logger.warning("EVENT_TURN_END received but no audio in buffer")
            
            # Clear buffer on interrupt
            elif chunk.type == ChunkType.CONTROL_INTERRUPT:
                if self._audio_buffer:
                    self.logger.debug(f"Clearing {len(self._audio_buffer)} buffered audio chunks")
                    self._audio_buffer.clear()
                self.asr.reset()
            
            return
        
        # Collect audio for later transcription
        if is_audio_chunk(chunk) and chunk.type == ChunkType.AUDIO_RAW:
            if chunk.data:
                self._audio_buffer.append(chunk.data)
                self.logger.debug(f"Buffered audio chunk, total: {len(self._audio_buffer)}")
            
            # Passthrough audio for downstream (e.g., echo)
            yield chunk
        else:
            # Passthrough other data types
            yield chunk
