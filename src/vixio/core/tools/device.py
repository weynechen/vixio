"""
Device tool client interface

Abstract base class for device tool clients.
Each transport protocol implements its own version.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List

from vixio.core.tools.types import ToolDefinition


class DeviceToolClientBase(ABC):
    """
    Abstract base class for device tool client.
    
    Responsibilities:
    - Initialize tools connection with device
    - Fetch tool list from device
    - Execute tool calls on device
    - Handle tool call results
    
    Each transport protocol (Xiaozhi, WebRTC, etc.) implements its own version.
    Returns ToolDefinition (not FunctionTool) to avoid framework dependency.
    
    Usage:
        client = XiaozhiDeviceToolClient(send_callback, session_id)
        
        # Initialize and fetch tools
        if await client.initialize():
            tool_defs = client.get_tool_definitions()
            # Convert to agent-specific tools
            agent_tools = converter.convert_many(tool_defs)
        
        # Call a tool (usually triggered by agent)
        result = await client.call_tool("play_music", {"query": "周杰伦"})
    """
    
    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize tools connection and fetch tool list.
        
        Sends tools initialize and tools/list requests to device.
        Stores tool definitions internally.
        
        Returns:
            True if initialization successful, False otherwise
        """
        pass
    
    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Call a device tool and wait for result.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            
        Returns:
            Tool execution result as string
            
        Raises:
            TimeoutError: If tool call times out
            ValueError: If tool not found
        """
        pass
    
    @abstractmethod
    def get_tool_definitions(self) -> List[ToolDefinition]:
        """
        Get tool definitions (framework-agnostic).
        
        Returns list of ToolDefinition, each with device_client set to self.
        Provider layer converts these to framework-specific tools.
        
        Returns:
            List of ToolDefinition
        """
        pass
    
    @abstractmethod
    def on_tools_message(self, payload: Dict[str, Any]) -> None:
        """
        Handle incoming tools message from device.
        
        Called by Transport when receiving tools response.
        Routes to appropriate handler based on message type.
        
        Args:
            payload: tools JSON-RPC payload (without type="tools" wrapper) , or any other implementation of the tools protocol
        """
        pass
    
    @abstractmethod
    def cancel_all(self) -> None:
        """
        Cancel all pending tool calls.
        
        Called when session is interrupted or closed.
        Resolves all pending Futures with cancellation.
        """
        pass
    
    @property
    @abstractmethod
    def has_tools(self) -> bool:
        """
        Check if device has registered any tools.
        
        Returns:
            True if device has tools available
        """
        pass
    
    @property
    @abstractmethod
    def tool_count(self) -> int:
        """
        Get number of available tools.
        
        Returns:
            Number of tools
        """
        pass

