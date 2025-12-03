# Function Tool 接入设计文档

## 1. 设计理念

**核心原则：直接使用 Agent 框架的工具机制，不做额外抽象。**

```python
# 目标：所有工具汇总到一个 list，提供给 Agent
agent = Agent(
    name="Assistant",
    instructions="...",
    tools=[device_tool_1, device_tool_2, local_tool_1, ...],
    model=LitellmModel(...)
)
```

### 1.1 工具来源

| 来源 | 说明 | 实现方式 |
|------|------|----------|
| **设备端** | 设备通过 MCP 协议注册的能力 | 动态创建 `FunctionTool`，执行时通过 Transport 往返 |
| **本地** | 服务器端常规函数 | 提供转换函数 → `FunctionTool` |
| **MCP** | 第三方 MCP | 预留，用户自行集成 |

### 1.2 不做什么

- ❌ 不设计通用工具抽象层
- ❌ 不增加新的 Station
- ❌ 不增加多 Agent 框架适配接口

---

## 2. 设备工具获取流程

### 2.1 完整交互流程

参考 xiaozhi-server 的实现，使用 JSON-RPC 2.0 协议：

```
┌──────────┐                                              ┌──────────┐
│  Server  │                                              │  Device  │
└────┬─────┘                                              └────┬─────┘
     │                                                         │
     │◄────────── hello {features: {mcp: true}} ───────────────│
     │                                                         │
     │                   设备支持 MCP/Function Tool             │
     │                                                         │
     │──────────── mcp {method: "initialize", id: 1} ─────────►│
     │                                                         │
     │◄───────── mcp {id: 1, result: {serverInfo: ...}} ───────│
     │                                                         │
     │──────────── mcp {method: "tools/list", id: 2} ─────────►│
     │                                                         │
     │◄───────── mcp {id: 2, result: {tools: [...]}} ──────────│
     │                                                         │
     │            工具列表获取完成，Agent 可以使用               │
     │                                                         │
     │  ═══════════════════════════════════════════════════════│
     │                                                         │
     │           LLM 决定调用 play_music(query="周杰伦")        │
     │                                                         │
     │──── mcp {method: "tools/call", id: 3, params: {...}} ──►│
     │                                                         │
     │                                          Device 执行工具 │
     │                                                         │
     │◄────── mcp {id: 3, result: {content: [...]}} ───────────│
     │                                                         │
     │                  返回结果给 LLM 继续生成                  │
     │                                                         │
```

### 2.2 消息格式

#### hello 消息（Device → Server）

```json
{
    "type": "hello",
    "features": {
        "mcp": true
    }
}
```

#### initialize 请求（Server → Device）

```json
{
    "type": "mcp",
    "payload": {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "VixioServer", "version": "1.0.0"}
        }
    }
}
```

#### tools/list 请求（Server → Device）

```json
{
    "type": "mcp",
    "payload": {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list"
    }
}
```

#### tools/list 响应（Device → Server）

```json
{
    "type": "mcp",
    "payload": {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "play_music",
                    "description": "Play music on the device",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Music search query"}
                        },
                        "required": ["query"]
                    }
                },
                {
                    "name": "set_volume",
                    "description": "Set device volume (0-100)",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "level": {"type": "integer"}
                        },
                        "required": ["level"]
                    }
                }
            ]
        }
    }
}
```

#### tools/call 请求（Server → Device）

```json
{
    "type": "mcp",
    "payload": {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "play_music",
            "arguments": {"query": "周杰伦"}
        }
    }
}
```

#### tools/call 响应（Device → Server）

```json
{
    "type": "mcp",
    "payload": {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "content": [
                {"type": "text", "text": "正在播放: 周杰伦 - 晴天"}
            ]
        }
    }
}
```

---

## 3. 数据流设计

### 3.1 工具获取时序（Handshake 后）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Transport Layer                                                             │
│                                                                              │
│   on_hello(features):                                                        │
│     if features.mcp:                                                         │
│       1. 创建 DeviceToolClient                                               │
│       2. 发送 initialize 消息                                                 │
│       3. 发送 tools/list 消息                                                 │
│       4. 等待 tools 列表返回                                                  │
│       5. 创建 FunctionTool[]                                                 │
│       6. 通知 AgentStation 工具已就绪                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 工具调用时序

