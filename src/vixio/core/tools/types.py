"""
Framework-agnostic tool type definitions

These types are used by:
- Transport layer (device tools)
- Core layer (local tools)
- Provider layer converts these to framework-specific format (e.g., FunctionTool)
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from vixio.core.tools.device import DeviceToolClientBase


@dataclass
class ToolDefinition:
    """
    Framework-agnostic tool definition.
    
    This is the common format used across all layers:
    - Transport layer creates these from device MCP responses
    - Core layer creates these for local tools
    - Provider layer converts these to framework-specific tools (e.g., FunctionTool)
    
    Attributes:
        name: Tool name (unique identifier)
        description: Human-readable description for LLM
        parameters: JSON Schema for tool parameters
        executor: For LOCAL tools - the actual async function to call
        device_client: For DEVICE tools - reference to DeviceToolClientBase
        timeout: Tool execution timeout in seconds
    """
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    
    # For local tools: the actual executor function
    executor: Optional[Callable[..., Awaitable[str]]] = None
    
    # For device tools: reference to the client that can execute it
    device_client: Optional['DeviceToolClientBase'] = None
    
    # Execution timeout
    timeout: float = 30.0
    
    def is_local(self) -> bool:
        """Check if this is a local tool."""
        return self.executor is not None
    
    def is_device(self) -> bool:
        """Check if this is a device tool."""
        return self.device_client is not None
    
    def __repr__(self) -> str:
        source = "local" if self.is_local() else "device" if self.is_device() else "unknown"
        return f"ToolDefinition({self.name}, source={source})"


@dataclass
class ToolCallRequest:
    """
    Tool call request (framework-agnostic).
    
    Used for tracking and debugging tool calls.
    """
    tool_name: str
    arguments: Dict[str, Any]
    call_id: str = ""
    
    def __repr__(self) -> str:
        return f"ToolCallRequest({self.tool_name}, id={self.call_id[:8] if self.call_id else 'none'})"


@dataclass
class ToolCallResult:
    """
    Tool call result (framework-agnostic).
    
    Used for tracking and debugging tool results.
    """
    call_id: str
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    
    def __repr__(self) -> str:
        status = "success" if self.success else "error"
        return f"ToolCallResult({self.call_id[:8] if self.call_id else 'none'}, {status})"

