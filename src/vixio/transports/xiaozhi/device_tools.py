"""
Xiaozhi device tool client implementation

Handles MCP protocol interactions for Xiaozhi devices:
- Initialize MCP connection
- Fetch tool list from device
- Execute tool calls on device
- Handle tool call responses
"""

import asyncio
import json
import re
from typing import Any, Callable, Dict, List, Optional, Awaitable
from loguru import logger

from vixio.core.tools.device import DeviceToolClientBase
from vixio.core.tools.types import ToolDefinition


class XiaozhiDeviceToolClient(DeviceToolClientBase):
    """
    MCP client for Xiaozhi devices.
    
    Manages MCP protocol interactions:
    - Sends initialize and tools/list requests
    - Receives and parses tool definitions
    - Executes tool calls and waits for responses
    
    Usage:
        client = XiaozhiDeviceToolClient(send_callback, session_id, vision_config)
        
        # Initialize (called by Transport after hello handshake)
        if await client.initialize():
            # Get tool definitions for agent
            tool_defs = client.get_tool_definitions()
        
        # Handle incoming MCP messages (called by Transport)
        client.on_tools_message(payload)
        
        # Call a tool (triggered by agent)
        result = await client.call_tool("play_music", {"query": "周杰伦"})
    """
    
    # Fixed message IDs for initialize and tools/list
    MCP_INITIALIZE_ID = 1
    MCP_TOOLS_LIST_ID = 2
    
    def __init__(
        self,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]],
        session_id: str,
        timeout: float = 30.0,
        vision_config: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Xiaozhi device tool client.
        
        Args:
            send_callback: Async function to send MCP messages to device
                           Signature: async def send(message: Dict) -> None
            session_id: Session ID for logging
            timeout: Default timeout for tool calls
            vision_config: Optional vision config with 'url' and 'token' keys
                          Will be sent in MCP initialize capabilities.vision
        """
        self._send_callback = send_callback
        self._session_id = session_id
        self._timeout = timeout
        self._vision_config = vision_config  # {"url": "...", "token": "..."}
        
        # Tool storage
        self._tools: Dict[str, Dict[str, Any]] = {}  # sanitized_name -> tool_data
        self._name_mapping: Dict[str, str] = {}  # sanitized_name -> original_name
        
        # MCP state
        self._ready = False
        self._initialized = False
        
        # Pending tool calls (call_id -> Future)
        self._pending_calls: Dict[int, asyncio.Future] = {}
        self._next_id = 3  # Start after initialize(1) and tools/list(2)
        self._lock = asyncio.Lock()
        
        # Initialize event (for waiting on tools/list completion)
        self._ready_event = asyncio.Event()
        
        self.logger = logger.bind(
            component="XiaozhiDeviceToolClient",
            session_id=session_id[:8] if session_id else "unknown"
        )
    
    # =========================================================================
    # DeviceToolClientBase interface implementation
    # =========================================================================
    
    async def initialize(self) -> bool:
        """
        Initialize MCP connection and fetch tool list.
        
        Sends:
        1. initialize request (id=1)
        2. tools/list request (id=2)
        
        Waits for tools/list response before returning.
        
        Returns:
            True if initialization successful
        """
        if self._initialized:
            self.logger.debug("Already initialized")
            return self._ready
        
        self._initialized = True
        self.logger.info("Initializing MCP connection...")
        
        try:
            # Send initialize message
            await self._send_initialize()
            
            # Send tools/list request
            await self._send_tools_list()
            
            # Wait for tools list response (with timeout)
            try:
                await asyncio.wait_for(
                    self._ready_event.wait(),
                    timeout=self._timeout
                )
                self.logger.info(f"MCP initialization complete, {len(self._tools)} tools available")
                return True
            except asyncio.TimeoutError:
                self.logger.warning("MCP initialization timed out waiting for tools list")
                return False
        
        except Exception as e:
            self.logger.error(f"MCP initialization failed: {e}")
            return False
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Call a device tool and wait for result.
        
        Args:
            tool_name: Name of the tool (sanitized name)
            arguments: Tool arguments as dict
            
        Returns:
            Tool execution result as string
        """
        if not self._ready:
            raise RuntimeError("MCP client not ready")
        
        if tool_name not in self._tools:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        # Get next call ID
        async with self._lock:
            call_id = self._next_id
            self._next_id += 1
        
        # Create future for response
        result_future: asyncio.Future = asyncio.Future()
        async with self._lock:
            self._pending_calls[call_id] = result_future
        
        # Get original tool name
        original_name = self._name_mapping.get(tool_name, tool_name)
        
        # Send tools/call request
        payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": original_name,
                "arguments": arguments
            }
        }
        
        self.logger.info(f"Calling tool '{original_name}' with args: {arguments}")
        await self._send_mcp_message(payload)
        
        try:
            # Wait for response
            raw_result = await asyncio.wait_for(result_future, timeout=self._timeout)
            self.logger.debug(f"Tool '{original_name}' returned: {raw_result}")
            
            # Parse result
            return self._parse_tool_result(raw_result)
        
        except asyncio.TimeoutError:
            async with self._lock:
                self._pending_calls.pop(call_id, None)
            raise TimeoutError(f"Tool call '{tool_name}' timed out")
        
        except asyncio.CancelledError:
            async with self._lock:
                self._pending_calls.pop(call_id, None)
            raise
        
        except Exception as e:
            async with self._lock:
                self._pending_calls.pop(call_id, None)
            raise
    
    def get_tool_definitions(self) -> List[ToolDefinition]:
        """
        Get tool definitions (framework-agnostic).
        
        Returns list of ToolDefinition with device_client set to self.
        """
        definitions = []
        
        for sanitized_name, tool_data in self._tools.items():
            input_schema = tool_data.get("inputSchema", {})
            
            # Build JSON Schema parameters
            parameters = {
                "type": input_schema.get("type", "object"),
                "properties": input_schema.get("properties", {}),
            }
            required = input_schema.get("required", [])
            if required:
                parameters["required"] = required
            
            definition = ToolDefinition(
                name=sanitized_name,
                description=tool_data.get("description", ""),
                parameters=parameters,
                device_client=self,
                timeout=self._timeout,
            )
            definitions.append(definition)
        
        return definitions
    
    def on_tools_message(self, payload: Dict[str, Any]) -> None:
        """
        Handle incoming MCP message from device.
        
        Routes to appropriate handler based on message content.
        """
        if not isinstance(payload, dict):
            self.logger.error("Invalid MCP payload: not a dict")
            return
        
        # Handle result responses
        if "result" in payload:
            self._handle_result(payload)
        
        # Handle error responses
        elif "error" in payload:
            self._handle_error(payload)
        
        # Handle method calls (requests from device - rare)
        elif "method" in payload:
            self._handle_method(payload)
        
        else:
            self.logger.warning(f"Unknown MCP message format: {payload}")
    
    def cancel_all(self) -> None:
        """Cancel all pending tool calls."""
        cancelled_count = 0
        for call_id, future in list(self._pending_calls.items()):
            if not future.done():
                future.cancel()
                cancelled_count += 1
        
        self._pending_calls.clear()
        
        if cancelled_count > 0:
            self.logger.info(f"Cancelled {cancelled_count} pending tool calls")
    
    @property
    def has_tools(self) -> bool:
        """Check if device has registered any tools."""
        return len(self._tools) > 0
    
    @property
    def tool_count(self) -> int:
        """Get number of available tools."""
        return len(self._tools)
    
    # =========================================================================
    # Internal methods
    # =========================================================================
    
    async def _send_mcp_message(self, payload: Dict[str, Any]) -> None:
        """Send MCP message via callback."""
        message = {
            "type": "mcp",
            "payload": payload
        }
        await self._send_callback(message)
    
    async def _send_initialize(self) -> None:
        """Send MCP initialize request with vision capabilities."""
        # Build capabilities
        capabilities: Dict[str, Any] = {
            "roots": {"listChanged": True},
            "sampling": {},
        }
        
        # Add vision config if available
        if self._vision_config:
            capabilities["vision"] = self._vision_config
            self.logger.debug(f"Including vision config: url={self._vision_config.get('url', '')[:50]}...")
        
        payload = {
            "jsonrpc": "2.0",
            "id": self.MCP_INITIALIZE_ID,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": capabilities,
                "clientInfo": {
                    "name": "VixioServer",
                    "version": "1.0.0"
                }
            }
        }
        self.logger.debug("Sending MCP initialize")
        await self._send_mcp_message(payload)
    
    async def _send_tools_list(self, cursor: Optional[str] = None) -> None:
        """Send MCP tools/list request."""
        payload = {
            "jsonrpc": "2.0",
            "id": self.MCP_TOOLS_LIST_ID,
            "method": "tools/list"
        }
        
        if cursor:
            payload["params"] = {"cursor": cursor}
        
        self.logger.debug(f"Sending MCP tools/list (cursor={cursor})")
        await self._send_mcp_message(payload)
    
    def _handle_result(self, payload: Dict[str, Any]) -> None:
        """Handle result response."""
        msg_id = int(payload.get("id", 0))
        result = payload.get("result", {})
        
        # Check for pending tool call first
        if msg_id in self._pending_calls:
            self._resolve_tool_call(msg_id, result)
            return
        
        # Handle initialize response
        if msg_id == self.MCP_INITIALIZE_ID:
            self._handle_initialize_result(result)
        
        # Handle tools/list response
        elif msg_id == self.MCP_TOOLS_LIST_ID:
            self._handle_tools_list_result(result)
        
        else:
            self.logger.warning(f"Unknown result message id: {msg_id}")
    
    def _handle_initialize_result(self, result: Dict[str, Any]) -> None:
        """Handle initialize response."""
        server_info = result.get("serverInfo", {})
        name = server_info.get("name", "unknown")
        version = server_info.get("version", "unknown")
        self.logger.info(f"Device MCP server: {name} v{version}")
    
    def _handle_tools_list_result(self, result: Dict[str, Any]) -> None:
        """Handle tools/list response."""
        tools_data = result.get("tools", [])
        
        if not isinstance(tools_data, list):
            self.logger.error("Invalid tools list format")
            self._ready_event.set()
            return
        
        self.logger.info(f"Received {len(tools_data)} tools from device")
        
        # Parse and store tools
        for tool in tools_data:
            if not isinstance(tool, dict):
                continue
            
            name = tool.get("name", "")
            if not name:
                continue
            
            # Sanitize tool name (replace special characters)
            sanitized_name = self._sanitize_tool_name(name)
            
            input_schema = tool.get("inputSchema", {})
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            
            self._tools[sanitized_name] = {
                "name": name,
                "description": tool.get("description", ""),
                "inputSchema": {
                    "type": input_schema.get("type", "object"),
                    "properties": input_schema.get("properties", {}),
                    "required": input_schema.get("required", [])
                }
            }
            self._name_mapping[sanitized_name] = name
            
            self.logger.debug(f"Registered tool: {sanitized_name} (original: {name})")
        
        # Check for pagination
        next_cursor = result.get("nextCursor", "")
        if next_cursor:
            self.logger.debug(f"More tools available, fetching with cursor: {next_cursor}")
            # Create task to fetch more tools
            asyncio.create_task(self._send_tools_list(cursor=next_cursor))
        else:
            # All tools received
            self._ready = True
            self._ready_event.set()
            self.logger.info(f"All tools received, {len(self._tools)} tools ready")
    
    def _handle_error(self, payload: Dict[str, Any]) -> None:
        """Handle error response."""
        msg_id = int(payload.get("id", 0))
        error_data = payload.get("error", {})
        error_msg = error_data.get("message", "Unknown error")
        
        self.logger.error(f"MCP error (id={msg_id}): {error_msg}")
        
        # Reject pending tool call if any
        if msg_id in self._pending_calls:
            future = self._pending_calls.pop(msg_id)
            if not future.done():
                future.set_exception(RuntimeError(f"MCP error: {error_msg}"))
    
    def _handle_method(self, payload: Dict[str, Any]) -> None:
        """Handle method request from device."""
        method = payload.get("method", "")
        self.logger.info(f"Received MCP method request: {method} (not implemented)")
    
    def _resolve_tool_call(self, call_id: int, result: Any) -> None:
        """Resolve a pending tool call."""
        future = self._pending_calls.pop(call_id, None)
        if future and not future.done():
            future.set_result(result)
    
    def _parse_tool_result(self, raw_result: Any) -> str:
        """Parse tool call result into string."""
        if isinstance(raw_result, dict):
            # Check for error
            if raw_result.get("isError") is True:
                error_msg = raw_result.get("error", "Tool call returned error")
                raise RuntimeError(error_msg)
            
            # Extract content
            content = raw_result.get("content")
            if isinstance(content, list) and len(content) > 0:
                first_item = content[0]
                if isinstance(first_item, dict) and "text" in first_item:
                    return first_item["text"]
        
        # Fallback: convert to string
        return str(raw_result)
    
    @staticmethod
    def _sanitize_tool_name(name: str) -> str:
        """
        Sanitize tool name for agent compatibility.
        
        Replaces special characters with underscores.
        """
        # Replace non-alphanumeric characters (except underscore) with underscore
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # Remove consecutive underscores
        sanitized = re.sub(r'_+', '_', sanitized)
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        return sanitized or "unnamed_tool"

