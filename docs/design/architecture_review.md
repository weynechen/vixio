# Vixio 系统架构评估报告

> 评估日期：2025-12-26  
> 评估视角：Python后端架构师 / 资深开发者  
> 评估范围：整体架构设计、分层合理性、耦合度、接口清晰度、可扩展性

---

## 一、系统概述

Vixio 是一个语音驱动的 Agent 框架，实现了完整的语音对话管道。核心组件包括：

| 层次 | 职责 | 核心类 |
|------|------|--------|
| Transport层 | WebSocket/HTTP 连接管理 | `TransportBase`, `XiaozhiTransport` |
| DAG层 | 数据流图，管理 Station 间的数据路由 | `DAG`, `CompiledDAG`, `DAGNode` |
| Station层 | 处理节点 | `Station`, `StreamStation`, `BufferStation`, `DetectorStation` |
| Provider层 | 具体服务提供者 | `BaseProvider`, `TTSProvider`, `ASRProvider` 等 |
| Middleware层 | 横切关注点处理 | `Middleware`, `MiddlewareChain` |
| Config层 | 配置管理 | `VixioConfig`, `VixioSettings` |
| Session层 | 会话生命周期管理 | `SessionManager`, `ControlBus` |

---

## 二、架构优点

### 2.1 DAG 数据流模型

灵活的管道配置，支持分支和合并：

```python
dag = DAG("voice_chat")
dag.add_node("vad", VADStation())
dag.add_node("asr", ASRStation())
dag.add_edge("transport_in", "vad", "asr", "agent", "tts", "transport_out")
```

### 2.2 Chunk 类型系统

清晰的数据/信号分类（Data、Control、Event）：

```python
class ChunkType(str, Enum):
    # Data Chunks
    AUDIO_DELTA = "audio.delta"
    TEXT_DELTA = "text.delta"
    
    # Control Signals
    CONTROL_STATE_RESET = "control.state_reset"
    
    # Event Signals
    EVENT_STREAM_COMPLETE = "event.stream.complete"
```

### 2.3 Completion Contract

`EMITS_COMPLETION` 和 `AWAITS_COMPLETION` 的契约设计优雅解决了 Station 间的协调问题：

```python
class ASRStation(StreamStation):
    EMITS_COMPLETION = True      # ASR 完成后发出完成信号
    AWAITS_COMPLETION = False    # ASR 不等待上游完成信号

class TextAggregatorStation(BufferStation):
    EMITS_COMPLETION = True      # 聚合完成后发出信号
    AWAITS_COMPLETION = True     # 需要等待 ASR 完成信号
```

### 2.4 Provider 注册机制

装饰器模式简化 Provider 注册：

```python
@register_provider("silero-vad-grpc")
class SileroVADGRPCProvider(VADProvider):
    pass
```

### 2.5 Turn 管理机制

`turn_id` 机制有效处理中断和状态重置，所有 Station 自动丢弃过期数据。

---

## 三、架构问题与改进建议

### 🔴 问题1：分层不清晰 - Station 与 Provider 职责边界模糊

**现状分析**

```python
# src/vixio/stations/tts.py
class TTSStation(StreamStation):
    def __init__(self, tts_provider: TTSProvider, ...):
        self.tts = tts_provider  # 直接持有 Provider 引用
    
    async def process_chunk(self, chunk: Chunk):
        # 直接调用 Provider 方法
        # 同时处理业务逻辑（事件发送、状态管理）
        async for audio_data in self.tts.synthesize(text):
            yield AudioChunk(...)
```

**问题**
- Station 直接依赖具体 Provider 实现，而非抽象接口
- Station 承担了太多业务逻辑（TTS 事件发送、状态管理、音频处理）
- Provider 的生命周期管理散落在 Station 和 Runner 中

**改进方案 A：引入 Service 层**

