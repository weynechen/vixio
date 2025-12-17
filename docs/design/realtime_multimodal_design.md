# Realtime 多模态实时聊天设计方案

## 1. 背景与目标

为了适配 Qwen Omni Realtime、OpenAI Realtime API 以及未来可能出现的类似多模态实时模型（Realtime Multimodal Models），我们需要在 Vixio 框架中抽象出一套通用的 Realtime 交互机制。

这些模型通常具有以下特征：
- **全双工/流式交互**：通过 WebSocket 长连接进行音频/视频/文本的双向流式传输。
- **端到端集成**：内置 VAD + ASR + LLM + TTS，打破了传统的级联处理流程。
- **状态管理**：连接具有上下文状态（Session），支持动态更新配置（如 Prompt, Tools）。
- **工具调用**：支持服务端判断并发起工具调用（Function Calling）。

本设计旨在提供一个统一的抽象层，屏蔽不同厂商协议的差异，同时保持与 Vixio 现有 DAG 和工具体系的兼容性。

## 2. 核心架构

架构采用 **Provider-Station** 分层模式：

```
[Transport] <-> [RealtimeStation] <-> [BaseRealtimeProvider] <-> [SpecificProvider (Qwen/OpenAI)] <-> [Cloud API]
                       ^
                       | (Events / Chunks)
                       v
                [Downstream Nodes]
                (Logger, ToolExecutor, etc.)
```

### 2.1 目录结构调整

```
src/vixio/
  providers/
    realtime.py       # [NEW] 定义 BaseRealtimeProvider 基类
    qwen/
      qwen_omni_realtime.py  # 继承 BaseRealtimeProvider
    openai/
      openai_realtime.py     # [NEW] 继承 BaseRealtimeProvider
  stations/
    realtime.py       # 更新 RealtimeStation 以适配新基类
  core/
    chunk.py          # 扩展 ChunkType 以支持 Tool Call
```

## 3. 详细设计

### 3.1 BaseRealtimeProvider 基类

在 `src/vixio/providers/realtime.py` 中定义抽象基类，规范所有 Realtime Provider 的行为。

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Callable, Any
from vixio.providers.base import BaseProvider
from vixio.core.tools.types import ToolDefinition

class BaseRealtimeProvider(BaseProvider, ABC):
    """
    Realtime Provider 基类。
    负责管理 WebSocket 连接、音频流传输、事件转换和状态管理。
    """
    
    def __init__(self, name: str):
        super().__init__(name=name)
        self._event_handler: Optional[Callable[[Any], None]] = None

    def set_event_handler(self, handler: Callable[[Any], None]):
        """设置事件回调，通常由 Station 注册，用于接收 Chunk"""
        self._event_handler = handler

    @abstractmethod
    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        pass

    @abstractmethod
    async def update_session(self, 
                           instructions: Optional[str] = None, 
                           tools: Optional[List[ToolDefinition]] = None,
                           voice: Optional[str] = None) -> None:
        """
        动态更新会话配置。
        
        Args:
            instructions: System prompt
            tools: 可用的工具列表 (使用框架通用的 ToolDefinition)
            voice: 音色配置
        """
        pass

    @abstractmethod
    async def send_audio(self, audio_data: bytes) -> None:
        """发送音频数据 (PCM)"""
        pass

    @abstractmethod
    async def send_tool_output(self, tool_call_id: str, output: str) -> None:
        """回传工具执行结果给模型"""
        pass

    @abstractmethod
    async def interrupt(self) -> None:
        """打断当前生成（用于客户端主动打断）"""
        pass
        
    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass
```

### 3.2 ChunkType 扩展

为了支持 Realtime 特有的工具调用和更丰富的事件，需要在 `src/vixio/core/chunk.py` 中扩展 `ChunkType` 和数据类。

#### 3.2.1 新增 ChunkType

```python
class ChunkType(str, Enum):
    # ... 现有类型 ...
    
    # Realtime 特有事件
    EVENT_REALTIME_CONNECTED = "event.realtime.connected"
    EVENT_REALTIME_SESSION_UPDATED = "event.realtime.session_updated"
    
    # 工具调用相关 (Data Chunks)
    TOOL_CALL = "tool.call"       # 模型发起工具调用请求
    TOOL_OUTPUT = "tool.output"   # 工具执行结果
```

#### 3.2.2 新增数据类

**ToolCallChunk**:
```python
@dataclass
class ToolCallChunk(Chunk):
    """
    模型发起的工具调用请求。
    """
    type: ChunkType = ChunkType.TOOL_CALL
    call_id: str = ""           # 唯一调用 ID
    tool_name: str = ""         # 工具名称
    arguments: Dict[str, Any] = field(default_factory=dict) # 参数
    
    def __repr__(self) -> str:
        return f"ToolCallChunk({self.tool_name}, id={self.call_id})"