```
用户: "帮我播放周杰伦的歌"
        │
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  AgentStation                                                                 │
│                                                                               │
│   Agent.chat("帮我播放周杰伦的歌")                                            │
│         │                                                                     │
│         ▼                                                                     │
│   LLM 决定调用 play_music(query="周杰伦")                                     │
│         │                                                                     │
│         ▼                                                                     │
│   FunctionTool.on_invoke_tool() 被触发                                        │
│         │                                                                     │
│         │    ┌───────────────────────────────────────────────────────────┐   │
│         └───►│  DeviceToolClient.call_tool("play_music", {query: "周杰伦"}) │   │
│              │                                                           │   │
│              │  1. 生成唯一 call_id                                       │   │
│              │  2. 创建 Future 等待结果                                   │   │
│              │  3. 发送 tools/call 消息到设备                             │   │
│              │  4. await Future (with timeout)                           │   │
│              │  5. 返回结果给 LLM                                         │   │
│              └───────────────────────────────────────────────────────────┘   │
│                                                                               │
│         ▼                                                                     │
│   LLM 收到结果: "正在播放: 周杰伦 - 晴天"                                     │
│         │                                                                     │
│         ▼                                                                     │
│   LLM 生成最终回复 → TEXT_DELTA 流式输出                                      │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. MCP 消息的数据路径设计

### 4.1 设计决策：MCP 消息旁路 vs 经过 Pipeline

MCP 消息（工具调用/结果）有两种处理方式：

| 方式 | 优点 | 缺点 |
|------|------|------|
| **旁路** (当前设计) | 简单，不需要新 Chunk 类型 | 与框架数据流不一致 |
| **经过 Pipeline** | 统一数据流，可追踪 | 复杂，ToolResult 需要特殊路由 |

**选择旁路的原因**：
1. MCP 消息是 Agent 内部的同步调用（await 结果），不是流式数据
2. ToolResult 需要返回给调用方（DeviceToolClient），不是送到 Pipeline 下游
3. 不需要经过中间 Station 处理

### 4.2 完整数据路径图

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Pipeline (主数据流)                                 │
│                                                                                  │
│   Audio/Text 数据流:                                                             │
│   InputStation → VAD → ASR → Agent → TTS → OutputStation                        │
│        ▲                       │                    │                            │
│        │                       │                    │                            │
│   Transport                    │                    ▼                            │
│   read_queue              TEXT_DELTA           send_queue                       │
│                                                                                  │
└────────│───────────────────────│────────────────────│────────────────────────────┘
         │                       │                    │
         │                       │                    │
┌────────│───────────────────────│────────────────────│────────────────────────────┐
│        │                       │                    │                            │
│   Transport Layer              │                    │                            │
│        │                       │                    │                            │
│        │            ┌──────────┴──────────┐         │                            │
│        │            │    AgentStation     │         │                            │
│        │            │                     │         │                            │
│        │            │  Agent 调用工具时:   │         │                            │
│        │            │  FunctionTool       │         │                            │
│        │            │    .on_invoke()     │         │                            │
│        │            └──────────┬──────────┘         │                            │
│        │                       │                    │                            │
│        │                       ▼                    │                            │
│        │     ┌─────────────────────────────────┐    │                            │
│        │     │      DeviceToolClient           │    │                            │
│        │     │                                 │    │                            │
│        │     │  call_tool()                    │    │                            │
│        │     │    │                            │    │                            │
│        │     │    ├─► _send_mcp_message()  ────│────│───► WebSocket ──► Device   │
│        │     │    │                            │    │                            │
│        │     │    └─► await Future             │    │                            │
│        │     │           ▲                     │    │                            │
│        │     │           │                     │    │                            │
│        │     │  on_mcp_message() ◄─────────────│────│────────────────────────────│
│        │     │    │                            │    │           ▲                │
│        │     │    └─► resolve Future           │    │           │                │
│        │     │           │                     │    │    Device response         │
│        │     │           ▼                     │    │           │                │
│        │     │     return result to Agent      │    │   WebSocket ◄── Device     │
│        │     └─────────────────────────────────┘    │                            │
│        │                                            │                            │
│        │              MCP 消息旁路                   │                            │
│        │         (不经过 Pipeline Station)          │                            │
│        │                                            │                            │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Protocol 扩展：MCP 消息处理

虽然 MCP 消息不经过 Pipeline，但仍需要 Protocol 处理编解码：

```python
# transports/xiaozhi/protocol.py (扩展)

class XiaozhiMessageType:
    # ... existing types ...
    MCP = "mcp"  # MCP JSON-RPC messages


