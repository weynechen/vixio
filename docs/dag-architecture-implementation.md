# DAG 架构实现计划

## 核心设计原则

1. **Station 复用**：Station 接口基本不变，移除透传和来源检查
2. **自动路由**：基于下游节点的 `ALLOWED_INPUT_TYPES` 自动过滤数据
3. **类型泛化**：保持通用类型（TEXT, AUDIO 等），不按来源细分
4. **完全解耦**：Station 不检查 `chunk.source`，只关心类型
5. **内聚同步**：流同步在 Station 内部解决

## Pipeline vs DAG 核心区别

### Pipeline（当前）

```
Audio → VAD → ASR → TextAgg → Agent → TTS → Output
```

- **数据流**：线性，所有 chunk 无条件传递
- **透传**：Station 必须 yield 不关心的 chunk 类型
- **耦合**：Station 检查 `chunk.source` 做分支处理

### DAG（新设计）

```
                    ┌─→ AudioRecorder（旁路）
Audio → VAD ────────┼─→ ASR → TextAgg → Agent → TTS → Output
                    │                      ↑
VoicePrint ─────────┴──────→ JoinNode ─────┘
```

- **数据流**：图结构，chunk 只发送给"接受它"的下游
- **透传**：不需要，DAG 层面自动过滤
- **解耦**：Station 只关心类型，不检查来源

## 解耦设计

### 核心规则

| 规则 | 说明 |
|------|------|
| 类型泛化 | TEXT 就是 TEXT，不按来源细分（如 TEXT_ASR, TEXT_AGENT） |
| 只声明输入 | Station 声明 `ALLOWED_INPUT_TYPES` |
| 不透传 | Station 只输出处理后的数据，不透传不关心的数据 |
| 不检查来源 | Station 不检查 `chunk.source`，DAG 连接保证数据来源 |
| source 自标识 | 每个 Station 输出的 chunk，`source` 设为自己的名字 |

### 解耦的保证

```
DAG 连接 + 不透传 + 自动路由 = Station 只收到它关心的类型
```

Station 不需要知道"谁在上游"，因为：
1. DAG 配置决定了连接关系
2. 只有声明接受的类型才会传入
3. 不会收到"透传"的无关数据

### 示例：同一个 Agent，两种输入场景

**场景 1：语音输入**
```python
dag.add_edge("transport_in", "vad", "asr", "text_agg", "agent", ...)
```
- Agent 收到来自 TextAgg 的 TEXT

**场景 2：文字直接输入**
```python
dag.add_edge("transport_in", "agent", ...)
```
- Agent 收到来自 transport_in 的 TEXT

**Agent 代码完全相同**：
```python
class AgentStation(Station):
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    
    async def process_chunk(self, chunk: Chunk):
        # 只处理 TEXT，不检查来源
        # chunk.source 可能是 "TextAgg" 或 "transport_in"，但 Agent 不关心
        response = await self.llm.chat(chunk.data)
        
        # 输出时设置 source 为自己
        yield TextDeltaChunk(data=response, source=self.name)
```

## 路由机制

### Chunk 分类

| 类别 | 类型 | 示例 | 路由规则 |
|------|------|------|----------|
| **CONTROL** | 控制信号 | `CONTROL_INTERRUPT`, `CONTROL_ABORT` | 走 ControlBus，广播给所有节点 |
| **EVENT** | 事件信号 | `EVENT_VAD_START`, `EVENT_TTS_STOP` | 走 DAG，广播到所有直接下游 |
| **DATA** | 数据 | `AUDIO_RAW`, `TEXT`, `TEXT_DELTA` | 走 DAG，按 `ALLOWED_INPUT_TYPES` 过滤 |

### 自动路由

每个 Station 声明 `ALLOWED_INPUT_TYPES`，DAG 层面自动路由：

```python
class DAGNode:
    def accepts(self, chunk: Chunk) -> bool:
        """检查是否接受该 chunk"""
        # Signal（EVENT）总是接受，广播到所有下游
        if chunk.is_signal():
            return True
        
        # Data 按 ALLOWED_INPUT_TYPES 过滤
        allowed = self.station.ALLOWED_INPUT_TYPES
        if not allowed:  # 空列表表示接受所有
            return True
        return chunk.type in allowed
    
    async def run(self):
        async for chunk in self.station.process(self._input_iterator()):
            # 设置 source 为当前节点名（如果 Station 没设置）
            if not chunk.source:
                chunk.source = self.name
            
            # 发送给接受该 chunk 的下游
            for downstream in self.downstream_nodes:
                if downstream.accepts(chunk):
                    await downstream.input_queue.put(chunk)
```

### Station 的 source 设置

每个 Station 输出 chunk 时，应设置 `source` 为自己：

```python
class ASRStation(Station):
    async def process_chunk(self, chunk: Chunk):
        text = await self.recognize(chunk.data)
        yield TextDeltaChunk(data=text, source=self.name)  # source = "ASR"

class AgentStation(Station):
    async def process_chunk(self, chunk: Chunk):
        response = await self.llm.chat(chunk.data)
        yield TextDeltaChunk(data=response, source=self.name)  # source = "Agent"
```

