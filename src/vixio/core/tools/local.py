"""
Local tool definitions and utilities

Provides:
- Built-in local tools (get_time, get_date, etc.)
- Utility for converting Python functions to ToolDefinition
"""

import inspect
from datetime import datetime
from typing import Any, Callable, Dict, List, get_type_hints, Optional

from vixio.core.tools.types import ToolDefinition


# =============================================================================
# Built-in Local Tools
# =============================================================================

async def get_current_time() -> str:
    """
    Get the current time.
    
    Returns:
        Current time in HH:MM:SS format
    """
    return datetime.now().strftime("%H:%M:%S")


async def get_current_date() -> str:
    """
    Get the current date.
    
    Returns:
        Current date in YYYY-MM-DD format
    """
    return datetime.now().strftime("%Y-%m-%d")


async def get_current_datetime() -> str:
    """
    Get the current date and time.
    
    Returns:
        Current datetime in YYYY-MM-DD HH:MM:SS format
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
# Tool Definition Utilities
# =============================================================================

# Python type to JSON Schema type mapping
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}


def _python_type_to_json_type(py_type: type) -> str:
    """Convert Python type to JSON Schema type."""
    # Handle Optional types
    if hasattr(py_type, "__origin__"):
        if py_type.__origin__ is type(None):
            return "null"
        # For Optional[X], use X's type
        args = getattr(py_type, "__args__", ())
        for arg in args:
            if arg is not type(None):
                return _python_type_to_json_type(arg)
    
    return _TYPE_MAP.get(py_type, "string")


def func_to_tool_definition(func: Callable) -> ToolDefinition:
    """
    Convert a Python function to ToolDefinition.
    
    Uses function signature, type hints, and docstring to build schema.
    
    Args:
        func: Async function with type hints and docstring
        
    Returns:
        ToolDefinition with executor set to the function
        
    Example:
        async def search_music(query: str, limit: int = 10) -> str:
            '''
            Search for music.
            
            Args:
                query: Search query string
                limit: Maximum number of results
            '''
            ...
        
        tool_def = func_to_tool_definition(search_music)
        # tool_def.name = "search_music"
        # tool_def.parameters = {
        #     "type": "object",
        #     "properties": {
        #         "query": {"type": "string", "description": "Search query string"},
        #         "limit": {"type": "integer", "description": "Maximum number of results"}
        #     },
        #     "required": ["query"]
        # }
    """
    # Get function metadata
    name = func.__name__
    doc = func.__doc__ or ""
    
    # Parse docstring for description and param docs
    description, param_docs = _parse_docstring(doc)
    
    # Get type hints
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}
    
    # Get signature
    sig = inspect.signature(func)
    
    # Build JSON Schema for parameters
    properties = {}
    required = []
    
    for param_name, param in sig.parameters.items():
        # Skip self, cls, *args, **kwargs
        if param_name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        
        # Get type
        param_type = hints.get(param_name, str)
        json_type = _python_type_to_json_type(param_type)
        
        # Build property schema
        prop_schema = {"type": json_type}
        
        # Add description from docstring if available
        if param_name in param_docs:
            prop_schema["description"] = param_docs[param_name]
        
        properties[param_name] = prop_schema
        
        # Check if required (no default value)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    
    # Build complete schema
    parameters = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required
    
    return ToolDefinition(
        name=name,
        description=description,
        parameters=parameters,
        executor=func,
    )


def _parse_docstring(docstring: str) -> tuple[str, Dict[str, str]]:
    """
    Parse docstring to extract description and parameter docs.
    
    Supports Google-style docstrings.
    
    Args:
        docstring: Function docstring
        
    Returns:
        Tuple of (description, {param_name: param_description})
    """
    if not docstring:
        return "", {}
    
    lines = docstring.strip().split("\n")
    
    # First paragraph is description
    description_lines = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            break
        if line.lower().startswith(("args:", "arguments:", "parameters:", "returns:", "raises:")):
            break
        description_lines.append(line)
        i += 1
    
    description = " ".join(description_lines)
    
    # Parse Args section
    param_docs = {}
    in_args = False
    current_param = None
    current_desc = []
    
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        if stripped.lower().startswith(("args:", "arguments:", "parameters:")):
            in_args = True
            i += 1
            continue
        
        if stripped.lower().startswith(("returns:", "raises:", "example:")):
            # Save last param if any
            if current_param and current_desc:
                param_docs[current_param] = " ".join(current_desc)
            break
        
        if in_args:
            # Check for new parameter (name: description)
            if ":" in stripped and not stripped.startswith(" "):
                # Save previous param
                if current_param and current_desc:
                    param_docs[current_param] = " ".join(current_desc)
                
                # Parse new param
                parts = stripped.split(":", 1)
                current_param = parts[0].strip()
                current_desc = [parts[1].strip()] if len(parts) > 1 else []
            elif current_param:
                # Continuation of current param description
                current_desc.append(stripped)
        
        i += 1
    
    # Save last param if any
    if current_param and current_desc:
        param_docs[current_param] = " ".join(current_desc)
    
    return description, param_docs


# =============================================================================
# Built-in Tools List
# =============================================================================

# List of built-in local tool functions
_BUILTIN_TOOLS = [
    get_current_time,
    get_current_date,
    get_current_datetime,
]


def get_builtin_local_tools() -> List[ToolDefinition]:
    """
    Get all built-in local tools as ToolDefinitions.
    
    Returns:
        List of ToolDefinition for built-in tools
    """
    return [func_to_tool_definition(func) for func in _BUILTIN_TOOLS]


def register_local_tool(func: Callable) -> Callable:
    """
    Decorator to register a function as a built-in local tool.
    
    Usage:
        @register_local_tool
        async def my_tool(param: str) -> str:
            '''My custom tool.'''
            return "result"
    """
    _BUILTIN_TOOLS.append(func)
    return func

