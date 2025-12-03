# DAG 架构实现计划

## 核心设计原则

1. **并发执行**：可并行的节点同时执行（基于 asyncio.Queue）
2. **无状态节点**：节点之间只通过 Chunk 传递数据
3. **完全重写**：新架构，不兼容旧 Pipeline
4. **最小化实现**：只实现核心功能（add_node, add_edge, compile, run）

## 架构对比

### 当前 Pipeline（线性）

```
Input → VAD → TurnDetector → ASR → TextAgg → Agent → SentenceAgg → TTS → Output
```

### 新 DAG（多路径）

```
                                    ┌─→ Agent ─→ SentenceAgg ─→ TTS ─┐
Input → VAD → TurnDetector → ASR → TextAgg ─┤                        ├─→ Transport
                                             └────────────────────────┘
```

## 实现步骤

### 1. 核心类设计

**文件：** `core/dag.py`

需要实现以下类：

#### 1.1 `DAGNode`

- 包装 Station，作为 DAG 的节点
- 属性：
                                                                - `name: str` - 节点名称
                                                                - `station: Station` - 实际的处理单元
                                                                - `input_queues: Dict[str, asyncio.Queue]` - 输入队列（按上游节点分组）
                                                                - `output_edges: List[str]` - 输出边（下游节点名称列表）
- 方法：
                                                                - `async run()` - 执行节点（从所有输入队列读取，处理后分发到下游）

#### 1.2 `DAG`

- DAG 图的表示和执行
- 属性：
                                                                - `nodes: Dict[str, DAGNode]` - 所有节点
                                                                - `edges: List[Tuple[str, str]]` - 边列表（from_node, to_node）
                                                                - `entry_point: str` - 入口节点
                                                                - `exit_nodes: Set[str]` - 出口节点（没有下游的节点）
- 方法：
                                                                - `validate()` - 验证 DAG（检测环、孤立节点）
                                                                - `topological_sort()` - 拓扑排序（用于依赖分析）
                                                                - `async run(input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]` - 执行 DAG

#### 1.3 `DAGBuilder`

- 构建器模式，类似 LangGraph 的 StateGraph
- 方法：
                                                                - `add_node(name: str, station: Station)` - 添加节点
                                                                - `add_edge(from_node: str, to_node: str)` - 添加边
                                                                - `set_entry_point(name: str)` - 设置入口节点
                                                                - `set_exit_points(names: List[str])` - 设置出口节点（可选）
                                                                - `compile() -> DAG` - 编译为可执行的 DAG

### 2. 数据流处理

**关键设计：**

```python
# 每个节点的输入队列按上游节点分组
class DAGNode:
    def __init__(self, name: str, station: Station):
        self.name = name
        self.station = station
        self.input_queues: Dict[str, asyncio.Queue] = {}  # {upstream_name: queue}
        self.output_edges: List[str] = []
    
    async def run(self, output_queues: Dict[str, asyncio.Queue]):
        """
        执行节点逻辑：
  1. 从所有输入队列读取 chunks（合并为单一流）
  2. 通过 station.process() 处理
  3. 分发到所有下游节点的队列
        """
        # Merge all input queues into single stream
        merged_stream = self._merge_input_streams()
        
        # Process through station
        async for output_chunk in self.station.process(merged_stream):
            # Send to all downstream nodes
            for downstream_name in self.output_edges:
                await output_queues[downstream_name].put(output_chunk)
```

### 3. 执行模型

**并发执行：**

```python
class DAG:
    async def run(self, input_stream: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
        """
  1. 为每个节点创建输入队列
  2. 启动所有节点的 asyncio.Task（并发执行）
  3. 将 input_stream 喂给入口节点
  4. 从出口节点收集输出
        """
        # Create queues for all nodes
        node_queues = {name: asyncio.Queue() for name in self.nodes}
        
        # Start all nodes as concurrent tasks
        tasks = []
        for name, node in self.nodes.items():
            task = asyncio.create_task(node.run(node_queues))
            tasks.append(task)
        
        # Feed input to entry point
        asyncio.create_task(self._feed_input(input_stream, node_queues[self.entry_point]))
        
        # Collect output from exit nodes
        async for chunk in self._collect_output(node_queues, self.exit_nodes):
            yield chunk
```

