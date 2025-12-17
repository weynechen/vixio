# Transport 架构设计文档

## 1. 概述

本文档定义了 Vixio 框架中 Transport 层的架构设计，包括各组件的职责边界、接口定义以及扩展机制。

### 1.1 设计目标

1. **职责单一**：每个组件只负责一件事
2. **解耦彻底**：Pipeline 与 Transport 通过队列解耦
3. **框架提供公共能力**：Turn 管理、Latency 跟踪等由框架统一实现
4. **协议可扩展**：新协议只需实现必要接口，无需关心框架细节

### 1.2 架构图

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              Pipeline                                       │
│  ┌─────────────┐   ┌─────┐   ┌─────┐   ┌─────┐   ┌──────────────┐         │
│  │InputStation │ → │ VAD │ → │ ASR │ → │ TTS │ → │ OutputStation│         │
│  │ (格式转换)  │   │     │   │     │   │     │   │  (格式转换)  │         │
│  └──────┬──────┘   └─────┘   └─────┘   └─────┘   └───────┬──────┘         │
│         │ read from queue                     put to queue │                │
└─────────│──────────────────────────────────────────────────│────────────────┘
          │                                                  │
          ▼                                                  ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                         TransportBase (框架层)                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                     公共能力 (框架实现)                                │ │
│  │  • Turn 管理: TTS_STOP 后自动 increment_turn                          │ │
│  │  • Latency 跟踪: first_audio_sent 自动记录                            │ │
│  │  • Worker 管理: 强制 read_worker + send_worker                        │ │
│  │  • 输出控制: FlowController (上层控制) | PlayoutTracker (底层控制)    │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌──────────────────────┐                   ┌──────────────────────┐      │
│  │   Audio read_queue   │ ← read_worker ←   │   Audio send_queue   │      │
│  │  (供InputStation读取) │                   │ (OutputStation写入)   │      │
│  └──────────────────────┘                   └───────────┬──────────┘      │
│                                                         │                  │
│  ┌──────────────────────┐                               ▼                  │
│  │  Video read_queue    │ (预留)            ┌──────────────────────┐      │
│  │  (供未来视频输入)    │                   │     send_worker      │      │
│  └──────────────────────┘                   │    (框架实现)        │      │
│                                             │                      │      │
│  ┌──────────────────────┐                   │ ┌──────────────────┐ │      │
│  │  Video send_queue    │ (预留)            │ │二选一策略:       │ │      │
│  │  (供未来视频输出)    │                   │ │A. FlowController │ │      │
│  └──────────────────────┘                   │ │   (上层主动控制) │ │      │
│                                             │ │B. PlayoutTracker │ │      │
│                                             │ │   (底层控制节奏) │ │      │
│                                             │ └──────────────────┘ │      │
│                                             │ • Turn管理          │      │
│                                             │ • Latency记录       │      │
│                                             └───────────┬──────────┘      │
│                                                         │                  │
└─────────────────────────────────────────────────────────│──────────────────┘
                                                          │
                                                          ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        具体协议实现 (协议方实现)                            │
│                                                                            │
│  XiaozhiTransport / LiveKitTransport / WebRTCTransport / ...              │
│                                                                            │
│  必须实现:                                                                 │
│  • _do_read(): 从连接读取 raw data                                         │
│  • _do_write(): 向连接写入 raw data                                        │
│  • _create_protocol(): 创建协议处理器                                      │
│  • _create_audio_codec(): 创建音频编解码器                                 │
│                                                                            │
│  可选实现:                                                                 │
│  • _create_output_controller(): 创建输出控制器 (二选一，或None)            │
│  • _on_session_start(): 会话开始时的钩子                                   │
│  • _on_session_end(): 会话结束时的钩子                                     │
└────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 输出控制策略 (二选一)

| 策略 | 适用场景 | 原理 | 示例协议 |
|------|---------|------|---------|
| **FlowController** | 上层主动控制 | 框架调用 `wait_for_next()` 控制发送间隔 | Xiaozhi (60ms帧间隔) |
| **PlayoutTracker** | 底层控制 | 框架直接发送，等待底层 `on_playout_finished` 回调 | WebRTC, LiveKit |

**选择原则**:
- 如果底层协议 (如WebRTC) 有自己的流控/缓冲机制 → 使用 **PlayoutTracker**
- 如果需要上层精确控制发送节奏 (如自定义WebSocket) → 使用 **FlowController**