## Transport 层解耦

### OutputStation 改造

OutputStation 不做业务判断，只做格式转换，将 `source` 传递给 Protocol：

**Before（耦合）**：
```python
async def _chunk_to_message(self, chunk: Chunk):
    if chunk.type == ChunkType.TEXT:
        if "asr" in chunk.source.lower():      # ← 检查来源
            return self.protocol.send_stt(...)
        elif "agent" in chunk.source.lower():  # ← 检查来源
            return self.protocol.send_llm(...)
```

**After（解耦）**：
```python
async def _chunk_to_message(self, chunk: Chunk):
    if chunk.type == ChunkType.TEXT:
        # 不检查来源，直接传给 Protocol
        return self.protocol.send_text(self._session_id, chunk.data, chunk.source)
    
    elif chunk.type == ChunkType.AUDIO_RAW:
        return self.protocol.send_audio(self._session_id, chunk.data, chunk.source)
    
    # ... 其他类型
```

### Protocol 处理 source

Protocol 层面根据 `source` 决定具体的消息格式：

```python
class XiaozhiProtocol(ProtocolBase):
    def send_text(self, session_id: str, text: str, source: str) -> Dict:
        """
        Protocol 根据 source 决定消息类型
        - source 包含 "asr" → STT 消息
        - source 包含 "agent" → LLM 消息
        - 其他 → 通用文本消息
        """
        if "asr" in source.lower():
            return {"type": "stt", "text": text, ...}
        elif "agent" in source.lower():
            return {"type": "llm", "text": text, ...}
        else:
            return {"type": "text", "text": text, ...}
```

### 解耦层次

```
┌─────────────────────────────────────────────────────────────┐
│  Station 层：只关心类型，不检查来源                           │
│  - ALLOWED_INPUT_TYPES 决定接受什么                          │
│  - 输出时设置 source = self.name                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Transport 层（OutputStation）：纯转发，不做业务判断          │
│  - 将 chunk.type + chunk.source 传给 Protocol               │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Protocol 层：根据 type + source 生成具体协议消息            │
│  - 这是协议细节，属于 Protocol 的职责                        │
└─────────────────────────────────────────────────────────────┘
```

## 分叉场景与解决方案

### 场景分类

| 场景类型 | 描述 | 解决方案 | 示例 |
|----------|------|----------|------|
| **独立旁路** | 分叉后独立处理，无需同步 | DAG 天然支持 | 音频录制、日志记录 |
| **结果合并** | 多分支结果按 key 合并 | JoinNode | 声纹+ASR、情感+文本 |
| **流同步** | 多分支输出时间对齐 | 内聚设计 | TTS(text+audio)、数字人 |

### 1. 独立旁路

```
Audio → VAD → ASR → ...
          ↓
    AudioRecorder（独立保存，不需要同步）
```

DAG 直接支持，无需特殊处理。

### 2. 结果合并（JoinNode）

场景：声纹识别结果需要和 ASR 结果关联

```
Audio ─┬─→ ASR ──────────────┐
       │                     ↓
       └─→ VoicePrint → JoinNode → Agent
                        (合并: speaker_id + text)
```

```python
class JoinNode(Station):
    """
    等待多个上游的结果，按 turn_id 合并后输出
    """
    ALLOWED_INPUT_TYPES = []  # 接受所有类型
    
    def __init__(self, upstream_count: int, timeout: float = 5.0):
        super().__init__(name="JoinNode")
        self.upstream_count = upstream_count
        self.timeout = timeout
        self.pending: Dict[int, Dict[str, Chunk]] = {}  # {turn_id: {source: chunk}}
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        turn_id = chunk.turn_id
        source = chunk.source
        
        if turn_id not in self.pending:
            self.pending[turn_id] = {}
        self.pending[turn_id][source] = chunk
        
        # 检查是否收集够了
        if len(self.pending[turn_id]) >= self.upstream_count:
            merged = self._merge_chunks(self.pending.pop(turn_id))
            yield merged
    
    def _merge_chunks(self, chunks: Dict[str, Chunk]) -> Chunk:
        """合并多个 chunk，将其他信息放入 metadata"""
        base = None
        metadata = {}
        
        for source, chunk in chunks.items():
            if chunk.type == ChunkType.TEXT:
                base = chunk
            metadata[source] = {
                "type": chunk.type.value,
                "data": chunk.data
            }
        
        if base:
            base.metadata.update({"merged_sources": metadata})
            base.source = self.name
            return base
        
        # 如果没有 TEXT，创建一个
        first_chunk = list(chunks.values())[0]
        return Chunk(
            type=ChunkType.TEXT,
            data="",
            metadata={"merged_sources": metadata},
            turn_id=first_chunk.turn_id,
            source=self.name
        )
```

### 3. 流同步（内聚设计）

**核心原则**：流同步在 Station 内部解决，输出已同步的复合数据。

#### TTS Station（输出同步的 text + audio）