```python
# 新增 services/ 目录
class TTSService:
    """TTS 业务逻辑层 - 隔离 Station 和 Provider"""
    
    def __init__(self, provider: TTSProvider):
        self._provider = provider
        self._is_speaking = False
    
    async def synthesize_with_events(self, text: str) -> AsyncIterator[TTSOutput]:
        """封装业务逻辑，返回统一的输出类型"""
        if not self._is_speaking:
            yield TTSEvent(type="start")
            self._is_speaking = True
        
        yield TTSEvent(type="sentence_start", text=text)
        
        async for audio in self._provider.synthesize(text):
            yield TTSAudio(data=audio, sample_rate=self._provider.sample_rate)
    
    async def finish(self) -> AsyncIterator[TTSOutput]:
        """结束 TTS 会话"""
        if self._is_speaking:
            yield TTSEvent(type="stop")
            self._is_speaking = False

# Station 只负责数据转换
class TTSStation(StreamStation):
    def __init__(self, service: TTSService):
        self._service = service  # 依赖 Service，非 Provider
    
    async def process_chunk(self, chunk: Chunk):
        async for output in self._service.synthesize_with_events(chunk.data):
            yield self._to_chunk(output)
```

**改进方案 B：Provider 职责明确化（较小改动）**

```python
# 在 Provider 中定义清晰的业务接口
class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """纯音频合成，无状态"""
        pass
    
    @property
    @abstractmethod
    def sample_rate(self) -> int:
        pass

# Station 只调用 Provider 的明确接口，业务逻辑内聚在 Station
# 但需要在文档中明确两者的职责边界
```

---

### 🔴 问题2：Provider 接口设计过于宽泛

**现状分析**

```python
# src/vixio/providers/base.py
class BaseProvider(ABC):
    @property
    @abstractmethod
    def is_local(self) -> bool: pass
    
    @property
    @abstractmethod
    def is_stateful(self) -> bool: pass
    
    @property
    @abstractmethod
    def category(self) -> str: pass  # 字符串，非枚举
    
    @classmethod
    @abstractmethod
    def get_config_schema(cls) -> Dict[str, Any]: pass  # Dict，无类型验证
```

**问题**
- `category` 属性用字符串，失去类型安全
- `get_config_schema` 返回 Dict，没有类型验证
- 不同类型 Provider（VAD/ASR/TTS/Agent）共用一个基类，缺乏专门化

**改进方案**

```python
from enum import Enum
from typing import Generic, TypeVar, Type
from pydantic import BaseModel

class ProviderCategory(Enum):
    VAD = "vad"
    ASR = "asr" 
    TTS = "tts"
    AGENT = "agent"

TConfig = TypeVar('TConfig', bound=BaseModel)

class BaseProvider(ABC, Generic[TConfig]):
    """泛型 Provider 基类，强类型配置"""
    
    @classmethod
    @abstractmethod
    def get_config_class(cls) -> Type[TConfig]:
        """返回 Pydantic 配置类，而非 Dict"""
        pass
    
    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """自动从 Pydantic 模型生成 schema（向后兼容）"""
        return cls.get_config_class().model_json_schema()
    
    @property
    @abstractmethod
    def category(self) -> ProviderCategory:
        """返回枚举，而非字符串"""
        pass

# 使用示例
class SileroVADConfig(BaseModel):
    threshold: float = 0.35
    threshold_low: float = 0.15
    service_url: str = "localhost:50051"

class SileroVADProvider(BaseProvider[SileroVADConfig]):
    @classmethod
    def get_config_class(cls) -> Type[SileroVADConfig]:
        return SileroVADConfig
    
    @property
    def category(self) -> ProviderCategory:
        return ProviderCategory.VAD
```

---

### 🔴 问题3：配置管理存在全局单例问题

**现状分析**

```python
# src/vixio/config/loader.py
_global_config: Optional[VixioConfig] = None

def get_config() -> VixioConfig:
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config
```

**问题**
- 全局单例在多租户/测试场景下会产生问题
- 配置变更需要重启，无法热加载
- 不同 Session 可能需要不同配置

**改进方案**

```python
# 依赖注入模式
class ConfigProvider:
    """配置提供者 - 可注入，支持多租户"""
    
    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path
        self._config = self._load(config_path)
        self._watchers: list[Callable] = []
    
    def get(self) -> VixioConfig:
        return self._config
    
    def reload(self) -> VixioConfig:
        """支持热加载"""
        self._config = self._load(self._config_path)
        for watcher in self._watchers:
            watcher(self._config)
        return self._config
    
    def on_change(self, callback: Callable[[VixioConfig], None]) -> None:
        """注册配置变更监听器"""
        self._watchers.append(callback)

# 使用：通过依赖注入传递配置
class SessionManager:
    def __init__(self, config_provider: ConfigProvider, ...):
        self._config = config_provider.get()

# 测试时可以轻松替换
def test_session():
    mock_config = ConfigProvider(config_path="test_config.yaml")
    manager = SessionManager(config_provider=mock_config)
```