---

## 2. 组件职责定义

### 2.1 Pipeline 层

Pipeline 是业务处理流水线，只关心 Chunk 的流转和处理。

| 组件 | 职责 | 特点 |
|------|------|------|
| InputStation | raw data → Chunk 格式转换 | 从 read_queue 读取，输出 Chunk |
| OutputStation | Chunk → raw data 格式转换 | 接收 Chunk，写入 send_queue |
| 其他 Station | 业务处理 (VAD/ASR/TTS等) | 纯 Chunk 处理 |

### 2.2 Transport 框架层 (TransportBase)

框架层提供公共能力，所有具体协议实现必须继承。

| 能力 | 说明 | 实现方 |
|------|------|--------|
| read_worker | 从连接读取数据放入 read_queue | 框架 |
| send_worker | 从 send_queue 取数据发送到连接 | 框架 |
| Turn 管理 | 检测 TTS_STOP，自动 increment_turn | 框架 |
| Latency 跟踪 | 记录 first_audio_sent | 框架 |
| 流控支持 | 可选，调用协议方提供的 FlowController | 框架 |

### 2.3 具体协议实现层

具体协议只需实现与连接相关的最小接口。

| 接口 | 职责 | 必须/可选 |
|------|------|----------|
| _do_read() | 从物理连接读取数据 | 必须 |
| _do_write() | 向物理连接写入数据 | 必须 |
| _create_protocol() | 创建 Protocol 实例 | 必须 |
| _create_audio_codec() | 创建 AudioCodec 实例 | 必须 |
| _create_flow_controller() | 创建 FlowController | 可选 |

---

## 3. 接口定义

### 3.1 TransportBase (框架层)