class XiaozhiProtocol(ProtocolBase):
    
    def parse_message(self, data: Union[bytes, str]) -> Dict[str, Any]:
        """Parse incoming message."""
        # ... existing code ...
        
        # MCP messages are JSON with type="mcp"
        # Handled directly by Transport, not converted to Chunk
        if isinstance(data, str):
            msg = json.loads(data)
            # MCP messages have type="mcp" and payload field
            if msg.get("type") == XiaozhiMessageType.MCP:
                return msg  # Return as-is, Transport will route to DeviceToolClient
            # ... rest of handling ...
    
    def create_mcp_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create MCP message for sending to device."""
        return {
            "type": XiaozhiMessageType.MCP,
            "payload": payload
        }
    
    def message_to_chunk(self, message: Dict, session_id: str, turn_id: int) -> Optional[Chunk]:
        """Convert message to Chunk."""
        msg_type = message.get("type")
        
        # MCP messages are NOT converted to Chunk
        # They are handled directly by Transport → DeviceToolClient
        if msg_type == XiaozhiMessageType.MCP:
            return None  # Signal to Transport: handle this specially
        
        # ... existing chunk conversions ...
```

### 4.4 Transport 消息路由

```python
# transports/xiaozhi/transport.py (扩展)

class XiaozhiTransport(TransportBase):
    
    async def _read_worker(self, session_id: str) -> None:
        """Read worker with MCP message routing."""
        while self._running:
            data = await self._do_read(session_id)
            if data is None:
                break
            
            # Decode message
            message = self._protocol.decode_message(data)
            
            # Route MCP messages directly to DeviceToolClient (bypass Pipeline)
            if message.get("type") == XiaozhiMessageType.MCP:
                self._on_mcp_message(session_id, message)
                continue  # Don't put in read_queue
            
            # Other messages go to read_queue for Pipeline processing
            await self._read_queues[session_id].put(data)
    
    def _on_mcp_message(self, session_id: str, message: Dict) -> None:
        """Route MCP message to DeviceToolClient."""
        client = self._device_tool_clients.get(session_id)
        if client:
            client.on_mcp_message(message.get("payload", {}))
        else:
            self.logger.warning(f"No DeviceToolClient for session {session_id[:8]}")
```

### 4.5 完整格式转换时序

一次设备工具调用的完整格式转换过程：

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           工具调用格式转换时序                                     │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│  1. LLM 决定调用工具                                                               │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  OpenAI API Response:                                                    │  │
│     │  {                                                                       │  │
│     │    "tool_calls": [{                                                      │  │
│     │      "name": "play_music",                                               │  │
│     │      "arguments": "{\"query\": \"周杰伦\"}"                               │  │
│     │    }]                                                                    │  │
│     │  }                                                                       │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  2. openai-agents 框架触发 FunctionTool.on_invoke_tool()                          │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  Python 调用:                                                            │  │
│     │  on_invoke_tool(ctx, input_json="{\"query\": \"周杰伦\"}")               │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  3. DeviceToolClient.call_tool() 构建 JSON-RPC 请求                              │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  JSON-RPC Request (Python dict):                                         │  │
│     │  {                                                                       │  │
│     │    "jsonrpc": "2.0",                                                     │  │
│     │    "id": 3,                                                              │  │
│     │    "method": "tools/call",                                               │  │
│     │    "params": {"name": "play_music", "arguments": {"query": "周杰伦"}}    │  │
│     │  }                                                                       │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  4. Protocol.create_mcp_message() 封装为 Xiaozhi 消息                            │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  Xiaozhi Message (Python dict):                                          │  │
│     │  {                                                                       │  │
│     │    "type": "mcp",                                                        │  │
│     │    "payload": { <JSON-RPC Request> }                                     │  │
│     │  }                                                                       │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  5. Protocol.encode_message() 序列化为 JSON 字符串                                │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  Wire Format (JSON string):                                              │  │
│     │  "{\"type\":\"mcp\",\"payload\":{\"jsonrpc\":\"2.0\",...}}"               │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  6. Transport.write() 发送到 WebSocket                                           │
│                                                                                   │
│  ═══════════════════════════════════════════════════════════════════════════════ │
│                              ↓ Device 处理 ↓                                      │
│  ═══════════════════════════════════════════════════════════════════════════════ │
│                                                                                   │
│  7. WebSocket 接收设备响应                                                         │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  Wire Format (JSON string):                                              │  │
│     │  "{\"type\":\"mcp\",\"payload\":{\"jsonrpc\":\"2.0\",\"id\":3,...}}"     │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  8. Protocol.decode_message() 解析为 Python dict                                 │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  Xiaozhi Message (Python dict):                                          │  │
│     │  {                                                                       │  │
│     │    "type": "mcp",                                                        │  │
│     │    "payload": {                                                          │  │
│     │      "jsonrpc": "2.0",                                                   │  │
│     │      "id": 3,                                                            │  │
│     │      "result": {"content": [{"type": "text", "text": "正在播放..."}]}     │  │
│     │    }                                                                     │  │
│     │  }                                                                       │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  9. Transport 识别 type="mcp"，路由到 DeviceToolClient                            │
│     （不经过 Pipeline / InputStation）                                            │
│                                      │                                           │
│                                      ▼                                           │
│  10. DeviceToolClient.on_mcp_message() 处理 payload                              │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  JSON-RPC Response:                                                      │  │
│     │  {                                                                       │  │
│     │    "jsonrpc": "2.0",                                                     │  │
│     │    "id": 3,                                                              │  │
│     │    "result": {"content": [{"type": "text", "text": "正在播放..."}]}       │  │
│     │  }                                                                       │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  11. 通过 call_id 匹配 Future，resolve 返回结果                                    │
│     ┌─────────────────────────────────────────────────────────────────────────┐  │
│     │  Python string:                                                          │  │
│     │  "正在播放: 周杰伦 - 晴天"                                                 │  │
│     └─────────────────────────────────────────────────────────────────────────┘  │
│                                      │                                           │
│                                      ▼                                           │
│  12. 返回给 LLM，继续生成回复                                                      │
│                                                                                   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 4.6 格式转换点总结

| 位置 | 转换 | 负责组件 |
|------|------|----------|
| **发送方向** | | |
| Agent → DeviceToolClient | JSON string → Python dict | `json.loads()` |
| DeviceToolClient | Python dict → JSON-RPC payload | 手动构建 |
| Protocol | JSON-RPC → Xiaozhi Message | `create_mcp_message()` |
| Protocol | Xiaozhi Message → Wire format | `encode_message()` (json.dumps) |
| **接收方向** | | |
| Protocol | Wire format → Xiaozhi Message | `decode_message()` (json.loads) |
| Transport | 识别 type="mcp" | 条件判断 |
| DeviceToolClient | JSON-RPC result → Python string | 提取 content[0].text |

### 4.7 为什么不用 Chunk？

如果要用 Chunk 表示 MCP 消息：

```python
# 假设使用 Chunk 的问题:

# 1. ToolCallChunk 从哪里产生？
#    - 从 AgentStation 内部的 FunctionTool.on_invoke()
#    - 但此时 Agent 在等待结果，不能 yield chunk

# 2. ToolResultChunk 送到哪里？
#    - 需要送回给 DeviceToolClient.call_tool() 的 await
#    - 不是送到 Pipeline 下游

# 3. 结论：MCP 消息是同步调用，不适合流式 Chunk 模型
```

---

## 5. 实现设计

### 5.1 抽象层：通用工具定义

```python
# core/tools/types.py

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Awaitable


@dataclass
class ToolDefinition:
    """
    Framework-agnostic tool definition.
    
    This is the common format used by:
    - Transport layer (device tools)
    - Core layer (local tools)
    
    Provider layer converts this to framework-specific format (e.g., FunctionTool).
    """
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    
    # For local tools: the actual executor function
    executor: Optional[Callable[..., Awaitable[str]]] = None
    
    # For device tools: reference to the client that can execute it
    device_client: Optional[Any] = None


@dataclass
class ToolCallRequest:
    """Tool call request (framework-agnostic)."""
    tool_name: str
    arguments: Dict[str, Any]
    call_id: str = ""


@dataclass
class ToolCallResult:
    """Tool call result (framework-agnostic)."""
    call_id: str
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
```

### 5.2 抽象层：DeviceToolClient 接口

```python
# core/tools/device.py

from abc import ABC, abstractmethod
from typing import Any, Dict, List
from core.tools.types import ToolDefinition


class DeviceToolClientBase(ABC):
    """
    Abstract base class for device tool client.
    
    Each transport protocol implements its own version.
    Returns ToolDefinition (not FunctionTool) to avoid framework dependency.
    """
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize connection and fetch tool list."""
        pass
    
    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a device tool and wait for result."""
        pass
    
    @abstractmethod
    def get_tool_definitions(self) -> List[ToolDefinition]:
        """
        Get tool definitions (framework-agnostic).
        
        Returns:
            List of ToolDefinition, NOT FunctionTool
        """
        pass
    
    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel all pending tool calls."""
        pass