---

### 🔴 问题4：Middleware 与 Station 强耦合

**现状分析**

```python
# src/vixio/core/middleware.py
def with_middlewares(*middlewares: Middleware):
    def decorator(cls):
        original_process_chunk = cls.process_chunk
        cls._middleware_templates = middlewares
        
        @wraps(original_process_chunk)
        async def wrapped_process_chunk(self, chunk: Chunk):
            # 在运行时创建 middleware
            if not hasattr(self, '_middlewares'):
                default_middlewares = _create_default_middlewares(self)
                # ... 复杂的初始化逻辑
```

**问题**
- 装饰器模式导致 Middleware 在运行时动态创建，难以测试
- `_create_default_middlewares` 函数内部硬编码了类型判断
- Middleware 克隆逻辑 `_clone_middleware` 有潜在的属性丢失风险
- 难以在不同实例间复用 Middleware 配置

**改进方案：组合模式替代装饰器**

```python
class StationBuilder:
    """Station 构建器 - 显式组合"""
    
    def __init__(self, station_class: Type[Station]):
        self._station_class = station_class
        self._middlewares: list[Middleware] = []
    
    def with_middleware(self, middleware: Middleware) -> 'StationBuilder':
        self._middlewares.append(middleware)
        return self
    
    def with_default_middlewares(self) -> 'StationBuilder':
        """显式添加默认中间件"""
        self._middlewares.extend([
            InputValidatorMiddleware(...),
            SignalHandlerMiddleware(),
            ErrorHandlerMiddleware(),
        ])
        return self
    
    def with_timeout(self, seconds: float) -> 'StationBuilder':
        """便捷方法"""
        self._middlewares.append(TimeoutHandlerMiddleware(seconds))
        return self
    
    def build(self, **kwargs) -> Station:
        station = self._station_class(**kwargs)
        station.set_middlewares(self._middlewares.copy())
        return station

# 使用
tts_station = (StationBuilder(TTSStation)
    .with_default_middlewares()
    .with_timeout(30.0)
    .build(tts_provider=provider))

# 测试时可以精确控制 middleware
def test_tts_station():
    station = (StationBuilder(TTSStation)
        .with_middleware(MockMiddleware())  # 使用 mock
        .build(tts_provider=mock_provider))
```

---

### 🔴 问题5：DAG 与 Session 生命周期耦合严重

**现状分析**

```python
# src/vixio/core/session.py
async def _handle_connection(self, connection_id: str) -> None:
    # SessionManager 承担了太多职责
    
    # 1. 创建 ControlBus
    control_bus = ControlBus(...)
    self._control_buses[connection_id] = control_bus
    
    # 2. 创建 DAG（通过 factory）
    dag = await self.dag_factory()
    
    # 3. 获取 InputStation 和 OutputStation
    input_station = self.transport.get_input_station(connection_id)
    output_station = self.transport.get_output_station(connection_id)
    
    # 4. 添加节点并编译
    dag.add_node("transport_out", output_station)
    compiled_dag = dag.compile(control_bus=control_bus)
    
    # 5. 启动中断处理器
    interrupt_task = asyncio.create_task(self._handle_interrupts(...))
    
    # 6. 处理设备工具
    # ... 更多逻辑
```

**问题**
- `SessionManager` 承担了太多职责：连接管理、DAG 创建、生命周期管理、工具注册
- DAG 创建逻辑与 Session 管理混在一起
- `dag_factory` 返回半成品 DAG（没有 transport_out），需要外部补全
- 难以单独测试各个组件

**改进方案：职责分离**