```python
"""
Transport 基类 - 框架层实现

提供公共能力：
- read_worker / send_worker 管理
- Turn 管理 (TTS_STOP 后自动 increment)
- Latency 跟踪 (first_audio_sent)
- 流控支持 (可选)
"""

from abc import ABC, abstractmethod
from typing import Optional, Any, Dict
from asyncio import Queue
from core.protocol import ProtocolBase
from core.control_bus import ControlBus


class TransportBase(ABC):
    """
    Transport 基类.
    
    框架层实现公共能力，具体协议只需实现 _do_read / _do_write 等抽象方法。
    """
    
    # ============ 生命周期管理 ============
    
    @abstractmethod
    async def start(self) -> None:
        """启动 Transport 服务 (如 WebSocket server)"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止 Transport 服务"""
        pass
    
    # ============ 对 Pipeline 暴露的接口 ============
    
    def get_read_queue(self, session_id: str) -> Queue:
        """
        获取读取队列 (供 InputStation 使用).
        
        框架实现: 内部 read_worker 会将 _do_read() 的数据放入此队列。
        
        Args:
            session_id: 会话 ID
            
        Returns:
            asyncio.Queue: 包含 raw data (bytes/str) 的队列
        """
        pass  # 框架实现
    
    def get_send_queue(self, session_id: str) -> Queue:
        """
        获取发送队列 (供 OutputStation 使用).
        
        框架实现: OutputStation 将数据放入此队列，
        内部 send_worker 会取出并调用 _do_write() 发送。
        
        Args:
            session_id: 会话 ID
            
        Returns:
            asyncio.Queue: OutputStation 写入 raw data 的队列
        """
        pass  # 框架实现
    
    def get_protocol(self) -> ProtocolBase:
        """
        获取协议处理器 (供 InputStation/OutputStation 使用).
        
        Returns:
            ProtocolBase: 协议实例
        """
        pass  # 框架实现，调用 _create_protocol()
    
    def get_audio_codec(self, session_id: str) -> Any:
        """
        获取音频编解码器 (供 InputStation/OutputStation 使用).
        
        Args:
            session_id: 会话 ID
            
        Returns:
            AudioCodec: 编解码器实例，或 None (如果协议不需要)
        """
        pass  # 框架实现，调用 _create_audio_codec()
    
    # ============ ControlBus 集成 ============
    
    def set_control_bus(self, session_id: str, control_bus: ControlBus) -> None:
        """
        设置 ControlBus (由 SessionManager 调用).
        
        框架用于：
        - Turn 管理: TTS_STOP 后调用 control_bus.increment_turn()
        
        Args:
            session_id: 会话 ID
            control_bus: ControlBus 实例
        """
        pass  # 框架实现
    
    # ============ Latency 跟踪集成 ============
    
    def set_latency_monitor(self, latency_monitor: Any) -> None:
        """
        设置 Latency Monitor (由 SessionManager 调用).
        
        框架用于：
        - 记录 first_audio_sent 时间点
        - 自动生成 latency report
        
        Args:
            latency_monitor: LatencyMonitor 实例
        """
        pass  # 框架实现
    
    # ============ 连接回调 ============
    
    def on_new_connection(self, handler) -> None:
        """
        注册新连接回调.
        
        Args:
            handler: async def handler(session_id: str) -> None
        """
        pass  # 框架实现
    
    async def on_pipeline_ready(self, session_id: str) -> None:
        """
        Pipeline 就绪回调 (发送握手消息等).
        
        框架实现: 调用 protocol.handshake() 并发送。
        协议方可覆盖此方法添加自定义逻辑。
        
        Args:
            session_id: 会话 ID
        """
        pass  # 框架实现，可覆盖
    
    # ============ 协议方必须实现的抽象方法 ============
    
    @abstractmethod
    async def _do_read(self, session_id: str) -> Optional[bytes | str]:
        """
        从物理连接读取一条消息.
        
        协议方实现: 从 WebSocket/TCP/等 读取原始数据。
        
        Args:
            session_id: 会话 ID
            
        Returns:
            raw data (bytes 或 str)，连接关闭时返回 None
            
        Raises:
            ConnectionError: 连接异常
        """
        pass
    
    @abstractmethod
    async def _do_write(self, session_id: str, data: bytes | str) -> None:
        """
        向物理连接写入一条消息.
        
        协议方实现: 向 WebSocket/TCP/等 写入原始数据。
        
        Args:
            session_id: 会话 ID
            data: 要发送的 raw data
            
        Raises:
            ConnectionError: 连接异常
        """
        pass
    
    @abstractmethod
    def _create_protocol(self) -> ProtocolBase:
        """
        创建协议处理器.
        
        协议方实现: 返回具体协议的 Protocol 实例。
        
        Returns:
            ProtocolBase: 协议实例 (如 XiaozhiProtocol)
        """
        pass
    
    @abstractmethod
    def _create_audio_codec(self, session_id: str) -> Any:
        """
        创建音频编解码器.
        
        协议方实现: 返回具体协议需要的音频编解码器。
        如果协议不需要音频编解码，返回 None。
        
        Args:
            session_id: 会话 ID
            
        Returns:
            AudioCodec 实例，或 None
        """
        pass
    
    # ============ 协议方可选实现的方法 ============
    
    def _create_output_controller(self, session_id: str) -> Any:
        """
        创建输出控制器 (二选一策略).
        
        协议方实现: 根据底层能力选择返回:
        - FlowControllerBase: 上层主动控制节奏 (如 Xiaozhi 60ms音频帧)
        - PlayoutTrackerBase: 底层控制节奏 (如 WebRTC)
        - None: 无需输出控制
        
        两种策略互斥，框架根据返回类型自动适配行为。
        
        Args:
            session_id: 会话 ID
            
        Returns:
            FlowControllerBase | PlayoutTrackerBase | None
        """
        return None  # 默认无需输出控制
    
    async def _on_session_start(self, session_id: str) -> None:
        """
        会话开始钩子 (可选).
        
        协议方可覆盖: 在会话开始时执行自定义逻辑。
        
        Args:
            session_id: 会话 ID
        """
        pass  # 默认空实现
    
    async def _on_session_end(self, session_id: str) -> None:
        """
        会话结束钩子 (可选).
        
        协议方可覆盖: 在会话结束时执行清理逻辑。
        
        Args:
            session_id: 会话 ID
        """
        pass  # 默认空实现
    
    def _is_tts_stop_message(self, data: bytes | str) -> bool:
        """
        判断是否为 TTS_STOP 消息 (可选覆盖).
        
        框架默认实现: 尝试用 protocol 解析并判断。
        协议方可覆盖以提供更高效的判断。
        
        用于 Turn 管理: send_worker 检测到 TTS_STOP 后自动 increment_turn。
        
        Args:
            data: 要发送的 raw data
            
        Returns:
            True 如果是 TTS_STOP 消息
        """
        pass  # 框架有默认实现，可覆盖
```

