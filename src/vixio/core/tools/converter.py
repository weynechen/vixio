"""
Tool converter interface

Abstract base class for converting tools to agent-specific formats.
Each agent provider implements its own converter.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, List

from vixio.core.tools.types import ToolDefinition


class ToolConverterBase(ABC):
    """
    Abstract base class for converting tools to framework-specific format.
    
    Each Agent provider implements its own converter:
    - OpenAI Agent: converts to FunctionTool
    - LangGraph: converts to Tool
    - etc.
    
    This separates conversion logic from tool definition,
    ensuring transport layer doesn't depend on agent framework.
    
    Usage:
        converter = OpenAIAgentToolConverter()
        
        # Convert device tools
        device_tool_defs = device_client.get_tool_definitions()
        device_tools = converter.convert_many(device_tool_defs)
        
        # Convert local tools
        local_tool_defs = get_builtin_local_tools()
        local_tools = converter.convert_many(local_tool_defs)
        
        # All tools for agent
        all_tools = device_tools + local_tools
    """
    
    @abstractmethod
    def convert(self, tool_def: ToolDefinition) -> Any:
        """
        Convert a single ToolDefinition to framework-specific tool.
        
        Args:
            tool_def: Framework-agnostic tool definition
            
        Returns:
            Framework-specific tool object (e.g., FunctionTool)
        """
        pass
    
    def convert_many(self, tool_defs: List[ToolDefinition]) -> List[Any]:
        """
        Convert multiple ToolDefinitions to framework-specific tools.
        
        Default implementation calls convert() for each definition.
        Override for batch optimization if needed.
        
        Args:
            tool_defs: List of tool definitions
            
        Returns:
            List of framework-specific tool objects
        """
        return [self.convert(td) for td in tool_defs]
    
    @abstractmethod
    def convert_function(self, func: Callable) -> Any:
        """
        Convert a Python function to framework-specific tool.
        
        Some frameworks (like OpenAI Agents) support native function conversion
        via decorators or introspection. This method allows using that native
        support when available.
        
        Args:
            func: Python async function with type hints and docstring
            
        Returns:
            Framework-specific tool object
        """
        pass