```

### 5.3 抽象层：工具转换接口

```python
# core/tools/converter.py

from abc import ABC, abstractmethod
from typing import Any, Callable, List
from core.tools.types import ToolDefinition


class ToolConverterBase(ABC):
    """
    Abstract base class for converting tools to framework-specific format.
    
    Each Agent provider implements its own converter.
    Separates conversion by tool source type.
    """
    
    @abstractmethod
    def convert_device_tool(self, tool_def: ToolDefinition) -> Any:
        """
        Convert device tool definition to framework-specific tool.
        
        Args:
            tool_def: Device tool definition (with device_client reference)
            
        Returns:
            Framework-specific tool (e.g., FunctionTool)
        """
        pass
    
    @abstractmethod
    def convert_local_tool(self, func: Callable) -> Any:
        """
        Convert local function to framework-specific tool.
        
        Most Agent frameworks support this natively (e.g., @function_tool decorator).
        
        Args:
            func: Async function to convert
            
        Returns:
            Framework-specific tool
        """
        pass
    
    @abstractmethod
    def convert_mcp_tool(self, tool_def: ToolDefinition) -> Any:
        """
        Convert MCP tool definition to framework-specific tool.
        
        Args:
            tool_def: MCP tool definition (with mcp_client reference)
            
        Returns:
            Framework-specific tool
        """
        pass
    
    def convert_device_tools(self, tool_defs: List[ToolDefinition]) -> List[Any]:
        """Convert a list of device tool definitions."""
        return [self.convert_device_tool(td) for td in tool_defs]
```

### 5.4 Transport 实现：Xiaozhi DeviceToolClient

```python
# transports/xiaozhi/device_tools.py