### 3.2 ProtocolBase (协议层)

```python
"""
Protocol 基类 - 消息编解码和业务接口

职责:
1. 消息编解码 (低级): decode_message / encode_message
2. Chunk 转换: message_to_chunk / chunk_to_message
3. 业务接口 (高级): handshake / send_stt / send_tts 等
"""

from abc import ABC, abstractmethod
from typing import Union, Dict, Any, Optional
from core.chunk import Chunk


class ProtocolBase(ABC):
    """
    Protocol 基类.
    
    定义消息编解码和 Chunk 转换接口。
    """
    
    # ============ 消息编解码 (必须实现) ============
    
    @abstractmethod
    def decode_message(self, data: Union[bytes, str]) -> Dict[str, Any]:
        """
        解码原始数据为消息字典.
        
        Args:
            data: 原始数据 (从 Transport 接收)
            
        Returns:
            消息字典 (包含 type 等字段)
        """
        pass
    
    @abstractmethod
    def encode_message(self, message: Dict[str, Any]) -> Union[bytes, str]:
        """
        编码消息字典为原始数据.
        
        Args:
            message: 消息字典
            
        Returns:
            原始数据 (发送给 Transport)
        """
        pass
    
    # ============ Chunk 转换 (必须实现) ============
    
    @abstractmethod
    def message_to_chunk(
        self, 
        message: Dict[str, Any], 
        session_id: str, 
        turn_id: int
    ) -> Optional[Chunk]:
        """
        将协议消息转换为 Chunk (供 InputStation 使用).
        
        Args:
            message: 解码后的消息字典
            session_id: 会话 ID
            turn_id: 当前 Turn ID
            
        Returns:
            Chunk 实例，或 None (如果消息不需要转换)
        """
        pass
    
    @abstractmethod
    def chunk_to_message(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        """
        将 Chunk 转换为协议消息 (供 OutputStation 使用).
        
        Args:
            chunk: Chunk 实例
            
        Returns:
            消息字典，或 None (如果需要特殊处理)
        """
        pass
    
    # ============ 业务接口 (可选实现) ============
    
    def handshake(self, session_id: str, **params) -> Optional[Dict[str, Any]]:
        """创建握手消息 (如 HELLO)."""
        return None
    
    def send_stt(self, session_id: str, text: str, **params) -> Optional[Dict[str, Any]]:
        """创建 STT (语音识别结果) 消息."""
        return None
    
    def send_tts_event(
        self, 
        session_id: str, 
        event: str, 
        text: Optional[str] = None, 
        **params
    ) -> Optional[Dict[str, Any]]:
        """创建 TTS 事件消息 (start/stop/sentence_start 等)."""
        return None
    
    def send_state(self, session_id: str, state: str, **params) -> Optional[Dict[str, Any]]:
        """创建状态消息 (listening/speaking/idle 等)."""
        return None
```

### 3.3 InputStation (格式转换层)

```python
"""
InputStation - 输入格式转换

职责:
1. 从 Transport 的 read_queue 读取 raw data
2. 调用 Protocol 解码消息
3. 调用 Protocol 转换为 Chunk
4. 调用 AudioCodec 解码音频 (如需要)
5. 输出 Chunk 到 Pipeline
"""

from typing import AsyncIterator, Optional, Any
from asyncio import Queue
from core.station import Station
from core.protocol import ProtocolBase
from core.chunk import Chunk, ChunkType


class InputStation(Station):
    """
    输入格式转换 Station.
    
    作为 Pipeline 的起点，负责将 Transport 的 raw data 转换为 Chunk。
    """
    
    def __init__(
        self,
        session_id: str,
        read_queue: Queue,           # 从 Transport 获取
        protocol: ProtocolBase,      # 从 Transport 获取
        audio_codec: Optional[Any],  # 从 Transport 获取，可为 None
        name: str = "InputStation"
    ):
        """
        Args:
            session_id: 会话 ID
            read_queue: Transport 的读取队列
            protocol: 协议处理器
            audio_codec: 音频编解码器 (可为 None)
            name: Station 名称
        """
        pass
    
    async def _generate_chunks(self) -> AsyncIterator[Chunk]:
        """
        生成 Chunk 流.
        
        流程:
        1. 从 read_queue 获取 raw data
        2. protocol.decode_message() 解码
        3. protocol.message_to_chunk() 转换
        4. audio_codec.decode() 解码音频 (如需要)
        5. yield Chunk
        """
        pass
```

