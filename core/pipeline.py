"""
Pipeline - assembly line that connects workstations (stations)

Design:
- Each station's output becomes the next station's input
- Chunks flow sequentially through all stations
- Data chunks get transformed at each station
- Signal chunks passthrough all stations, triggering state changes
"""

from typing import List, Optional, AsyncIterator
from core.station import Station
from core.chunk import Chunk
import logging

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Pipeline - assembly line that connects workstations (stations).
    
    Design:
    - Each station's output becomes the next station's input
    - Chunks flow sequentially through all stations
    - Data chunks get transformed at each station
    - Signal chunks passthrough all stations, triggering state changes
    """
    
    def __init__(self, stations: List[Station], name: Optional[str] = None):
        """
        Initialize pipeline.
        
        Args:
            stations: List of stations to chain together
            name: Pipeline name for logging
        """
        self.stations = stations
        self.name = name or "Pipeline"
        self.logger = logging.getLogger(f"pipeline.{self.name}")
        
        if not stations:
            self.logger.warning(f"[{self.name}] Created empty pipeline")
        else:
            station_names = [s.name for s in stations]
            self.logger.info(f"[{self.name}] Created with {len(stations)} stations: {' -> '.join(station_names)}")
    
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
        Run the pipeline - chain all stations together.
        
        Flow:
        1. Input stream feeds into first station
        2. Each station's output feeds into next station
        3. Final station's output is pipeline output
        
        Args:
            input_stream: Source of chunks (usually from Transport)
            
        Yields:
            Processed chunks from final station
        """
        stream = input_stream
        
        # Chain stations: station1.process(input) -> station2.process(station1_output) -> ...
        for station in self.stations:
            stream = station.process(stream)
        
        # Yield final output
        chunk_count = 0
        async for chunk in stream:
            chunk_count += 1
            self.logger.debug(f"[{self.name}] Output chunk #{chunk_count}: {chunk}")
            yield chunk
        
        self.logger.debug(f"[{self.name}] Completed processing {chunk_count} chunks")
    
    def add_station(self, station: Station, position: Optional[int] = None) -> None:
        """
        Add a station to the pipeline.
        
        Args:
            station: Station to add
            position: Position to insert (None = append to end)
        """
        if position is None:
            self.stations.append(station)
            self.logger.info(f"[{self.name}] Appended station: {station.name}")
        else:
            self.stations.insert(position, station)
            self.logger.info(f"[{self.name}] Inserted station at position {position}: {station.name}")
    
    def remove_station(self, station_name: str) -> bool:
        """
        Remove a station by name.
        
        Args:
            station_name: Name of station to remove
            
        Returns:
            True if station was found and removed
        """
        for i, station in enumerate(self.stations):
            if station.name == station_name:
                removed = self.stations.pop(i)
                self.logger.info(f"[{self.name}] Removed station: {removed.name}")
                return True
        self.logger.warning(f"[{self.name}] Station not found: {station_name}")
        return False
    
    def get_station(self, station_name: str) -> Optional[Station]:
        """
        Get a station by name.
        
        Args:
            station_name: Name of station to find
            
        Returns:
            Station if found, None otherwise
        """
        for station in self.stations:
            if station.name == station_name:
                return station
        return None
    
    def __str__(self) -> str:
        station_names = [s.name for s in self.stations]
        return f"Pipeline({self.name}: {' -> '.join(station_names)})"
    
    def __repr__(self) -> str:
        return self.__str__()