import asyncio
from typing import Any, Callable, Dict, List
from core.tools.device import DeviceToolClientBase
from core.tools.types import ToolDefinition
from loguru import logger


class XiaozhiDeviceToolClient(DeviceToolClientBase):
    """
    Xiaozhi-specific device tool client.
    
    Implements MCP protocol over Xiaozhi WebSocket.
    Returns ToolDefinition (NOT FunctionTool) - conversion happens in Provider.
    """
    
    def __init__(self, session_id: str, send_callback: Callable, timeout: float = 30.0):
        self.session_id = session_id
        self.timeout = timeout
        self._send = send_callback
        
        self._tools: Dict[str, Dict] = {}
        self._pending_calls: Dict[int, asyncio.Future] = {}
        self._next_id = 3
        self._ready = asyncio.Event()
        
        self.logger = logger.bind(component="XiaozhiDeviceToolClient", session=session_id[:8])
    
    async def initialize(self) -> bool:
        """Initialize MCP connection and fetch tool list."""
        # Send initialize
        await self._send_mcp_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "VixioServer", "version": "1.0.0"}
            }
        })
        
        # Send tools/list
        await self._send_mcp_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        })
        
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=10.0)
            self.logger.info(f"Device tools ready: {len(self._tools)} tools")
            return True
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for device tools")
            return False
    
    def on_mcp_message(self, payload: Dict) -> None:
        """Handle MCP message from device."""
        if "result" in payload:
            msg_id = payload.get("id", 0)
            result = payload["result"]
            
            if msg_id in self._pending_calls:
                future = self._pending_calls.pop(msg_id)
                if not future.done():
                    future.set_result(result)
                return
            
            if msg_id == 1:
                self.logger.debug("MCP initialize response received")
            elif msg_id == 2:
                self._handle_tools_list(result)
        
        elif "error" in payload:
            msg_id = payload.get("id", 0)
            error = payload["error"]
            if msg_id in self._pending_calls:
                future = self._pending_calls.pop(msg_id)
                if not future.done():
                    future.set_exception(Exception(error.get("message", "MCP error")))
    
    def _handle_tools_list(self, result: Dict) -> None:
        """Handle tools/list response."""
        tools = result.get("tools", [])
        for tool in tools:
            name = tool.get("name", "")
            if name:
                self._tools[name] = {
                    "name": name,
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {"type": "object", "properties": {}})
                }
        self.logger.info(f"Device tools loaded: {len(self._tools)}")
        self._ready.set()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a device tool and wait for result."""
        if tool_name not in self._tools:
            return f"Error: Tool '{tool_name}' not found"
        
        call_id = self._next_id
        self._next_id += 1
        
        future = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = future
        
        try:
            await self._send_mcp_message({
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments}
            })
            
            result = await asyncio.wait_for(future, timeout=self.timeout)
            
            # Extract text from MCP result
            if isinstance(result, dict):
                content = result.get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    if isinstance(content[0], dict) and "text" in content[0]:
                        return content[0]["text"]
            return str(result)
        
        except asyncio.TimeoutError:
            self._pending_calls.pop(call_id, None)
            return f"Error: Tool '{tool_name}' timeout"
        except Exception as e:
            self._pending_calls.pop(call_id, None)
            return f"Error: {e}"
    
    async def _send_mcp_message(self, payload: Dict) -> None:
        await self._send({"type": "mcp", "payload": payload})
    
    def get_tool_definitions(self) -> List[ToolDefinition]:
        """
        Get tool definitions (framework-agnostic).
        
        Returns ToolDefinition list, NOT FunctionTool.
        Conversion to FunctionTool happens in Provider layer.
        """
        definitions = []
        for tool_name, tool_data in self._tools.items():
            schema = tool_data.get("inputSchema", {})
            definitions.append(ToolDefinition(
                name=tool_name,
                description=tool_data.get("description", ""),
                parameters={
                    "type": schema.get("type", "object"),
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", [])
                },
                device_client=self  # Reference to self for execution
            ))
        return definitions
    
    def cancel_all(self) -> None:
        for future in self._pending_calls.values():
            if not future.done():
                future.cancel()
        self._pending_calls.clear()
```

### 5.5 抽象层：内置本地工具

```python
# core/tools/local.py

from datetime import datetime
from typing import Callable, List


# ============ Built-in Local Tools ============
# These are simple functions that can be converted by any Agent framework

async def get_time() -> str:
    """Get current date and time"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def get_date() -> str:
    """Get current date"""
    return datetime.now().strftime("%Y-%m-%d")


# Export built-in tools as function list
# Agent frameworks typically support converting functions directly
# (e.g., @function_tool decorator in OpenAI Agents)
BUILTIN_LOCAL_TOOLS: List[Callable] = [get_time, get_date]
```

### 5.6 Provider 实现：OpenAI Agent 工具转换器

```python
# providers/openai_agent/tools.py