### 3.4 OutputStation (格式转换层)

```python
"""
OutputStation - 输出格式转换

职责:
1. 从 Pipeline 接收 Chunk
2. 调用 AudioCodec 编码音频 (如需要)
3. 调用 Protocol 转换为消息
4. 调用 Protocol 编码消息
5. 写入 Transport 的 send_queue

注意: OutputStation 不负责:
- Flow control (由 Transport.send_worker 处理)
- Turn 管理 (由 Transport.send_worker 处理)
- Latency 跟踪 (由 Transport.send_worker 处理)
"""

from typing import AsyncIterator, Optional, Any
from asyncio import Queue
from core.station import Station
from core.protocol import ProtocolBase
from core.chunk import Chunk, ChunkType


class OutputStation(Station):
    """
    输出格式转换 Station.
    
    作为 Pipeline 的终点，负责将 Chunk 转换为 raw data 并写入 Transport 队列。
    """
    
    def __init__(
        self,
        session_id: str,
        send_queue: Queue,           # 从 Transport 获取
        protocol: ProtocolBase,      # 从 Transport 获取
        audio_codec: Optional[Any],  # 从 Transport 获取，可为 None
        name: str = "OutputStation"
    ):
        """
        Args:
            session_id: 会话 ID
            send_queue: Transport 的发送队列
            protocol: 协议处理器
            audio_codec: 音频编解码器 (可为 None)
            name: Station 名称
        """
        pass
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        """
        处理 Chunk 并写入发送队列.
        
        流程:
        1. audio_codec.encode() 编码音频 (如需要)
        2. protocol.chunk_to_message() 转换
        3. protocol.encode_message() 编码
        4. send_queue.put() 写入队列
        
        注意: 此方法不 yield 任何 Chunk (OutputStation 是终点)
        """
        pass
```

### 3.5 OutputController (二选一策略)

**重要设计决策**: 输出控制有两种互斥策略，协议方根据底层能力二选一：

| 策略 | 适用场景 | 实现方式 |
|------|---------|---------|
| **FlowController** | 上层主动控制节奏 (如Xiaozhi) | 框架在 send_worker 中调用 wait_for_next() |
| **PlayoutTracker** | 底层控制节奏 (如WebRTC) | 框架等待底层播放完成回调 |

```python
"""
OutputController - 输出控制接口 (二选一)

策略 A: FlowController - 上层主动控制发送节奏
  - 适用于: 自定义协议 (如Xiaozhi需要60ms音频帧间隔)
  - 框架行为: send_worker 中调用 wait_for_next() 后再发送

策略 B: PlayoutTracker - 底层控制发送节奏
  - 适用于: WebRTC等底层有自己流控机制的协议
  - 框架行为: 直接发送，等待底层 on_playout_finished 回调
"""

from abc import ABC, abstractmethod
from typing import Optional


class FlowControllerBase(ABC):
    """
    策略 A: 流控器 - 上层主动控制发送节奏.
    
    适用场景:
    - 自定义 WebSocket 协议
    - 需要精确控制音频帧间隔 (如 60ms)
    - 底层没有自己的流控机制
    
    框架在 send_worker 中调用:
        if flow_controller:
            await flow_controller.wait_for_next()
        await _do_write(data)
    """
    
    @abstractmethod
    async def wait_for_next(self) -> None:
        """
        等待下一个发送时机.
        
        实现示例 (60ms音频帧间隔):
            current = time.monotonic()
            sleep_time = max(0, self._next_send_time - current)
            await asyncio.sleep(sleep_time)
            self._next_send_time = current + 0.06  # 60ms
        """
        pass
    
    def reset(self) -> None:
        """重置流控状态 (新 Turn 开始时调用)."""
        pass


class PlayoutTrackerBase(ABC):
    """
    策略 B: 播放追踪器 - 底层控制发送节奏.
    
    适用场景:
    - WebRTC (底层自动处理流控)
    - LiveKit (rtc.AudioSource 内部控制)
    - 需要等待播放完成的场景
    
    框架行为:
    - 直接调用 _do_write() 发送 (不等待)
    - 发送后等待 on_playout_finished() 回调
    """
    
    @abstractmethod
    async def wait_for_playout(self) -> "PlayoutResult":
        """
        等待当前音频播放完成.
        
        Returns:
            PlayoutResult: 播放结果 (播放时长、是否被打断等)
        """
        pass
    
    @abstractmethod
    def on_playout_finished(
        self, 
        playback_position: float, 
        interrupted: bool
    ) -> None:
        """
        底层播放完成回调.
        
        Args:
            playback_position: 实际播放时长 (秒)
            interrupted: 是否被打断
        """
        pass
    
    def flush(self) -> None:
        """标记当前段播放完成."""
        pass
    
    def clear(self) -> None:
        """清空缓冲区 (打断时调用)."""
        pass


class PlayoutResult:
    """播放结果"""
    playback_position: float  # 实际播放时长 (秒)
    interrupted: bool         # 是否被打断
```