```python
class TTSStation(StreamStation):
    """
    TTS 站点 - 输入 TEXT，输出同步的 (TEXT事件 + AUDIO流)
    """
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT]
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        text = chunk.data
        sentence_id = self.sentence_counter
        self.sentence_counter += 1
        
        # 1. 句子开始事件（带完整文本）
        yield EventChunk(
            type=ChunkType.EVENT_TTS_SENTENCE_START,
            data=text,
            metadata={"sentence_id": sentence_id},
            source=self.name
        )
        
        # 2. 音频流
        async for audio_data in self.provider.synthesize(text):
            yield AudioChunk(
                data=audio_data,
                metadata={"sentence_id": sentence_id},
                source=self.name
            )
        
        # 3. 句子结束事件
        yield EventChunk(
            type=ChunkType.EVENT_TTS_SENTENCE_END,
            metadata={"sentence_id": sentence_id},
            source=self.name
        )
```

#### Avatar Station（输出同步的 video + audio + text）

```python
class AvatarStation(StreamStation):
    """
    数字人站点 - 输入 (AUDIO, TEXT事件)，输出同步的帧
    """
    ALLOWED_INPUT_TYPES = [ChunkType.AUDIO_RAW, ChunkType.EVENT_TTS_SENTENCE_START]
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        if chunk.type == ChunkType.EVENT_TTS_SENTENCE_START:
            self.current_text = chunk.data
            yield chunk  # 透传文本事件
            return
        
        if chunk.type == ChunkType.AUDIO_RAW:
            # 生成口型视频帧
            video_frames = await self.avatar_provider.generate_lipsync(chunk.data)
            
            # 输出同步的复合帧
            yield AvatarFrameChunk(
                audio=chunk.data,
                video=video_frames,
                text=self.current_text,
                metadata=chunk.metadata,
                source=self.name
            )
```

## API 设计

### DAG Builder

```python
class DAG:
    def __init__(self, name: str = "DAG"):
        self.name = name
        self._nodes: Dict[str, DAGNode] = {}
        self._edges: List[Tuple[str, str]] = []
    
    def add_node(self, name: str, station: Station) -> "DAG":
        """添加节点"""
        self._nodes[name] = DAGNode(name, station)
        return self
    
    def add_edge(self, *nodes: str) -> "DAG":
        """
        添加边，支持链式语法
        
        用法：
        - add_edge("a", "b")           # 单条边 a → b
        - add_edge("a", "b", "c", "d") # 链式 a → b → c → d
        """
        if len(nodes) < 2:
            raise ValueError("add_edge requires at least 2 nodes")
        
        for i in range(len(nodes) - 1):
            self._edges.append((nodes[i], nodes[i + 1]))
        return self
    
    def compile(self) -> "CompiledDAG":
        """编译 DAG，验证并返回可执行实例"""
        self._validate()
        return CompiledDAG(self)
```

### 虚拟节点

`transport_in` 和 `transport_out` 是虚拟节点：

```python
dag.add_edge("transport_in", "vad", "asr", "text_agg", "agent", "tts", "transport_out")
```

## 使用示例

### 基础语音对话

```python
dag = DAG("voice_chat")

# 添加节点
dag.add_node("vad", VADStation(vad_provider))
dag.add_node("turn_detector", TurnDetectorStation())
dag.add_node("asr", ASRStation(asr_provider))
dag.add_node("text_agg", TextAggregatorStation())
dag.add_node("agent", AgentStation(agent_provider))
dag.add_node("sentence_agg", SentenceAggregatorStation())
dag.add_node("tts", TTSStation(tts_provider))

# 主路径（链式语法）
dag.add_edge("transport_in", "vad", "turn_detector", "asr", "text_agg", 
             "agent", "sentence_agg", "tts", "transport_out")

# 编译运行
compiled = dag.compile()
```

### 带声纹识别

```python
dag = DAG("voice_chat_with_speaker_id")

dag.add_node("vad", VADStation(vad_provider))
dag.add_node("asr", ASRStation(asr_provider))
dag.add_node("voiceprint", VoicePrintStation(vp_provider))
dag.add_node("join", JoinNode(upstream_count=2))
dag.add_node("text_agg", TextAggregatorStation())
dag.add_node("agent", AgentStation(agent_provider))
dag.add_node("tts", TTSStation(tts_provider))

# 分叉：VAD 同时给 ASR 和 VoicePrint
dag.add_edge("transport_in", "vad")
dag.add_edge("vad", "asr")
dag.add_edge("vad", "voiceprint")

# 合并
dag.add_edge("asr", "join")
dag.add_edge("voiceprint", "join")

# 后续流程
dag.add_edge("join", "text_agg", "agent", "tts", "transport_out")
```

### 文字直接输入（跳过语音处理）

```python
dag = DAG("text_chat")

dag.add_node("agent", AgentStation(agent_provider))
dag.add_node("sentence_agg", SentenceAggregatorStation())
dag.add_node("tts", TTSStation(tts_provider))

# 文字直接进入 Agent
dag.add_edge("transport_in", "agent", "sentence_agg", "tts", "transport_out")
```

**Agent 代码不需要任何修改**，同样的 `ALLOWED_INPUT_TYPES = [TEXT]`。

## Station 改动

### 移除透传和来源检查