```python
# 1. DAG 构建器 - 单一职责
class DAGBuilder:
    """DAG 构建器 - 创建完整的 DAG"""
    
    def __init__(self, config: DAGConfig):
        self._config = config
    
    def build(
        self,
        input_station: InputStation,
        output_station: OutputStation,
        control_bus: ControlBus,
    ) -> CompiledDAG:
        dag = DAG(self._config.name)
        
        # 添加所有节点
        for node_config in self._config.nodes:
            station = self._create_station(node_config)
            dag.add_node(node_config.name, station)
        
        # 添加 transport 节点
        dag.add_node("transport_out", output_station)
        
        # 添加边
        for edge in self._config.edges:
            dag.add_edge(*edge)
        
        return dag.compile(control_bus)

# 2. Session 生命周期管理器 - 单一职责
class SessionLifecycleManager:
    """Session 生命周期管理"""
    
    def __init__(self, dag_builder: DAGBuilder):
        self._dag_builder = dag_builder
    
    async def create_session(
        self,
        connection_id: str,
        transport: TransportBase,
    ) -> Session:
        control_bus = ControlBus()
        
        input_station = transport.get_input_station(connection_id)
        output_station = transport.get_output_station(connection_id)
        
        dag = self._dag_builder.build(
            input_station=input_station,
            output_station=output_station,
            control_bus=control_bus,
        )
        
        return Session(
            id=connection_id,
            dag=dag,
            control_bus=control_bus,
        )
    
    async def destroy_session(self, session: Session) -> None:
        await session.cleanup()

# 3. SessionManager - 组合各个管理器
class SessionManager:
    """Session 入口 - 组合"""
    
    def __init__(
        self,
        lifecycle_manager: SessionLifecycleManager,
        transport: TransportBase,
    ):
        self._lifecycle = lifecycle_manager
        self._transport = transport
        self._sessions: Dict[str, Session] = {}
    
    async def _handle_connection(self, connection_id: str) -> None:
        session = await self._lifecycle.create_session(
            connection_id, self._transport
        )
        self._sessions[connection_id] = session
        await session.run()
```

---

### 🔴 问题6：Protocol 接口设计负担过重

**现状分析**

```python
# src/vixio/core/protocol.py
class ProtocolBase(ABC):
    # 编解码方法（必须实现）
    @abstractmethod
    def decode_message(self, data: Union[bytes, str]) -> Dict[str, Any]: pass
    
    @abstractmethod
    def encode_message(self, message: Dict[str, Any]) -> Union[bytes, str]: pass
    
    @abstractmethod
    def message_to_chunk(self, message, session_id, turn_id) -> Optional[Chunk]: pass
    
    @abstractmethod
    def chunk_to_message(self, chunk: Chunk) -> Optional[Dict[str, Any]]: pass
    
    @abstractmethod
    def prepare_audio_data(self, pcm_data, sample_rate, channels, session_id) -> list[bytes]: pass
    
    # 业务方法（约 15 个可选方法）
    def send_stt(self, session_id, text, **params): return None
    def send_llm(self, session_id, text, **params): return None
    def send_tts_audio(self, session_id, audio_data, **params): return None
    def send_tts_event(self, session_id, event, text, **params): return None
    def handshake(self, session_id, **params): return None
    # ... 更多方法
```

**问题**
- 一个 Protocol 类承担了编解码、消息转换、业务方法等多重职责
- 新增协议需要实现大量方法
- 业务方法（send_stt, send_llm 等）属于应用层，不应在 Protocol 中
- 难以扩展或替换部分功能

**改进方案：接口分离原则（ISP）**