### 3.6 AudioCodec (可选组件)

```python
"""
AudioCodec - 音频编解码接口

用于需要音频编解码的协议 (如 Opus)。
协议方可选实现。
"""

from abc import ABC, abstractmethod


class AudioCodecBase(ABC):
    """
    音频编解码器基类.
    """
    
    @abstractmethod
    def encode(self, pcm_data: bytes) -> bytes:
        """
        编码 PCM 为协议格式 (如 Opus).
        
        Args:
            pcm_data: PCM 音频数据
            
        Returns:
            编码后的数据
        """
        pass
    
    @abstractmethod
    def decode(self, encoded_data: bytes) -> bytes:
        """
        解码协议格式为 PCM.
        
        Args:
            encoded_data: 编码的音频数据
            
        Returns:
            PCM 音频数据
        """
        pass
```

---

## 4. 框架层实现细节 (send_worker)

### 4.1 send_worker 伪代码 (二选一策略)

```python
async def _send_worker(self, session_id: str) -> None:
    """
    发送 Worker (框架实现).
    
    职责:
    1. 从 send_queue 取数据
    2. 输出控制 (二选一策略)
    3. 发送数据
    4. Latency 跟踪 (first_audio_sent)
    5. Turn 管理 (TTS_STOP 检测)
    """
    send_queue = self._send_queues[session_id]
    output_controller = self._output_controllers.get(session_id)
    control_bus = self._control_buses.get(session_id)
    latency_monitor = self._latency_monitor
    
    first_audio_recorded = False
    
    # 判断输出控制策略
    is_flow_control = isinstance(output_controller, FlowControllerBase)
    is_playout_track = isinstance(output_controller, PlayoutTrackerBase)
    
    while self._running:
        # 1. 从队列取数据
        data = await send_queue.get()
        if data is None:
            break
        
        # 2. 输出控制 (二选一策略)
        if is_flow_control:
            # 策略 A: 上层主动控制 - 等待后再发送
            await output_controller.wait_for_next()
        
        # 3. 发送数据
        await self._do_write(session_id, data)
        
        # 4. Latency 跟踪: 记录 first_audio_sent
        if not first_audio_recorded and self._is_audio_message(data):
            if latency_monitor:
                turn_id = control_bus.get_current_turn_id() if control_bus else 0
                latency_monitor.record(session_id, turn_id, "first_audio_sent")
                latency_monitor.log_report(session_id, turn_id)
                latency_monitor.print_summary(session_id, turn_id)
            first_audio_recorded = True
        
        # 5. Turn 管理: 检测 TTS_STOP，increment_turn
        if self._is_tts_stop_message(data):
            # 策略 B: 底层控制 - 等待播放完成
            if is_playout_track:
                result = await output_controller.wait_for_playout()
                # 可以根据 result.interrupted 处理打断情况
            
            if control_bus:
                await control_bus.increment_turn(
                    source="Transport",
                    reason="bot_finished"
                )
            # 重置状态准备下一个 Turn
            first_audio_recorded = False
            if is_flow_control:
                output_controller.reset()
            elif is_playout_track:
                output_controller.flush()
```

### 4.2 两种策略对比