**Before（Pipeline 模式）**：
```python
async def process_chunk(self, chunk: Chunk):
    if chunk.type == ChunkType.TEXT_DELTA:
        if chunk.source == "agent":  # ← 检查来源
            sentences = self._aggregator.add_chunk(chunk.data)
            for s in sentences:
                yield TextChunk(data=s, source=self.name)
        yield chunk  # ← 透传
    else:
        yield chunk  # ← 透传
```

**After（DAG 模式）**：
```python
async def process_chunk(self, chunk: Chunk):
    # 只处理 TEXT_DELTA，不检查来源，不透传
    if chunk.type == ChunkType.TEXT_DELTA:
        sentences = self._aggregator.add_chunk(chunk.data)
        for s in sentences:
            yield TextChunk(data=s, source=self.name)
```

## Signal 处理

### Signal 分类

```
┌─────────────────────────────────────────────────────────────┐
│  CONTROL 信号：走 ControlBus（广播，立即生效）               │
│  - CONTROL_INTERRUPT → 所有节点立即重置状态                  │
│  - CONTROL_ABORT → 输出层立即中止                            │
└─────────────────────────────────────────────────────────────┘
                            
┌─────────────────────────────────────────────────────────────┐
│  EVENT 信号：走 DAG 数据流（顺序传递）                       │
│  - 总是广播到所有直接下游（不按 ALLOWED_INPUT_TYPES 过滤）   │
│  - Station 内部决定是否响应特定 EVENT                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DATA：走 DAG 数据流                                         │
│  - 按 ALLOWED_INPUT_TYPES 过滤                               │
└─────────────────────────────────────────────────────────────┘
```

### CONTROL 信号（走 ControlBus）

CONTROL 信号需要**立即**影响所有节点，不能等待数据流传递。

**为什么不能走 DAG？**
```
如果 INTERRUPT 走 DAG 数据流：
INTERRUPT → VAD → ASR → Agent → TTS
                         ↑
                    Agent 正在生成，很慢
                    TTS 可能要等很久才收到
```

**ControlBus 的作用**：
1. 管理 `turn_id`（递增表示新轮次）
2. 广播 CONTROL 信号给所有订阅者
3. 提供中断检测机制

```python
class ControlBus:
    async def send_interrupt(self, source: str, reason: str):
        """广播 INTERRUPT 给所有订阅的 Station"""
        # 1. 递增 turn_id
        await self.increment_turn(source, reason)
        
        # 2. 设置中断事件
        self._interrupt_event.set()
```

**Station 响应 CONTROL**：

Station 通过 ControlBus 订阅 CONTROL 信号，通过 `turn_id` 过滤旧数据：

```python
class Station:
    async def process(self, input_stream):
        async for chunk in input_stream:
            # 通过 turn_id 过滤旧轮次的数据
            if chunk.turn_id < self.current_turn_id:
                continue  # 丢弃旧数据
            
            # 检测新轮次，重置状态
            if chunk.turn_id > self.current_turn_id:
                self.current_turn_id = chunk.turn_id
                await self.reset_state()
            
            # 处理 chunk
            async for output in self.process_chunk(chunk):
                yield output
```

### EVENT 信号（走 DAG）

EVENT 信号走 DAG 数据流，但**总是广播**到所有直接下游（不按类型过滤）。

**EVENT 的两种用途**：

1. **触发下游处理**：
   - `EVENT_TURN_END` → 触发 ASR 输出最终结果
   - `EVENT_AGENT_STOP` → 触发 SentenceAgg flush buffer

2. **通知客户端**：
   - `EVENT_STATE_LISTENING` → 通知客户端状态变化
   - `EVENT_TTS_SENTENCE_START` → 通知客户端显示字幕

**Station 响应 EVENT**：

Station 内部决定是否响应特定 EVENT：

```python
class SentenceAggregatorStation(Station):
    ALLOWED_INPUT_TYPES = [ChunkType.TEXT_DELTA]  # 只声明数据类型，不声明 EVENT
    
    async def process_chunk(self, chunk: Chunk):
        # EVENT 会自动进来（因为 Signal 总是被接受）
        if chunk.type == ChunkType.EVENT_AGENT_STOP:
            # 响应 Agent 停止，flush buffer
            remaining = self._aggregator.flush()
            if remaining:
                yield TextChunk(data=remaining, source=self.name)
            yield chunk  # 透传 EVENT 给下游
            return
        
        # 处理 TEXT_DELTA 数据
        if chunk.type == ChunkType.TEXT_DELTA:
            sentences = self._aggregator.add_chunk(chunk.data)
            for s in sentences:
                yield TextChunk(data=s, source=self.name)
```

### 中断处理流程

```
用户打断说话
    ↓
Transport 检测到
    ↓
ControlBus.send_interrupt(source="Transport", reason="user_interrupted")
    ↓
ControlBus:
  1. increment_turn() → turn_id = 2
  2. set interrupt_event
    ↓
所有 Station（通过 turn_id 机制）:
  - 收到 turn_id=1 的数据 → 丢弃（< current_turn_id）
  - 收到 turn_id=2 的数据 → 重置状态，开始处理
    ↓
Session:
  - 注入 CONTROL_ABORT 到 OutputStation
  - OutputStation 发送 TTS_STOP 给客户端
```

