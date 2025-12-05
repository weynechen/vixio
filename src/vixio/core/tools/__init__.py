"""
Core tools abstractions for Vixio framework

This module provides framework-agnostic tool definitions and interfaces:
- ToolDefinition: Universal tool representation
- DeviceToolClientBase: Interface for device tool clients
- ToolConverterBase: Interface for converting tools to agent-specific formats
- Local tool utilities
"""

from vixio.core.tools.types import ToolDefinition, ToolCallRequest, ToolCallResult
from vixio.core.tools.device import DeviceToolClientBase
from vixio.core.tools.converter import ToolConverterBase
from vixio.core.tools.local import (
    get_current_time,
    get_current_date,
    func_to_tool_definition,
    get_builtin_local_tools,
)

__all__ = [
    # Types
    "ToolDefinition",
    "ToolCallRequest",
    "ToolCallResult",
    # Interfaces
    "DeviceToolClientBase",
    "ToolConverterBase",
    # Local tools
    "get_current_time",
    "get_current_date",
    "func_to_tool_definition",
    "get_builtin_local_tools",
]