### 4. 文件修改清单

#### 新增文件

1. `core/dag.py` - DAG 核心实现（DAGNode, DAG, DAGBuilder）
2. `examples/dag_example.py` - DAG 使用示例

#### 修改文件

1. `core/session.py` - SessionManager 支持 DAG（可选，或创建新的 DAGSessionManager）
2. `examples/agent_chat.py` - 改用 DAG 构建（示例）

#### 不需要修改

- `core/station.py` - Station 接口保持不变
- `core/chunk.py` - Chunk 定义保持不变
- 所有 `stations/*` - 所有 station 实现保持不变
- `transports/*` - Transport 实现保持不变

### 5. 使用示例

```python
# 创建 DAG Builder
builder = DAGBuilder()

# 添加节点
builder.add_node("vad", VADStation(vad_provider))
builder.add_node("turn_detector", TurnDetectorStation())
builder.add_node("asr", ASRStation(asr_provider))
builder.add_node("text_agg", TextAggregatorStation())
builder.add_node("agent", AgentStation(agent_provider))
builder.add_node("sentence_agg", SentenceAggregatorStation())
builder.add_node("tts", TTSStation(tts_provider))

# 定义边（数据流）
builder.set_entry_point("vad")

# 线性部分
builder.add_edge("vad", "turn_detector")
builder.add_edge("turn_detector", "asr")
builder.add_edge("asr", "text_agg")

# 多路径：TextAgg 的输出给 Agent
builder.add_edge("text_agg", "agent")

# Agent 的输出给 SentenceAgg
builder.add_edge("agent", "sentence_agg")

# SentenceAgg 的输出给 TTS
builder.add_edge("sentence_agg", "tts")

# 设置出口节点（多个）
# Transport 可以从多个节点收集输出
builder.set_exit_points(["agent", "tts"])  # 收集 Agent 的 TEXT_DELTA 和 TTS 的 AUDIO

# 编译 DAG
dag = builder.compile()

# 运行
async for output_chunk in dag.run(input_stream):
    # Transport 处理输出
    await transport.output_chunk(session_id, output_chunk)
```

## 关键优势

1. **解决多消费者问题**：TextAgg 可以同时输出给 Agent 和 Transport（如果需要）
2. **清晰的数据流**：显式定义边关系，一目了然
3. **灵活性**：轻松添加分支、合并节点
4. **保持现有投资**：所有 Station 实现无需修改
5. **性能保持**：并发执行模型与当前 Pipeline 类似

## 实现优先级

1. **Phase 1（核心）**：实现 `DAGNode`, `DAG`, `DAGBuilder` 基础功能
2. **Phase 2（验证）**：创建单元测试，验证 DAG 正确性
3. **Phase 3（集成）**：修改 `SessionManager` 或创建新的 `DAGSessionManager`
4. **Phase 4（示例）**：更新 `examples/agent_chat.py` 使用 DAG

## 注意事项

1. **环检测**：DAG 必须无环，`compile()` 时验证
2. **队列大小**：合理设置队列大小，避免内存溢出
3. **错误处理**：节点错误时的传播和恢复机制
4. **资源清理**：DAG 停止时清理所有任务和队列
5. **Signal 传播**：CONTROL_INTERRUPT 等信号如何在 DAG 中传播（broadcast 到所有节点？）

## 待确认问题

1. **多输入合并**：如果一个节点有多个上游，如何合并输入流？（按顺序？按时间戳？）
2. **Exit nodes 输出**：多个出口节点的输出如何合并给 Transport？（merge streams？）
3. **Passthrough 规则**：DAG 下是否还需要 passthrough？（理论上不需要，通过显式边定义数据流）