### ControlBus 与 DAG 的集成

```python
class CompiledDAG:
    def __init__(self, dag: DAG, control_bus: ControlBus):
        self.control_bus = control_bus
        
        # 将 control_bus 传递给所有 Station
        for node in self.nodes.values():
            node.station.control_bus = control_bus
    
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        # ... DAG 运行逻辑 ...
        pass
```

### 总结

| 信号类型 | 传递方式 | 路由规则 | 响应方式 |
|----------|----------|----------|----------|
| CONTROL | ControlBus | 广播给所有订阅者 | turn_id 过滤 + 状态重置 |
| EVENT | DAG 数据流 | 广播到所有直接下游 | Station 内部判断响应 |
| DATA | DAG 数据流 | 按 ALLOWED_INPUT_TYPES 过滤 | 正常处理 |

## 核心类设计

### DAGNode

```python
class DAGNode:
    def __init__(self, name: str, station: Station):
        self.name = name
        self.station = station
        self.input_queue: asyncio.Queue = None
        self.downstream: List['DAGNode'] = []
    
    @property
    def allowed_input_types(self) -> Optional[Set[ChunkType]]:
        types = getattr(self.station, 'ALLOWED_INPUT_TYPES', None)
        if types:
            return set(types)
        return None  # None 表示接受所有
    
    def accepts(self, chunk: Chunk) -> bool:
        """
        判断是否接受 chunk
        - Signal (EVENT): 总是接受，广播到所有下游
        - Data: 按 ALLOWED_INPUT_TYPES 过滤
        
        注意：CONTROL 信号走 ControlBus，不走 DAG
        """
        # EVENT 信号总是接受
        if chunk.is_signal():
            return True
        
        # Data 按类型过滤
        allowed = self.allowed_input_types
        if allowed is None:
            return True
        return chunk.type in allowed
    
    async def run(self, output_queue: asyncio.Queue):
        try:
            async for chunk in self.station.process(self._input_iterator()):
                # 确保 source 已设置
                if not chunk.source:
                    chunk.source = self.name
                
                # 发送给接受该 chunk 的下游
                sent = False
                for downstream in self.downstream:
                    if downstream.accepts(chunk):
                        await downstream.input_queue.put(chunk)
                        sent = True
                
                # 如果没有下游接受，送到输出队列
                if not sent or not self.downstream:
                    await output_queue.put(chunk)
        finally:
            for downstream in self.downstream:
                await downstream.input_queue.put(None)
    
    async def _input_iterator(self) -> AsyncIterator[Chunk]:
        while True:
            chunk = await self.input_queue.get()
            if chunk is None:
                break
            yield chunk
```

### CompiledDAG

```python
class CompiledDAG:
    def __init__(self, dag: DAG, control_bus: Optional[ControlBus] = None):
        self.nodes = dag._nodes
        self.edges = dag._edges
        self.control_bus = control_bus
        self._build_graph()
        
        # 将 control_bus 传递给所有 Station
        if control_bus:
            for node in self.nodes.values():
                node.station.control_bus = control_bus
    
    def _build_graph(self):
        for from_name, to_name in self.edges:
            if from_name != "transport_in" and to_name != "transport_out":
                from_node = self.nodes.get(from_name)
                to_node = self.nodes.get(to_name)
                if from_node and to_node:
                    from_node.downstream.append(to_node)
    
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        output_queue = asyncio.Queue()
        
        # 创建输入队列
        for node in self.nodes.values():
            node.input_queue = asyncio.Queue()
        
        # 启动所有节点
        tasks = [asyncio.create_task(node.run(output_queue)) for node in self.nodes.values()]
        
        # 找入口节点
        entry_nodes = self._find_entry_nodes()
        
        # 喂入输入（过滤 CONTROL 信号，它们走 ControlBus）
        async def feed_input():
            async for chunk in input_stream:
                # CONTROL 信号走 ControlBus，不走 DAG
                if chunk.type.value.startswith("control."):
                    if self.control_bus:
                        await self.control_bus.send_interrupt(
                            source=chunk.source or "transport",
                            reason=chunk.type.value
                        )
                    continue
                
                # EVENT 和 DATA 走 DAG
                for entry in entry_nodes:
                    if entry.accepts(chunk):
                        await entry.input_queue.put(chunk)
            
            for entry in entry_nodes:
                await entry.input_queue.put(None)
        
        input_task = asyncio.create_task(feed_input())
        
        # 收集输出
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                    if chunk is None:
                        break
                    yield chunk
                except asyncio.TimeoutError:
                    if all(t.done() for t in tasks) and output_queue.empty():
                        break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
```

## Middleware 兼容性

### 当前 Middleware 机制

| Middleware | 功能 | DAG 兼容性 |
|------------|------|------------|
| SignalHandlerMiddleware | 处理 CONTROL_INTERRUPT，取消 streaming | ⚠️ 需要调整 |
| InterruptDetectorMiddleware | 检测 turn_id 变化 | ✅ 兼容 |
| InputValidatorMiddleware | 验证输入类型 | ✅ 兼容 |
| LatencyMonitorMiddleware | 监控延迟 | ✅ 兼容 |
| ErrorHandlerMiddleware | 错误处理 | ✅ 兼容 |

