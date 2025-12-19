"""
OpenAI Agent tool converter

Converts framework-agnostic ToolDefinition to OpenAI Agent's FunctionTool.
"""

import json
from typing import Any, Callable, List, Optional
from loguru import logger

from vixio.core.tools.converter import ToolConverterBase
from vixio.core.tools.types import ToolDefinition

try:
    from agents import FunctionTool
    from agents.tool_context import ToolContext
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = False
    FunctionTool = None
    ToolContext = None


class OpenAIAgentToolConverter(ToolConverterBase):
    """
    Converts ToolDefinition to OpenAI Agent's FunctionTool.
    
    Handles both local and device tools:
    - Local tools: calls the executor function directly
    - Device tools: calls device_client.call_tool()
    
    Usage:
        converter = OpenAIAgentToolConverter()
        
        # Convert device tools
        device_defs = device_client.get_tool_definitions()
        device_tools = converter.convert_many(device_defs)
        
        # Convert local tools
        local_defs = get_builtin_local_tools()
        local_tools = converter.convert_many(local_defs)
        
        # Initialize agent with all tools
        agent = Agent(tools=device_tools + local_tools, ...)
    """
    
    def __init__(self):
        if not AGENTS_AVAILABLE:
            raise RuntimeError(
                "OpenAI Agents framework not available. "
                "Install with: pip install openai-agents"
            )
        
        self.logger = logger.bind(component="OpenAIAgentToolConverter")
    
    def convert(self, tool_def: ToolDefinition) -> FunctionTool:
        """
        Convert ToolDefinition to FunctionTool.
        
        Creates appropriate on_invoke_tool callback based on tool source.
        """
        if tool_def.is_local():
            return self._convert_local_tool(tool_def)
        elif tool_def.is_device():
            return self._convert_device_tool(tool_def)
        else:
            raise ValueError(f"Tool '{tool_def.name}' has no executor or device_client")
    
    def convert_function(self, func: Callable) -> FunctionTool:
        """
        Convert Python function to FunctionTool.
        
        Uses func_to_tool_definition to extract metadata,
        then converts to FunctionTool.
        """
        from vixio.core.tools.local import func_to_tool_definition
        tool_def = func_to_tool_definition(func)
        return self._convert_local_tool(tool_def)
    
    def _convert_local_tool(self, tool_def: ToolDefinition) -> FunctionTool:
        """Convert local tool to FunctionTool."""
        tool_name = tool_def.name
        executor = tool_def.executor
        
        async def on_invoke_tool(ctx: ToolContext, input_json: str) -> str:
            """Invoke local tool."""
            try:
                # Parse arguments
                args = json.loads(input_json) if input_json else {}
                
                self.logger.debug(f"Invoking local tool '{tool_name}' with args: {args}")
                
                # Call executor
                result = await executor(**args)
                
                self.logger.debug(f"Local tool '{tool_name}' returned: {result}")
                return str(result) if result else "Success"
            
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON for tool '{tool_name}': {e}")
                return f"Error: Invalid arguments - {e}"
            
            except Exception as e:
                self.logger.error(f"Error invoking local tool '{tool_name}': {e}")
                return f"Error: {str(e)}"
        
        return FunctionTool(
            name=tool_name,
            description=tool_def.description,
            params_json_schema=tool_def.parameters,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=False,
        )
    
    def _convert_device_tool(self, tool_def: ToolDefinition) -> FunctionTool:
        """Convert device tool to FunctionTool."""
        tool_name = tool_def.name
        device_client = tool_def.device_client
        
        async def on_invoke_tool(ctx: ToolContext, input_json: str) -> str:
            """Invoke device tool via MCP."""
            try:
                # Parse arguments
                args = json.loads(input_json) if input_json else {}
                
                self.logger.info(f"Invoking device tool '{tool_name}' with args: {args}")
                
                # Call device tool
                result = await device_client.call_tool(tool_name, args)
                
                self.logger.info(f"Device tool '{tool_name}' returned: {result}")
                return str(result)
            
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON for tool '{tool_name}': {e}")
                return f"Error: Invalid arguments - {e}"
            
            except TimeoutError as e:
                self.logger.error(f"Device tool '{tool_name}' timed out")
                return f"Error: Tool call timed out"
            
            except Exception as e:
                self.logger.error(f"Error invoking device tool '{tool_name}': {e}")
                return f"Error: {str(e)}"
        
        return FunctionTool(
            name=tool_name,
            description=tool_def.description,
            params_json_schema=tool_def.parameters,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=False,
        )


def get_openai_agent_tools(
    device_tools: Optional[List[ToolDefinition]] = None,
    include_builtin: bool = True,
) -> List[FunctionTool]:
    """
    Convenience function to get all tools as FunctionTool list.
    
    Args:
        device_tools: List of device tool definitions
        include_builtin: Whether to include built-in local tools
        
    Returns:
        List of FunctionTool ready for Agent initialization
    """
    converter = OpenAIAgentToolConverter()
    tools = []
    
    # Convert device tools
    if device_tools:
        tools.extend(converter.convert_many(device_tools))
    
    # Add built-in local tools
    if include_builtin:
        from vixio.core.tools.local import get_builtin_local_tools
        local_defs = get_builtin_local_tools()
        tools.extend(converter.convert_many(local_defs))
    
    return tools