```python
# 1. 消息编解码 - 单一职责
class MessageCodec(ABC):
    """消息编解码"""
    
    @abstractmethod
    def decode(self, data: Union[bytes, str]) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def encode(self, message: Dict[str, Any]) -> Union[bytes, str]:
        pass

# 2. Chunk 转换器 - 单一职责
class ChunkConverter(ABC):
    """Chunk 与消息的相互转换"""
    
    @abstractmethod
    def to_chunk(
        self, 
        message: Dict[str, Any], 
        session_id: str, 
        turn_id: int
    ) -> Optional[Chunk]:
        pass
    
    @abstractmethod
    def from_chunk(self, chunk: Chunk) -> Optional[Dict[str, Any]]:
        pass

# 3. 音频帧处理器 - 单一职责
class AudioFramer(ABC):
    """音频帧处理"""
    
    @abstractmethod
    def frame(
        self, 
        pcm_data: bytes, 
        sample_rate: int, 
        channels: int
    ) -> list[bytes]:
        pass
    
    @abstractmethod
    def flush(self, session_id: str) -> list[bytes]:
        pass

# 4. 消息工厂 - 业务方法
class MessageFactory(ABC):
    """协议消息构造"""
    
    @abstractmethod
    def create_handshake(self, session_id: str, **params) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def create_stt_message(self, session_id: str, text: str) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def create_tts_audio_message(
        self, 
        session_id: str, 
        audio_data: bytes
    ) -> Dict[str, Any]:
        pass
    
    # ... 其他消息类型

# 5. 协议组合 - 组合而非继承
@dataclass
class Protocol:
    """协议组合"""
    
    codec: MessageCodec
    converter: ChunkConverter
    framer: AudioFramer
    factory: MessageFactory
    
    def decode_message(self, data: Union[bytes, str]) -> Dict[str, Any]:
        return self.codec.decode(data)
    
    def encode_message(self, message: Dict[str, Any]) -> Union[bytes, str]:
        return self.codec.encode(message)
    
    # 保持向后兼容的便捷方法
    def message_to_chunk(self, message, session_id, turn_id) -> Optional[Chunk]:
        return self.converter.to_chunk(message, session_id, turn_id)

# 使用示例
xiaozhi_protocol = Protocol(
    codec=JsonCodec(),
    converter=XiaozhiChunkConverter(),
    framer=OpusFramer(sample_rate=16000, frame_duration_ms=60),
    factory=XiaozhiMessageFactory(),
)
```

---

### 🟡 问题7：类型系统不够严格

**现状分析**

```python
# 多处使用 Any 类型
class Chunk:
    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# Provider 配置使用 Dict
def create(provider_name: str, config: Dict[str, Any]) -> BaseProvider:
    pass

# 方法参数缺乏类型约束
def send_tts_event(self, session_id: str, event: str, text: Optional[str] = None):
    # event 可以是任意字符串
    pass
```

**问题**
- `Any` 类型失去类型检查的保护
- IDE 无法提供有效的自动补全
- 运行时错误难以追踪

**改进方案**

```python
from typing import TypedDict, Union, Literal
from dataclasses import dataclass

# 1. 使用 TypedDict 增强 metadata 类型安全
class AudioMetadata(TypedDict, total=False):
    sample_rate: int
    channels: int
    visual_context: 'ImageContent'

class TextMetadata(TypedDict, total=False):
    source: str
    confidence: float

# 2. 专门化的 Chunk 类
@dataclass
class AudioChunk(Chunk):
    data: bytes  # 明确类型，而非 Any
    metadata: AudioMetadata = field(default_factory=dict)
    sample_rate: int = 16000
    channels: int = 1

@dataclass
class TextChunk(Chunk):
    data: str  # 明确为字符串
    metadata: TextMetadata = field(default_factory=dict)

# 3. 使用 Literal 约束字符串参数
TTSEventType = Literal["start", "stop", "sentence_start", "sentence_end"]

def send_tts_event(
    self, 
    session_id: str, 
    event: TTSEventType,  # 只能是指定的几个值
    text: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    pass

# 4. Provider 配置使用 Pydantic
class SileroVADConfig(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0, default=0.35)
    threshold_low: float = Field(ge=0.0, le=1.0, default=0.15)
    frame_window_threshold: int = Field(ge=1, default=8)
    
    @field_validator('threshold_low')
    def validate_threshold_low(cls, v, info):
        if v >= info.data.get('threshold', 1.0):
            raise ValueError('threshold_low must be less than threshold')
        return v
```

---

### 🟡 问题8：Runner 层过于特化，缺乏抽象

**现状分析**

```python
# src/vixio/xiaozhi/runners/pipeline_runner.py
async def run_pipeline_server(
    config_path: str,
    env: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8000,
    ...
):
    # 约 300 行的单一函数
    
    # 硬编码的 Provider 加载
    providers_dict = ProviderFactory.create_from_config_file(config_path, env)
    
    # 硬编码的 DAG 创建
    async def create_dag():
        dag = DAG("AgentVoiceChat")
        dag.add_node("vad", VADStation(vad_provider))
        dag.add_node("turn_detector", TurnDetectorStation(...))
        dag.add_node("asr", ASRStation(asr_provider))
        # ... 大量硬编码
```