### SignalHandlerMiddleware 调整

**当前**：从数据流接收 `CONTROL_INTERRUPT`

```python
async def process_signal(self, chunk: Chunk, next_handler):
    if chunk.type == ChunkType.CONTROL_INTERRUPT:
        # 从数据流接收
        await self._handle_interrupt()
```

**DAG 模式**：CONTROL 信号走 ControlBus，不走数据流

```python
class SignalHandlerMiddleware(SignalMiddleware):
    def attach(self, station: 'Station'):
        super().attach(station)
        # 订阅 ControlBus 的中断事件
        if station.control_bus:
            station.control_bus.register_interrupt_handler(self._handle_interrupt)
    
    async def _handle_interrupt(self, signal: InterruptSignal):
        """从 ControlBus 接收中断"""
        if self.cancel_streaming and self._streaming_task:
            self._streaming_task.cancel()
        if self.on_interrupt:
            await self.on_interrupt()
```

### 透传语义变化

**Pipeline 模式**：Station 必须 yield signal 透传

```python
async def process_chunk(self, chunk):
    if chunk.is_signal():
        # 处理
        yield chunk  # 必须 yield，否则下游收不到
```

**DAG 模式**：Station 决定是否 yield，DAG 层面保证广播

```python
async def process_chunk(self, chunk):
    if chunk.type == ChunkType.EVENT_AGENT_STOP:
        # 处理 EVENT
        remaining = self.flush()
        if remaining:
            yield TextChunk(data=remaining, source=self.name)
        # 可以 yield chunk 继续传播
        # 也可以不 yield（如果这个 EVENT 只需要本节点处理）
        yield chunk  # 让 EVENT 继续往下游传播
```

### Middleware 内部透传不变

Middleware 内部调用 `next_handler(chunk)` 的逻辑不需要改变：

```python
class DataMiddleware(Middleware):
    async def process(self, chunk, next_handler):
        if chunk.is_signal():
            # Signal 透传给 next_handler（Station 的 process_chunk）
            async for result in next_handler(chunk):
                yield result
            return
        
        # 处理 Data
        async for result in self.process_data(chunk, next_handler):
            yield result
```

这里的"透传"是 Middleware 链内部的，与 DAG 层面的广播不冲突。

### InterruptDetectorMiddleware 不需要调整

通过 `control_bus.get_current_turn_id()` 检测中断的机制在 DAG 中完全兼容：

```python
def _is_interrupted(self) -> bool:
    if not self.station or not self.station.control_bus:
        return False
    
    current_turn = self.station.control_bus.get_current_turn_id()
    return current_turn > self.station.current_turn_id
```

### ControlBus 扩展

为支持 Middleware 订阅中断事件，ControlBus 需要扩展：

```python
class ControlBus:
    def __init__(self):
        # ... existing code ...
        self._interrupt_handlers: List[Callable] = []
    
    def register_interrupt_handler(self, handler: Callable):
        """注册中断处理器"""
        self._interrupt_handlers.append(handler)
    
    async def send_interrupt(self, source: str, reason: str):
        """发送中断信号"""
        signal = InterruptSignal(source=source, reason=reason, turn_id=self._current_turn_id)
        
        # 递增 turn_id
        await self.increment_turn(source, reason)
        
        # 通知所有处理器
        for handler in self._interrupt_handlers:
            try:
                await handler(signal)
            except Exception as e:
                self.logger.error(f"Error in interrupt handler: {e}")
        
        # 设置事件
        self._interrupt_event.set()
```

## 可视化监控