import json
from typing import Callable, List
from agents import FunctionTool, function_tool
from agents.tool_context import ToolContext
from core.tools.types import ToolDefinition
from core.tools.converter import ToolConverterBase


class OpenAIAgentToolConverter(ToolConverterBase):
    """
    Converts tools to OpenAI Agents FunctionTool format.
    """
    
    def convert_device_tool(self, tool_def: ToolDefinition) -> FunctionTool:
        """Convert device tool to FunctionTool."""
        
        # Capture tool_def in closure
        tool_name = tool_def.name
        device_client = tool_def.device_client
        
        async def on_invoke_tool(ctx: ToolContext, input_json: str) -> str:
            params = json.loads(input_json) if input_json else {}
            return await device_client.call_tool(tool_name, params)
        
        return FunctionTool(
            name=tool_def.name,
            description=tool_def.description,
            params_json_schema=tool_def.parameters,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=False,
        )
    
    def convert_local_tool(self, func: Callable) -> FunctionTool:
        """
        Convert local function to FunctionTool.
        
        OpenAI Agents framework supports @function_tool decorator natively.
        """
        # Use the framework's built-in decorator
        return function_tool(func)
    
    def convert_mcp_tool(self, tool_def: ToolDefinition) -> FunctionTool:
        """Convert MCP tool to FunctionTool."""
        
        tool_name = tool_def.name
        mcp_client = tool_def.mcp_client
        
        async def on_invoke_tool(ctx: ToolContext, input_json: str) -> str:
            params = json.loads(input_json) if input_json else {}
            return await mcp_client.call_tool(tool_name, params)
        
        return FunctionTool(
            name=tool_def.name,
            description=tool_def.description,
            params_json_schema=tool_def.parameters,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=False,
        )
```

### 5.7 Protocol 扩展

在 `ProtocolBase` 中添加 MCP 消息支持：

```python
# core/protocol.py (扩展)

class ProtocolBase(ABC):
    # ... existing methods ...
    
    def create_mcp_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create MCP message.
        
        Args:
            payload: MCP payload (JSON-RPC 2.0 format)
            
        Returns:
            Message dict with type="mcp"
        """
        return {
            "type": "mcp",
            "payload": payload
        }
    
    def is_mcp_message(self, message: Dict[str, Any]) -> bool:
        """Check if message is MCP type."""
        return message.get("type") == "mcp"
    
    def get_mcp_payload(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract MCP payload from message."""
        if self.is_mcp_message(message):
            return message.get("payload")
        return None
```

### 5.8 Transport 集成

在 Transport 层处理 MCP 消息路由，通过回调通知工具就绪：

```python
# transports/xiaozhi/transport.py (扩展部分)

from typing import Callable, Dict, List
from transports.xiaozhi.device_tools import XiaozhiDeviceToolClient
from core.tools.types import ToolDefinition

class XiaozhiTransport(TransportBase):
    
    def __init__(self, ...):
        super().__init__(...)
        self._device_tool_clients: Dict[str, XiaozhiDeviceToolClient] = {}
        self._device_tools_callbacks: Dict[str, Callable] = {}
    
    def set_device_tools_callback(
        self, 
        session_id: str, 
        callback: Callable[[List[ToolDefinition]], None]
    ) -> None:
        """
        Register callback for when device tools become ready.
        
        Args:
            session_id: Session identifier
            callback: async def (tool_defs: List[ToolDefinition]) -> None
        """
        self._device_tools_callbacks[session_id] = callback
    
    async def _on_hello(self, session_id: str, message: Dict) -> None:
        """Handle hello message."""
        features = message.get("features", {})
        
        if features.get("mcp"):
            device_client = XiaozhiDeviceToolClient(
                session_id=session_id,
                send_callback=lambda msg: self._send_message(session_id, msg)
            )
            self._device_tool_clients[session_id] = device_client
            asyncio.create_task(self._init_device_tools(session_id, device_client))
        
        await self._send_welcome(session_id)
    
    async def _init_device_tools(self, session_id: str, client: XiaozhiDeviceToolClient) -> None:
        """Initialize device tools and notify via callback."""
        success = await client.initialize()
        if success:
            self.logger.info(f"Device tools ready for session {session_id[:8]}")
            
            # Notify callback with tool definitions
            callback = self._device_tools_callbacks.get(session_id)
            if callback:
                tool_defs = client.get_tool_definitions()
                await callback(tool_defs)
    
    def _on_mcp_message(self, session_id: str, message: Dict) -> None:
        client = self._device_tool_clients.get(session_id)
        if client:
            client.on_mcp_message(message.get("payload", {}))
```

---

## 6. 工具更新机制

### 6.1 时序问题

设备工具列表是在 **handshake 之后** 异步获取的，此时 Pipeline 和 Agent 已经创建。
因此需要一个 `update_tools` 接口来动态更新工具。

```
Timeline:
─────────────────────────────────────────────────────────────────────────────►