```
策略 A: FlowController (如 Xiaozhi)
┌─────────────────────────────────────────────────────────────┐
│  send_queue    wait_for_next()    _do_write()              │
│      │              │                  │                    │
│      ▼              ▼                  ▼                    │
│   [data] ──────► [等待60ms] ──────► [发送到WebSocket]      │
│      │              │                  │                    │
│      ▼              ▼                  ▼                    │
│   [data] ──────► [等待60ms] ──────► [发送到WebSocket]      │
│                                                             │
│  上层主动控制发送节奏                                       │
└─────────────────────────────────────────────────────────────┘

策略 B: PlayoutTracker (如 WebRTC/LiveKit)
┌─────────────────────────────────────────────────────────────┐
│  send_queue    _do_write()    wait_for_playout()           │
│      │              │                  │                    │
│      ▼              ▼                  │                    │
│   [data] ──────► [发送到RTC] ─────────►│                    │
│   [data] ──────► [发送到RTC] ─────────►│                    │
│   [data] ──────► [发送到RTC] ─────────►│                    │
│   [STOP] ──────► [发送到RTC] ──────► [等待播放完成回调]    │
│                                                             │
│  底层控制发送节奏，框架只在结束时等待                       │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 read_worker 伪代码

```python
async def _read_worker(self, session_id: str) -> None:
    """
    读取 Worker (框架实现).
    
    职责:
    1. 调用 _do_read() 从连接读取
    2. 放入 read_queue
    """
    read_queue = self._read_queues[session_id]
    
    try:
        while self._running:
            # 从连接读取
            data = await self._do_read(session_id)
            
            if data is None:
                # 连接关闭
                break
            
            # 放入队列
            await read_queue.put(data)
    
    except ConnectionError:
        pass
    
    finally:
        # 放入 None 表示流结束
        await read_queue.put(None)
```

---

## 5. 扩展新协议指南

### 5.1 最小实现

要支持新协议，只需实现以下内容:

```python
class MyProtocolTransport(TransportBase):
    """新协议 Transport 实现"""
    
    async def start(self) -> None:
        """启动服务"""
        pass
    
    async def stop(self) -> None:
        """停止服务"""
        pass
    
    async def _do_read(self, session_id: str) -> Optional[bytes | str]:
        """从连接读取"""
        pass
    
    async def _do_write(self, session_id: str, data: bytes | str) -> None:
        """向连接写入"""
        pass
    
    def _create_protocol(self) -> ProtocolBase:
        """创建协议处理器"""
        return MyProtocol()
    
    def _create_audio_codec(self, session_id: str) -> Any:
        """创建音频编解码器 (如不需要返回 None)"""
        return None


class MyProtocol(ProtocolBase):
    """新协议消息处理"""
    
    def decode_message(self, data):
        pass
    
    def encode_message(self, message):
        pass
    
    def message_to_chunk(self, message, session_id, turn_id):
        pass
    
    def chunk_to_message(self, chunk):
        pass
```

### 5.2 输出控制策略选择

```python
# 场景 A: 自定义协议需要上层流控 (如 Xiaozhi 60ms音频帧)
class XiaozhiTransport(TransportBase):
    def _create_output_controller(self, session_id: str):
        return AudioPacedFlowController(frame_duration_ms=60)


# 场景 B: WebRTC/LiveKit 底层自己控制 
class LiveKitTransport(TransportBase):
    def _create_output_controller(self, session_id: str):
        return LiveKitPlayoutTracker(audio_source=self._audio_source)


# 场景 C: 简单协议无需输出控制
class SimpleWSTransport(TransportBase):
    def _create_output_controller(self, session_id: str):
        return None  # 默认
```

### 5.3 示例: LiveKit Transport 实现

```python
class LiveKitTransport(TransportBase):
    """LiveKit Transport - 底层控制节奏"""
    
    async def start(self) -> None:
        await self._room.connect(self._url, self._token)
        self._audio_source = rtc.AudioSource(sample_rate=24000, channels=1)
        # ...
    
    async def _do_read(self, session_id: str) -> Optional[bytes]:
        """从 rtc.AudioStream 读取"""
        frame = await self._audio_stream.__anext__()
        return frame.data
    
    async def _do_write(self, session_id: str, data: bytes) -> None:
        """向 rtc.AudioSource 写入"""
        frame = rtc.AudioFrame(data, sample_rate=24000, ...)
        await self._audio_source.capture_frame(frame)
    
    def _create_protocol(self) -> ProtocolBase:
        # LiveKit 内部处理编解码，使用透传协议
        return PassthroughProtocol()
    
    def _create_audio_codec(self, session_id: str) -> Any:
        # WebRTC 内部处理，无需额外编解码
        return None
    
    def _create_output_controller(self, session_id: str):
        # 底层控制策略
        return LiveKitPlayoutTracker(audio_source=self._audio_source)