为支持类似 [react-dag-editor](https://github.com/microsoft/react-dag-editor) 的可视化界面，需要扩展以下功能。

### 可视化需求

| 功能 | 需要的数据 |
|------|------------|
| 可视化 DAG 结构 | 节点列表、边列表、节点类型、位置 |
| 实时数据流 | 每个节点的输入/输出 chunk、时间戳 |
| 延迟监控 | 每个节点的处理延迟、端到端延迟 |
| 文本预览 | TEXT/TEXT_DELTA 的内容预览 |
| 节点状态 | idle/processing、队列大小、错误状态 |

### Station 可视化元数据

```python
class Station:
    # 可视化元数据（可选）
    DISPLAY_NAME: str = ""      # 显示名称（如 "语音识别"）
    ICON: str = ""              # 图标名称（如 "microphone"）
    COLOR: str = ""             # 节点颜色（如 "#4CAF50"）
    DESCRIPTION: str = ""       # 描述
```

### 节点运行时统计

```python
@dataclass
class NodeStats:
    """节点运行时统计"""
    chunks_received: int = 0
    chunks_processed: int = 0
    chunks_forwarded: int = 0
    queue_size: int = 0
    avg_latency_ms: float = 0.0
    last_chunk_time: float = 0.0
    status: str = "idle"        # idle, processing, error
    error_count: int = 0
    last_error: Optional[str] = None
    
    # 文本预览
    last_input_text: str = ""
    last_output_text: str = ""

class DAGNode:
    def __init__(self, name: str, station: Station):
        self.name = name
        self.station = station
        self.stats = NodeStats()
        self.position: Dict[str, float] = {"x": 0, "y": 0}  # 用于布局
```

### DAG 结构序列化

```python
class DAG:
    def to_dict(self) -> Dict:
        """导出 DAG 结构为 JSON，供前端渲染"""
        return {
            "name": self.name,
            "nodes": [
                {
                    "id": name,
                    "type": node.station.__class__.__name__,
                    "display_name": getattr(node.station, 'DISPLAY_NAME', node.name),
                    "allowed_input_types": [t.value for t in (node.allowed_input_types or [])],
                    "position": node.position,
                    "icon": getattr(node.station, 'ICON', None),
                    "color": getattr(node.station, 'COLOR', None),
                }
                for name, node in self._nodes.items()
            ],
            "edges": [
                {"from": from_node, "to": to_node}
                for from_node, to_node in self._edges
            ]
        }
```

### 实时事件系统

```python
class DAGEventType(str, Enum):
    CHUNK_RECEIVED = "chunk_received"
    CHUNK_PROCESSED = "chunk_processed"
    CHUNK_FORWARDED = "chunk_forwarded"
    NODE_STATUS_CHANGED = "node_status_changed"
    NODE_ERROR = "node_error"
    EDGE_ACTIVE = "edge_active"  # 数据流经某条边

@dataclass
class DAGEvent:
    type: DAGEventType
    node_name: str
    timestamp: float
    data: Dict[str, Any]

class DAGEventEmitter:
    """DAG 事件发射器，用于实时监控"""
    
    def __init__(self):
        self._listeners: List[Callable[[DAGEvent], None]] = []
    
    def subscribe(self, listener: Callable[[DAGEvent], None]):
        """订阅事件"""
        self._listeners.append(listener)
    
    async def emit(self, event: DAGEvent):
        """发射事件"""
        for listener in self._listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                logger.error(f"Event listener error: {e}")
    
    async def emit_chunk_event(
        self, 
        node_name: str, 
        chunk: Chunk, 
        event_type: DAGEventType,
        target_node: Optional[str] = None
    ):
        """发送 chunk 相关事件"""
        preview = None
        if chunk.type in [ChunkType.TEXT, ChunkType.TEXT_DELTA]:
            preview = str(chunk.data)[:100] if chunk.data else ""
        
        event = DAGEvent(
            type=event_type,
            node_name=node_name,
            timestamp=time.time(),
            data={
                "chunk_type": chunk.type.value,
                "chunk_source": chunk.source,
                "turn_id": chunk.turn_id,
                "preview": preview,
                "target_node": target_node,
            }
        )
        await self.emit(event)
```

### DAGNode 支持监控

```python
class DAGNode:
    async def run(self, output_queue: asyncio.Queue, event_emitter: Optional[DAGEventEmitter] = None):
        self.stats.status = "processing"
        
        try:
            async for chunk in self.station.process(self._input_iterator()):
                # 更新统计
                self.stats.chunks_processed += 1
                self.stats.last_chunk_time = time.time()
                
                # 记录文本预览
                if chunk.type in [ChunkType.TEXT, ChunkType.TEXT_DELTA]:
                    self.stats.last_output_text = str(chunk.data)[:100]
                
                # 发射事件
                if event_emitter:
                    await event_emitter.emit_chunk_event(
                        self.name, chunk, DAGEventType.CHUNK_PROCESSED
                    )
                
                # 发送给下游
                for downstream in self.downstream:
                    if downstream.accepts(chunk):
                        await downstream.input_queue.put(chunk)
                        downstream.stats.chunks_received += 1
                        downstream.stats.queue_size = downstream.input_queue.qsize()
                        
                        # 发射边激活事件
                        if event_emitter:
                            await event_emitter.emit_chunk_event(
                                self.name, chunk, DAGEventType.EDGE_ACTIVE,
                                target_node=downstream.name
                            )
        
        except Exception as e:
            self.stats.status = "error"
            self.stats.error_count += 1
            self.stats.last_error = str(e)
            raise
        finally:
            self.stats.status = "idle"
```

### 监控 API

```python
class DAGMonitor:
    """DAG 监控接口"""
    
    def __init__(self, dag: CompiledDAG):
        self.dag = dag
        self.event_emitter = DAGEventEmitter()
    
    def get_structure(self) -> Dict:
        """获取 DAG 结构（用于初始渲染）"""
        return self.dag.to_dict()
    
    def get_stats(self) -> Dict:
        """获取所有节点的运行时统计"""
        return {
            "nodes": {
                name: {
                    "status": node.stats.status,
                    "chunks_received": node.stats.chunks_received,
                    "chunks_processed": node.stats.chunks_processed,
                    "queue_size": node.stats.queue_size,
                    "avg_latency_ms": node.stats.avg_latency_ms,
                    "last_input_text": node.stats.last_input_text,
                    "last_output_text": node.stats.last_output_text,
                    "error_count": node.stats.error_count,
                    "last_error": node.stats.last_error,
                }
                for name, node in self.dag.nodes.items()
            },
            "timestamp": time.time()
        }
    
    def subscribe_events(self, listener: Callable[[DAGEvent], None]):
        """订阅实时事件"""
        self.event_emitter.subscribe(listener)
```

### WebSocket 端点

```python
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/ws/dag/{session_id}")
async def dag_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    monitor = get_dag_monitor(session_id)
    
    # 发送初始结构
    await websocket.send_json({
        "type": "structure",
        "data": monitor.get_structure()
    })
    
    # 订阅事件并转发
    async def forward_event(event: DAGEvent):
        await websocket.send_json({
            "type": "event",
            "data": {
                "event_type": event.type.value,
                "node": event.node_name,
                "timestamp": event.timestamp,
                **event.data
            }
        })
    
    monitor.subscribe_events(forward_event)
    
    # 定期发送统计快照
    try:
        while True:
            await asyncio.sleep(1.0)
            await websocket.send_json({
                "type": "stats",
                "data": monitor.get_stats()
            })
    except Exception:
        pass
```

### 前端数据格式

#### DAG 结构 JSON

```json
{
  "name": "voice_chat",
  "nodes": [
    {
      "id": "vad",
      "type": "VADStation",
      "display_name": "语音检测",
      "allowed_input_types": ["audio.raw"],
      "position": {"x": 100, "y": 200},
      "icon": "microphone",
      "color": "#4CAF50"
    },
    {
      "id": "asr",
      "type": "ASRStation",
      "display_name": "语音识别",
      "allowed_input_types": ["audio.raw"],
      "position": {"x": 300, "y": 200},
      "icon": "text",
      "color": "#2196F3"
    }
  ],
  "edges": [
    {"from": "transport_in", "to": "vad"},
    {"from": "vad", "to": "asr"}
  ]
}
```

#### 实时事件 JSON

```json
{
  "type": "event",
  "data": {
    "event_type": "chunk_processed",
    "node": "asr",
    "timestamp": 1702345678.123,
    "chunk_type": "text.delta",
    "preview": "你好，我是...",
    "target_node": "text_agg"
  }
}
```

#### 统计快照 JSON

```json
{
  "type": "stats",
  "data": {
    "nodes": {
      "vad": {
        "status": "processing",
        "chunks_received": 150,
        "chunks_processed": 148,
        "queue_size": 2,
        "avg_latency_ms": 5.2
      },
      "asr": {
        "status": "processing",
        "last_output_text": "你好，我是小智"
      }
    },
    "timestamp": 1702345678.123
  }
}
```

## 文件修改清单

### 新增文件

1. `core/dag.py` - DAG 核心实现（DAG, DAGNode, CompiledDAG）
2. `core/dag_monitor.py` - DAG 监控接口
3. `core/dag_events.py` - 事件系统
4. `stations/join_node.py` - JoinNode 实现
5. `api/dag_websocket.py` - WebSocket 端点（可选）

### 修改文件

1. `stations/*.py` - 移除透传逻辑和来源检查，添加可视化元数据
2. `stations/transport_stations.py` - OutputStation 不检查 source，传给 Protocol
3. `transports/*/protocol.py` - Protocol 根据 type + source 生成消息
4. `core/session.py` - 支持 DAG
5. `core/control_bus.py` - 添加 interrupt handler 注册机制
6. `stations/middlewares/signal_handler.py` - 改为从 ControlBus 订阅中断
7. `core/station.py` - 添加可视化元数据字段

### 不需要修改

- `core/chunk.py` - Chunk 定义保持不变（source 字段已存在）
- `core/middleware.py` - Middleware 基类保持不变
- `stations/middlewares/interrupt_detector.py` - 检测机制兼容
- `stations/middlewares/input_validator.py` - 验证机制兼容
- `stations/middlewares/latency_monitor.py` - 监控机制兼容
- `stations/middlewares/error_handler.py` - 错误处理兼容

## 实现优先级

1. **Phase 1**：核心 DAG 实现（DAG, DAGNode, CompiledDAG）
2. **Phase 2**：单元测试
3. **Phase 3**：改造现有 Station（移除透传和来源检查）
4. **Phase 4**：改造 OutputStation 和 Protocol
5. **Phase 5**：ControlBus 扩展 + SignalHandlerMiddleware 调整
6. **Phase 6**：JoinNode 实现
7. **Phase 7**：改造 TTS Station（内聚同步）
8. **Phase 8**：集成到 SessionManager
9. **Phase 9**：可视化监控（DAGMonitor、事件系统、WebSocket）
10. **Phase 10**：前端可视化界面

## 注意事项

1. **环检测**：`compile()` 时验证 DAG 无环
2. **队列大小**：合理设置，避免内存溢出
3. **错误传播**：节点异常时通知下游
4. **资源清理**：DAG 停止时取消所有任务
5. **向后兼容**：Pipeline 可以作为 DAG 的特例继续使用
6. **Middleware 兼容**：大部分 Middleware 无需修改，仅 SignalHandlerMiddleware 需要调整
7. **监控性能**：事件发射应异步，避免阻塞主数据流
8. **WebSocket 连接管理**：处理断线重连、多客户端订阅