1. Pipeline 创建        2. Handshake           3. 设备工具就绪
   (Agent 初始化,         (hello 消息)            (tools/list 响应)
    仅 local tools)                               
        │                     │                      │
        │                     │                      │
        ▼                     ▼                      ▼
   agent.initialize()    transport 发送         transport.on_device_tools_ready()
   tools=[local_tools]   initialize +              │
                         tools/list                │
                                                   ▼
                                              agent.update_tools(device_tools)
```

### 6.2 AgentProvider 接口扩展

```python
# providers/agent.py (扩展)

class AgentProvider(BaseProvider):
    """Agent provider interface."""
    
    # ... existing methods ...
    
    @abstractmethod
    async def update_tools(self, tools: List[Any]) -> None:
        """
        Dynamically update agent tools.
        
        Called when device tools become available after initialization.
        
        Args:
            tools: List of framework-specific tools to add
        """
        pass
```

### 6.3 OpenAI Agent 实现

```python
# providers/openai_agent/provider.py (扩展)

class OpenAIAgentProvider(AgentProvider):
    
    async def update_tools(self, tools: List[FunctionTool]) -> None:
        """
        Update agent tools dynamically.
        
        OpenAI Agents framework supports updating tools on existing agent.
        """
        if not self._initialized or not self.agent:
            self.logger.warning("Agent not initialized, cannot update tools")
            return
        
        # Add new tools to agent
        current_tools = list(self.agent.tools) if self.agent.tools else []
        updated_tools = current_tools + tools
        
        # Recreate agent with updated tools
        # (OpenAI Agents SDK may require recreating the agent)
        self.agent = Agent(
            name=self.agent.name,
            model=self.model,
            instructions=self.system_prompt,
            tools=updated_tools,
        )
        
        self.logger.info(f"Agent tools updated: {len(current_tools)} -> {len(updated_tools)}")
```

---

## 7. 完整使用示例

```python
# examples/agent_with_device_tools.py

from core.tools.local import BUILTIN_LOCAL_TOOLS
from providers.openai_agent.tools import OpenAIAgentToolConverter

async def create_pipeline(transport, session_id):
    """Create pipeline with tool support."""
    
    # 1. Create tool converter
    converter = OpenAIAgentToolConverter()
    
    # 2. Convert local tools (available immediately)
    local_tools = [converter.convert_local_tool(func) for func in BUILTIN_LOCAL_TOOLS]
    
    # 3. Add custom local tools
    async def get_weather(city: str) -> str:
        """Get weather for a city"""
        return f"{city}: Sunny, 25°C"
    
    local_tools.append(converter.convert_local_tool(get_weather))
    
    # 4. Create Agent with local tools only (device tools not ready yet)
    agent_provider = OpenAIAgentProvider(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4",
    )
    await agent_provider.initialize(
        tools=local_tools,
        system_prompt="You are a helpful assistant."
    )
    
    # 5. Register callback for when device tools become ready
    async def on_device_tools_ready(tool_defs: List[ToolDefinition]):
        """Called by Transport when device tools are fetched."""
        device_tools = converter.convert_device_tools(tool_defs)
        await agent_provider.update_tools(device_tools)
    
    transport.set_device_tools_callback(session_id, on_device_tools_ready)
    
    # 6. Create pipeline
    stations = [
        VADStation(vad_provider),
        TurnDetectorStation(),
        ASRStation(asr_provider),
        TextAggregatorStation(),
        AgentStation(agent_provider),
        SentenceAggregatorStation(),
        TTSStation(tts_provider),
    ]
    
    return Pipeline(stations=stations)
```

### 依赖关系图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Pipeline Factory / App Layer                          │
│                                                                              │
│   1. converter.convert_local_tool(func) → FunctionTool                      │
│   2. agent_provider.initialize(tools=local_tools)                           │
│   3. transport.set_device_tools_callback(on_device_tools_ready)             │
│                                                                              │
│   Later (async):                                                             │
│   4. on_device_tools_ready(tool_defs) called by Transport                   │
│   5. converter.convert_device_tools(tool_defs) → List[FunctionTool]         │
│   6. agent_provider.update_tools(device_tools)                              │
│                                                                              │
└───────────────────────────────┬───────────────────────────────┬─────────────┘
                                │                               │
            ┌───────────────────┼───────────────────┐           │
            │                   │                   │           │
            ▼                   ▼                   ▼           │
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│  core/tools/      │ │ transports/       │ │ providers/        │
│                   │ │ xiaozhi/          │ │ openai_agent/     │
│  - types.py       │ │                   │ │                   │
│  - device.py      │◄┤ - device_tools.py │ │ - tools.py        │◄─┘
│  - local.py       │ │ - transport.py    │ │ - provider.py     │
│  - converter.py   │ │                   │ │                   │
└───────────────────┘ └───────────────────┘ └───────────────────┘
        ▲                     │                     │
        │                     │                     │
        └─────────────────────┴─────────────────────┘
                    依赖 core，互不依赖
```