class LiveKitPlayoutTracker(PlayoutTrackerBase):
    """LiveKit 播放追踪器"""
    
    def __init__(self, audio_source):
        self._audio_source = audio_source
        self._playout_event = asyncio.Event()
    
    async def wait_for_playout(self) -> PlayoutResult:
        await self._playout_event.wait()
        self._playout_event.clear()
        return self._last_result
    
    def on_playout_finished(self, playback_position: float, interrupted: bool):
        self._last_result = PlayoutResult(playback_position, interrupted)
        self._playout_event.set()
```

### 5.4 其他可选扩展

```python
class MyProtocolTransport(TransportBase):
    # ... 必须实现的方法 ...
    
    # 可选: 会话生命周期钩子
    async def _on_session_start(self, session_id: str):
        # 初始化资源
        pass
    
    async def _on_session_end(self, session_id: str):
        # 清理资源
        pass
    
    # 可选: 自定义 TTS_STOP 检测 (提高效率)
    def _is_tts_stop_message(self, data) -> bool:
        pass
```

---

## 6. 总结

### 6.1 职责分配

| 组件 | 职责 | 实现方 |
|------|------|--------|
| **Pipeline** | Chunk 业务处理 | 框架 |
| **InputStation** | raw → Chunk 格式转换 | 框架 |
| **OutputStation** | Chunk → raw 格式转换 | 框架 |
| **TransportBase** | 公共能力 (Turn/Latency/Worker) | 框架 |
| **具体 Transport** | 连接管理 + 读写实现 | 协议方 |
| **Protocol** | 消息编解码 + Chunk 转换 | 协议方 |
| **AudioCodec** | 音频编解码 | 协议方 (可选) |
| **OutputController** | 输出控制 (二选一策略) | 协议方 (可选) |

### 6.2 框架提供 vs 协议方实现

**框架提供 (通用能力)**:
- ✅ read_worker / send_worker 管理
- ✅ Turn 管理 (TTS_STOP → increment_turn)
- ✅ Latency 跟踪 (first_audio_sent)
- ✅ InputStation / OutputStation
- ✅ 生命周期管理
- ✅ 输出控制框架 (支持两种策略)
- ✅ 视频通道预留 (Video read_queue / send_queue)

**协议方实现 (协议特定)**:
- ✅ 物理连接管理 (WebSocket/TCP/WebRTC)
- ✅ 消息编解码格式
- ✅ 音频编解码格式 (可选)
- ✅ 输出控制策略选择 (二选一，可选)

### 6.3 设计边界

| 能力 | 框架职责 | 协议方职责 | 备注 |
|------|---------|-----------|------|
| **Turn 管理** | ✅ 框架自动处理 | ❌ 不需要关心 | TTS_STOP 后自动 increment |
| **Latency 跟踪** | ✅ 框架自动记录 | ❌ 不需要关心 | first_audio_sent 自动记录 |
| **Worker 管理** | ✅ 强制使用 | ❌ 不能跳过 | read/send worker 必须经过 |
| **输出控制** | ✅ 提供框架 | ⚪ 选择策略 | 二选一：FlowController 或 PlayoutTracker |
| **多参与者** | ❌ 不支持 | ⚪ 协议自己处理 | 属于 RTC 本身能力 |
| **视频通道** | ⚪ 预留接口 | ⚪ 未来扩展 | 当前仅音频 |

### 6.4 设计优势

1. **职责单一**: 每个组件只做一件事
2. **解耦彻底**: Pipeline 与 Transport 通过队列解耦
3. **扩展简单**: 新协议只需实现最小接口
4. **框架兜底**: 公共能力由框架统一实现，保证一致性
5. **策略灵活**: 输出控制支持两种互斥策略，适配不同底层能力

### 6.5 协议适配能力验证

| 协议 | 可支撑 | 说明 |
|------|-------|------|
| **Xiaozhi** | ✅ | FlowController + Opus 编解码 |
| **LiveKit** | ✅ | PlayoutTracker + 透传协议 |
| **WebRTC** | ✅ | PlayoutTracker + 透传协议 |
| **Twilio** | ✅ | FlowController + μ-law 编解码 |
| **Pipecat WebSocket** | ✅ | FlowController + 自定义 Serializer |

