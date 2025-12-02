"""
Transport base - bottom layer transport + Station provider

Responsibilities:
1. Connection management
2. Raw data read/write (internal)
3. Provide InputStation and OutputStation
"""

from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.station import Station


class TransportBase(ABC):
    """
    Transport base - provides InputStation and OutputStation.
    
    Instead of directly providing input_stream / output_chunk methods,
    provides two Stations that are automatically added to Pipeline by SessionManager.
    """
    
    @abstractmethod
    async def start(self) -> None:
        """Start transport service"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop transport service"""
        pass
    
    @abstractmethod
    def get_input_station(self, session_id: str) -> 'Station':
        """
        Get input Station.
        
        This Station serves as the first Station in Pipeline,
        responsible for reading data from connection and converting to Chunk stream.
        
        Args:
            session_id: Session ID
            
        Returns:
            InputStation instance (Station subclass)
        """
        pass
    
    @abstractmethod
    def get_output_station(self, session_id: str) -> 'Station':
        """
        Get output Station.
        
        This Station serves as the last Station in Pipeline,
        responsible for receiving Chunks and converting to protocol messages for sending.
        
        Args:
            session_id: Session ID
            
        Returns:
            OutputStation instance (Station subclass)
        """
        pass
    
    @abstractmethod
    def on_new_connection(self, handler: Callable[[str], Awaitable[None]]) -> None:
        """
        Register new connection callback.
        
        Args:
            handler: Async callback function, receives session_id
        """
        pass
    
    # Optional lifecycle hooks
    async def on_pipeline_ready(self, session_id: str) -> None:
        """
        Called when Pipeline is ready (e.g., send handshake message).
        
        Subclasses can override this method to implement handshake logic.
        """
        pass