---

## 8. 模块结构

```
vixio/
├── core/
│   ├── protocol.py
│   └── tools/                    # 抽象层 (无外部依赖)
│       ├── __init__.py
│       ├── types.py              # ToolDefinition, ToolCallRequest, ToolCallResult
│       ├── device.py             # DeviceToolClientBase 接口
│       ├── local.py              # 本地工具定义 + func_to_tool_definition()
│       └── converter.py          # ToolConverterBase 接口
│
├── providers/
│   └── openai_agent/
│       ├── provider.py
│       └── tools.py              # OpenAIAgentToolConverter (依赖 core + agents)
│
└── transports/
    └── xiaozhi/
        ├── transport.py
        └── device_tools.py       # XiaozhiDeviceToolClient (依赖 core only)
```

### 依赖关系

```
┌─────────────┐     ┌─────────────┐
│  providers  │     │ transports  │
│             │     │             │
│ (agents)    │     │ (xiaozhi)   │
└──────┬──────┘     └──────┬──────┘
       │                   │
       │   依赖 core       │   依赖 core
       │   (不依赖对方)    │   (不依赖对方)
       │                   │
       └─────────┬─────────┘
                 │
                 ▼
         ┌─────────────┐
         │    core     │
         │             │
         │ (tools/)    │
         │             │
         │ 无外部依赖   │
         └─────────────┘
```

### 分层职责

| 层级 | 文件 | 职责 | 依赖 |
|------|------|------|------|
| **core** | `types.py` | `ToolDefinition` 通用数据结构 | 无 |
| **core** | `device.py` | `DeviceToolClientBase` 接口 | types.py |
| **core** | `local.py` | 内置本地工具函数 | 无 |
| **core** | `converter.py` | `ToolConverterBase` 接口 | types.py |
| **provider** | `agent.py` | `AgentProvider.update_tools()` 接口 | core |
| **provider** | `tools.py` | `OpenAIAgentToolConverter` | core + agents |
| **transport** | `device_tools.py` | `XiaozhiDeviceToolClient` | core only |
| **transport** | `transport.py` | `set_device_tools_callback()` | core only |

---

## 9. 实施计划

| 阶段 | 内容 | 位置 | 工作量 |
|------|------|------|--------|
| **P0** | ToolDefinition 通用数据结构 | `core/tools/types.py` | 0.5天 |
| **P1** | DeviceToolClientBase + ToolConverterBase 接口 | `core/tools/device.py`, `converter.py` | 0.5天 |
| **P2** | 内置本地工具 | `core/tools/local.py` | 0.5天 |
| **P3** | XiaozhiDeviceToolClient 实现 | `transports/xiaozhi/device_tools.py` | 1天 |
| **P4** | OpenAIAgentToolConverter 实现 | `providers/openai_agent/tools.py` | 0.5天 |
| **P5** | AgentProvider.update_tools 接口 | `providers/agent.py`, `openai_agent/provider.py` | 0.5天 |
| **P6** | Transport 集成 + callback 机制 | `transports/xiaozhi/transport.py` | 0.5天 |
| **P7** | 示例和测试 | `examples/` | 1天 |

---

## 10. 关键设计决策

1. **依赖倒置**：
   - `core` 定义接口（`ToolDefinition`, `DeviceToolClientBase`, `ToolConverterBase`）
   - `providers` 依赖 `core`，实现 `ToolConverterBase`
   - `transports` 依赖 `core`，实现 `DeviceToolClientBase`
   - `providers` 和 `transports` 互不依赖

2. **分类转换**：
   - `convert_local_tool(func)` - 本地函数转换（利用框架原生支持）
   - `convert_device_tool(ToolDefinition)` - 设备工具转换
   - `convert_mcp_tool(ToolDefinition)` - MCP 工具转换
   - 不做统一的 `convert_tool()` 抽象

3. **动态工具更新**：
   - 设备工具在 handshake 后异步获取，Pipeline 已经创建
   - 通过 `agent_provider.update_tools()` 动态更新
   - 通过 `transport.set_device_tools_callback()` 注册回调

4. **工具获取时机**：hello 后立即发起 initialize + tools/list，不阻塞主流程

5. **工具调用机制**：
   - `ToolDefinition.device_client` 持有 `DeviceToolClientBase` 引用
   - `ToolConverterBase` 在转换时注入调用逻辑
   - 最终执行仍通过 `device_client.call_tool()`

6. **消息路由**：MCP 消息在 Transport 层路由到 DeviceToolClient，不经过 Pipeline

7. **可扩展性**：
   - 新增 Agent 框架：实现 `ToolConverterBase`（如 `LangGraphToolConverter`）
   - 新增 Transport 协议：实现 `DeviceToolClientBase`（如 `LiveKitDeviceToolClient`）