**问题**
- `run_pipeline_server` 是一个 300+ 行的函数，难以测试和维护
- DAG 结构硬编码，每种场景需要复制修改
- 信号处理逻辑在函数内部，无法复用
- 难以扩展新的 Runner 类型

**改进方案：模板方法模式**

```python
from abc import ABC, abstractmethod

class PipelineServer(ABC):
    """Pipeline 服务器模板"""
    
    def __init__(self, config_path: str, env: Optional[str] = None):
        self.config_path = config_path
        self.env = env
        self._providers: Dict[str, BaseProvider] = {}
        self._transport: Optional[TransportBase] = None
        self._session_manager: Optional[SessionManager] = None
    
    @abstractmethod
    def create_providers(self) -> Dict[str, BaseProvider]:
        """子类定义 Provider 创建逻辑"""
        pass
    
    @abstractmethod
    def create_dag(self, providers: Dict[str, BaseProvider]) -> DAG:
        """子类定义 DAG 结构"""
        pass
    
    @abstractmethod
    def create_transport(self, host: str, port: int) -> TransportBase:
        """子类定义 Transport"""
        pass
    
    def get_system_prompt(self) -> str:
        """可选：子类可覆盖"""
        return "You are a helpful AI assistant."
    
    async def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """模板方法 - 定义运行流程"""
        # 1. 创建 Provider
        self._providers = self.create_providers()
        await self._initialize_providers()
        
        # 2. 创建 Transport
        self._transport = self.create_transport(host, port)
        
        # 3. 创建 DAG 工厂
        dag_factory = lambda: self.create_dag(self._providers)
        
        # 4. 创建并启动 SessionManager
        self._session_manager = SessionManager(
            transport=self._transport,
            dag_factory=dag_factory,
        )
        
        await self._session_manager.start()
        await self._wait_for_shutdown()
    
    async def _initialize_providers(self) -> None:
        for provider in self._providers.values():
            if hasattr(provider, 'initialize'):
                await provider.initialize()

# 具体实现
class XiaozhiPipelineServer(PipelineServer):
    """Xiaozhi 协议的 Pipeline 服务器"""
    
    def create_providers(self) -> Dict[str, BaseProvider]:
        return ProviderFactory.create_from_config_file(
            self.config_path, self.env
        )
    
    def create_dag(self, providers: Dict[str, BaseProvider]) -> DAG:
        dag = DAG("xiaozhi-voice")
        
        dag.add_node("vad", VADStation(providers["vad"]))
        dag.add_node("turn_detector", TurnDetectorStation())
        dag.add_node("asr", ASRStation(providers["asr"]))
        dag.add_node("text_agg", TextAggregatorStation())
        dag.add_node("agent", AgentStation(providers["agent"]))
        dag.add_node("sentence_agg", SentenceAggregatorStation())
        dag.add_node("tts", TTSStation(providers["tts"]))
        
        dag.add_edge(
            "transport_in", "vad", "turn_detector", "asr", 
            "text_agg", "agent", "sentence_agg", "tts", "transport_out"
        )
        
        return dag
    
    def create_transport(self, host: str, port: int) -> TransportBase:
        return XiaozhiTransport(host=host, port=port)

# 使用
server = XiaozhiPipelineServer(config_path="config.yaml", env="dev")
await server.run(host="0.0.0.0", port=8000)
```

---

### 🟡 问题9：循环依赖问题

**现状分析**

多处使用 `TYPE_CHECKING` 来避免循环导入：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vixio.core.control_bus import ControlBus
    from vixio.core.station import Station
```

**问题点**
- `station.py` 依赖 `control_bus.py`
- `session.py` 依赖 `dag.py`, `station.py`, `transport.py`
- `transport_stations.py` 依赖 `protocol.py`, `chunk.py`
- `middleware.py` 依赖 `station.py`（反向依赖）

**改进方案：重构模块边界**

```
重构模块边界，使用依赖倒置：

core/
├── interfaces/          # 纯接口定义（无实现）
│   ├── station.py       # StationInterface
│   ├── transport.py     # TransportInterface
│   └── control.py       # ControlBusInterface
├── models/              # 数据模型（无业务逻辑）
│   ├── chunk.py
│   └── config.py
└── impl/                # 具体实现（依赖接口）
    ├── station.py
    └── dag.py
```