```

**ToolOutputChunk**:
```python
@dataclass
class ToolOutputChunk(Chunk):
    """
    工具执行结果，准备回传给模型。
    """
    type: ChunkType = ChunkType.TOOL_OUTPUT
    call_id: str = ""           # 对应的调用 ID
    output: str = ""            # 执行结果 (通常为 JSON 字符串)
    is_error: bool = False      # 是否执行出错
```

### 3.3 工具调用流程 (Tool Usage)

Realtime 场景下的工具调用是异步且双向的：

1.  **定义工具**: 用户在配置中定义工具（`LocalTool` 或 `DeviceTool`）。
2.  **注册工具**: `RealtimeStation` 初始化时，将 `List[ToolDefinition]` 传递给 `provider.update_session(tools=...)`。Provider 负责将其转换为厂商特定的 JSON Schema（如 OpenAI Function 格式）。
3.  **触发调用**:
    *   用户说话 -> 模型判断需调用工具。
    *   Provider 收到 WebSocket `function_call` 事件。
    *   Provider 封装为 `ToolCallChunk` 并通过 `on_event` 回调发出。
4.  **执行工具**:
    *   `RealtimeStation` 接收 `ToolCallChunk`。
    *   **方案 A (Station 内部执行)**: 如果 Station 绑定了 `ToolExecutor`，直接执行并获取结果。
    *   **方案 B (下游执行)**: 将 Chunk 发送给下游的 `ToolNode` 执行。
5.  **回传结果**:
    *   执行结果封装为 `ToolOutputChunk`。
    *   `RealtimeStation` 调用 `provider.send_tool_output(call_id, output)`。
    *   Provider 将结果发送回 WebSocket。
    *   模型根据结果继续生成语音。

### 3.4 事件映射与 Chunk 定义 (Event Mapping)

Provider 负责将厂商特定的事件映射为 Vixio 标准 Chunk。为了保持兼容性，我们尽可能复用现有的 ChunkType，并通过 `source` 和语义来区分。

| 厂商事件 (概念) | Vixio ChunkType | 构造示例 | 说明 |
| :--- | :--- | :--- | :--- |
| **Bot Audio Stream**<br>(`response.audio.delta`) | `AUDIO_RAW` | `AudioChunk(data=bytes, source="bot")` | 机器人回复的音频流。Transport 层直接播放。 |
| **Bot Text Stream**<br>(`response.audio_transcript.delta`) | `TEXT_DELTA` | `TextDeltaChunk(data=str, source="bot")` | 机器人回复的字幕流。用于 UI 展示。 |
| **User Transcript**<br>(`input_audio_transcription.completed`) | `TEXT` | `TextChunk(data=str, source="user")` | 用户语音识别结果。用于 UI 展示用户输入。 |
| **VAD Start**<br>(`speech_started`) | `EVENT_VAD_START` | `EventChunk(type=EVENT_VAD_START)` | 用户开始说话。Transport 可用于打断播放。 |
| **VAD End**<br>(`speech_stopped`) | `EVENT_VAD_END` | `EventChunk(type=EVENT_VAD_END)` | 用户停止说话。 |
| **Tool Call**<br>(`function_call`) | `TOOL_CALL` | `ToolCallChunk(...)` | **[新增]** 模型请求调用工具。 |

**关于流式传输的说明**：
*   **Bot Audio**: 始终使用 `AUDIO_RAW` 流式发送，确保音频实时性。
*   **Bot Text**: 针对不支持 Delta 更新的设备（如 Xiaozhi），在 DAG 中接入 `SentenceAggregatorStation`。RealtimeStation 输出 `TEXT_DELTA` -> `SentenceAggregatorStation` 聚合成 `TEXT` -> Transport。对于支持流式的客户端，则直通。
*   **User Transcript**: 使用 `TEXT` (source="user")，即在 `transcript.done` 后发送完整句子。

1.  **Qwen Omni**: 目前主要通过 `dashscope` SDK 封装。适配层需要将 SDK 的回调 (`on_event`) 转换为上述标准 Chunk。
2.  **OpenAI Realtime**: 使用 WebSocket 直接连接。适配层需处理 JSON 序列化/反序列化，并维护 `response.create` 等状态。
3.  **普通 Provider**: 该设计不影响非 Realtime 的 Provider（如 `OpenAILLMProvider`），它们继续使用 `generate` 接口。`BaseRealtimeProvider` 是一个并行的基类体系，专门处理流式双工场景。

## 5. 实施步骤

1.  创建 `src/vixio/providers/realtime.py`。
2.  更新 `src/vixio/core/chunk.py` 添加 Tool 相关的 Chunk。
3.  重构 `src/vixio/stations/realtime.py` 使用新的基类和事件处理逻辑。
4.  更新 `src/vixio/providers/qwen/qwen_omni_realtime.py` 适配新基类。
