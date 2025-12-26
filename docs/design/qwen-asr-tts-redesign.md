# Qwen ASR/TTS 重新设计方案

## 1. 设计目标

基于流式模型的实时语音能力，重新设计 vixio 的音频处理架构：

1. **音频数据分层**：引入 `AUDIO_DELTA` 和 `AUDIO_COMPLETE` 类型，与 `TEXT_DELTA`/`TEXT` 保持概念一致
2. **流式处理能力**：支持连续音频流直接送入 ASR（利用 ASR 内置 VAD）
3. **渐进式迁移**：支持批处理和流式两种模式，向后兼容
4. **降低延迟**：流式模式下减少中间缓冲环节，提高响应速度

## 2. Chunk 类型设计

### 2.1 核心概念

建立音频和文本数据的对称分层：

```
流式片段 (Delta) → 完整数据 (Complete)

AUDIO_DELTA  ──aggregation──→  AUDIO_COMPLETE
TEXT_DELTA   ──aggregation──→  TEXT
```

### 2.2 Chunk 类型定义

#### AUDIO_DELTA
- **语义**：流式音频片段，来自 Transport 的小块音频数据
- **特征**：
  - 持续时间：0.06-0.12 秒
  - 大小：约 1920 bytes (16kHz, 16-bit PCM)
  - 连续性：时间上连续的音频流
- **来源**：TransportInputStation
- **用途**：
  - 提供给 VAD 进行实时语音检测
  - 提供给 StreamingASR 进行连续识别
  - 经过 TurnDetector 合并后转为 AUDIO_COMPLETE

#### AUDIO_COMPLETE
- **语义**：完整音频片段，包含完整的语义单元
- **特征**：
  - 持续时间：可变（完整的用户发言）
  - 大小：可变（合并后的音频）
  - 完整性：包含完整的语音边界
- **来源**：
  - VADStation：检测到语音边界时输出
  - TurnDetectorStation：检测到说话轮次结束时输出
- **用途**：
  - 提供给 ASRStation 进行批处理识别
  - 作为完整的音频单元进行处理

### 2.3 类型对比

| 类型 | 语义 | 大小 | 连续性 | 处理模式 |
|------|------|------|--------|----------|
| AUDIO_DELTA | 流式片段 | 小 (~1920 bytes) | 连续流 | 实时处理 |
| AUDIO_COMPLETE | 完整单元 | 大 (可变) | 独立单元 | 批量处理 |
| TEXT_DELTA | 流式片段 | 小 (~10 chars) | 连续流 | 实时处理 |
| TEXT | 完整单元 | 大 (可变) | 独立单元 | 批量处理 |

## 3. Station 设计

### 3.1 Station 分层策略

根据处理模式，Station 分为两类：

1. **批处理 Station**：处理 COMPLETE 类型数据
2. **流式 Station**：处理 DELTA 类型数据

### 3.2 TransportInputStation

#### 输出变更
- **原设计**：输出 AUDIO_RAW（含混合语义）
- **新设计**：输出 AUDIO_DELTA（明确的流式片段）

#### 逻辑
- 接收 Transport 的小块音频帧
- 直接输出为 AUDIO_DELTA，不做缓冲和合并
- 保持音频流的连续性

### 3.3 VADStation

#### 输入/输出
- **输入**：AUDIO_DELTA
- **输出**：
  - AUDIO_DELTA（透传，附加 VAD 状态）
  - AUDIO_COMPLETE（当检测到语音边界时）
  - EVENT_USER_STARTED_SPEAKING
  - EVENT_USER_STOPPED_SPEAKING

#### 逻辑
1. 接收连续的 AUDIO_DELTA
2. 内部缓冲音频，进行 VAD 检测
3. 透传 AUDIO_DELTA（附加 VAD 状态 metadata）
4. 当检测到完整语音片段时：
   - 输出 AUDIO_COMPLETE（合并的音频）
   - 发出 USER_STOPPED_SPEAKING 事件

#### 两种输出模式
- **透传模式**：适合流式 ASR（下游为 StreamingASRStation）
- **合并模式**：适合批处理 ASR（下游为 ASRStation）

### 3.4 TurnDetectorStation

#### 输入/输出
- **输入**：AUDIO_DELTA（带 VAD 状态）
- **输出**：
  - AUDIO_COMPLETE（说话轮次结束时）
  - COMPLETION 事件

#### 逻辑
1. 接收连续的 AUDIO_DELTA（来自 VAD）
2. 根据 VAD 状态缓冲音频
3. 检测说话轮次结束（静音超过阈值）
4. 输出合并后的 AUDIO_COMPLETE
5. 发出 COMPLETION 事件

#### 区别于 VAD
- VAD：检测语音边界（可能有多个片段）
- TurnDetector：检测说话轮次（合并多个片段为一个完整发言）

### 3.5 ASRStation (批处理模式)

#### 输入/输出
- **输入**：AUDIO_COMPLETE
- **输出**：
  - TEXT_DELTA（流式识别结果）
  - EVENT_STREAM_COMPLETE

#### 逻辑
1. 接收完整音频（来自 TurnDetector）
2. 调用 ASR Provider 的 `transcribe_stream()`
3. 流式输出 TEXT_DELTA
4. 识别完成后发出 COMPLETION

#### Provider 接口
```python
async def transcribe_stream(
    self, 
    audio_chunks: List[bytes],  # 完整音频数据
    context: Optional[str] = None  # 上下文（可选）
) -> AsyncIterator[str]:
    """Batch mode: Process complete audio, stream results"""
```

#### 特点
- 输入是完整音频（AUDIO_COMPLETE）
- 输出是流式文本（TEXT_DELTA）
- ASR 内部的 VAD 被禁用（`enable_vad: false`）

### 3.6 StreamingASRStation (流式模式)

#### 输入/输出
- **输入**：AUDIO_DELTA
- **输出**：
  - TEXT_DELTA（流式识别结果）
  - EVENT_STREAM_COMPLETE

#### 逻辑
1. 接收连续的 AUDIO_DELTA（来自 Transport）
2. 持续追加到 ASR Provider 的 WebSocket
3. 监听 ASR 的回调结果
4. 实时输出 TEXT_DELTA
5. ASR 内置 VAD 检测到说话结束时，自动触发识别完成
6. 发出 COMPLETION 事件

#### Provider 接口
```python
async def transcribe_continuous(
    self,
    audio_stream: AsyncIterator[bytes]  # 连续音频流
) -> AsyncIterator[str]:
    """Streaming mode: Process continuous audio with built-in VAD"""
```

#### 特点
- 输入是连续音频流（AUDIO_DELTA）
- ASR 内置 VAD 启用（`enable_vad: true`）
- 不需要外部 VAD/TurnDetector
- 适合低延迟场景

#### 实现要点
- 需要维护长连接的 WebSocket
- 需要处理 ASR 的流式回调
- 需要处理连接异常和重连

### 3.7 TTSStation (批处理模式)

#### 输入/输出
- **输入**：TEXT
- **输出**：
  - AUDIO_COMPLETE（合成的音频）
  - EVENT_TTS_START/STOP

#### 逻辑
1. 接收完整文本（来自 SentenceAggregator）
2. 调用 TTS Provider 的 `synthesize()`
3. 流式输出 AUDIO_COMPLETE
4. 合成完成后发出 TTS_STOP

#### Provider 接口
```python
async def synthesize(
    self, 
    text: str  # 完整文本
) -> AsyncIterator[bytes]:
    """Batch mode: Synthesize complete sentence"""
```

#### 特点
- 输入是完整句子（TEXT）
- 需要 SentenceAggregator 预处理
- TTS 模式：`mode: "commit"`（客户端控制）

### 3.8 StreamingTTSStation (流式模式)

#### 输入/输出
- **输入**：TEXT_DELTA
- **输出**：
  - AUDIO_COMPLETE（合成的音频）
  - EVENT_TTS_START/STOP

#### 逻辑
1. 接收流式文本（来自 Agent，TEXT_DELTA）
2. 持续追加到 TTS Provider 的 WebSocket
3. TTS 智能分段和合成（server_commit 模式）
4. 流式输出 AUDIO_COMPLETE
5. 文本流结束时调用 `finish()`

#### Provider 接口
```python
async def synthesize_stream(
    self, 
    text_stream: AsyncIterator[str]  # 流式文本
) -> AsyncIterator[bytes]:
    """Streaming mode: Synthesize with intelligent segmentation"""
```

#### 特点
- 输入是流式文本（TEXT_DELTA）
- TTS 模式：`mode: "server_commit"`（服务端智能分段）
- 不需要 SentenceAggregator
- 降低首字延迟

## 4. Provider 接口设计

### 4.1 ASRProvider 接口扩展

#### 新增属性
```python
@property
@abstractmethod
def supports_vad(self) -> bool:
    """Whether provider has built-in VAD capability"""

@property
@abstractmethod
def supports_streaming_input(self) -> bool:
    """Whether provider supports continuous audio stream"""
```

#### 方法
```python
# 批处理模式（现有）
async def transcribe_stream(
    self, 
    audio_chunks: List[bytes],
    context: Optional[str] = None
) -> AsyncIterator[str]:
    """Process complete audio chunks, stream results"""

# 流式模式（新增）
async def transcribe_continuous(
    self,
    audio_stream: AsyncIterator[bytes]
) -> AsyncIterator[str]:
    """Process continuous audio stream with built-in VAD"""
```

### 4.2 TTSProvider 接口扩展

#### 新增属性
```python
@property
@abstractmethod
def supports_streaming_input(self) -> bool:
    """Whether provider supports streaming text input"""
```

#### 方法
```python
# 批处理模式（现有）
async def synthesize(
    self, 
    text: str
) -> AsyncIterator[bytes]:
    """Synthesize complete text"""

# 流式模式（扩展）
async def synthesize_stream(
    self, 
    text_stream: AsyncIterator[str]
) -> AsyncIterator[bytes]:
    """Synthesize streaming text with intelligent segmentation"""
```

## 5. Pipeline 配置

### 5.1 Full Pipeline (7 stations) - 批处理模式

```
Transport → VAD → TurnDetector → ASR → TextAggregator → Agent → SentenceAggregator → TTS → Transport
   ↓          ↓         ↓           ↓          ↓            ↓            ↓               ↓
AUDIO_    AUDIO_   AUDIO_      TEXT_      TEXT       TEXT_       TEXT          AUDIO_
DELTA     DELTA    COMPLETE    DELTA                 DELTA                     COMPLETE
```

**特点**：
- 使用外部 VAD + TurnDetector
- ASR 批处理模式（接收 AUDIO_COMPLETE）
- 需要 SentenceAggregator 预处理
- TTS 批处理模式（接收 TEXT）

**适用场景**：
- 稳定性优先
- 对延迟要求不高
- 需要精确控制说话轮次

### 5.2 Simplified Pipeline (6 stations) - 混合模式

```
Transport → VAD → TurnDetector → ASR → TextAggregator → Agent → StreamingTTS → Transport
   ↓          ↓         ↓           ↓          ↓            ↓            ↓
AUDIO_    AUDIO_   AUDIO_      TEXT_      TEXT       TEXT_       AUDIO_
DELTA     DELTA    COMPLETE    DELTA                 DELTA       COMPLETE
```

**特点**：
- 使用外部 VAD + TurnDetector
- ASR 批处理模式（接收 AUDIO_COMPLETE）
- 移除 SentenceAggregator
- TTS 流式模式（接收 TEXT_DELTA，server_commit）

**优势**：
- TTS 延迟降低（无需等待完整句子）
- TTS 智能分段（避免错误切分）

**适用场景**：
- 需要降低 TTS 延迟
- Agent 输出较长文本
- 保持 ASR 稳定性

### 5.3 Streaming Pipeline (4 stations) - 全流式模式

```
Transport → StreamingASR → TextAggregator → Agent → StreamingTTS → Transport
   ↓              ↓               ↓            ↓            ↓
AUDIO_         TEXT_           TEXT       TEXT_       AUDIO_
DELTA          DELTA                      DELTA       COMPLETE
```

**特点**：
- 移除外部 VAD + TurnDetector
- ASR 流式模式（接收 AUDIO_DELTA，内置 VAD）
- 移除 SentenceAggregator
- TTS 流式模式（接收 TEXT_DELTA，server_commit）

**优势**：
- Pipeline 最简洁
- 端到端延迟最低
- 充分利用 Qwen 内置能力

**挑战**：
- 需要维护长连接 WebSocket
- ASR 内置 VAD 可能不如专业 VAD 准确
- 错误恢复更复杂

**适用场景**：
- 极致低延迟需求
- 信任 Qwen 内置 VAD 能力
- 网络连接稳定

### 5.4 配置对比

| 特性 | Full (7 stations) | Simplified (6 stations) | Streaming (4 stations) |
|------|-------------------|------------------------|------------------------|
| VAD | 外部 (Silero) | 外部 (Silero) | 内置 (Qwen) |
| ASR 输入 | AUDIO_COMPLETE | AUDIO_COMPLETE | AUDIO_DELTA |
| ASR 模式 | Batch | Batch | Streaming |
| SentenceAgg | ✓ | ✗ | ✗ |
| TTS 输入 | TEXT | TEXT_DELTA | TEXT_DELTA |
| TTS 模式 | Commit | Server Commit | Server Commit |
| 延迟 | 高 | 中 | 低 |
| 稳定性 | 高 | 高 | 中 |
| 复杂度 | 低 | 中 | 高 |

## 6. 实施策略

### 6.1 Phase 1: Chunk 类型重构（✅ 完成）

1. ✅ 引入 `AUDIO_DELTA` 和 `AUDIO_COMPLETE`
2. ✅ 保留 `AUDIO_RAW` 作为向后兼容别名
3. ✅ 更新 TransportInputStation 输出 AUDIO_DELTA
4. ✅ 更新 VADStation 输出 AUDIO_DELTA (透传) + AUDIO_COMPLETE (合并)
5. ✅ 更新 TurnDetectorStation 透传 AUDIO_DELTA，输出 AUDIO_COMPLETE
6. ✅ 更新 ASRStation、TTSStation、RealtimeStation 支持新类型
7. ✅ 所有 Station 支持向后兼容（同时接受 AUDIO_RAW）

### 6.2 Phase 2: 实现 Simplified Pipeline（✅ 完成）

1. ✅ 实现 `StreamingTTSStation`
   - 接受 TEXT_DELTA 输入（来自 Agent）
   - 调用 TTS provider 的流式方法
   - 输出 AUDIO_COMPLETE
2. ✅ 更新 `Qwen3TtsFlashRealtimeProvider`
   - 添加 `append_text_stream()` 方法（追加文本片段）
   - 添加 `finish_stream()` 方法（完成流并刷新）
   - 支持 server_commit 模式
3. ✅ 更新 stations/__init__.py 导出 StreamingTTSStation

### 6.3 Phase 3: 实现 Streaming Pipeline（✅ 完成）

1. ✅ 实现 `StreamingASRStation`
   - 接受 AUDIO_DELTA 输入（连续音频流）
   - 调用 ASR provider 的流式方法
   - 输出 TEXT_DELTA
   - 使用 ASR 内置 VAD 检测语音边界
2. ✅ 更新 `Qwen3AsrFlashRealtimeProvider`
   - 添加 `supports_streaming_input` 属性
   - 添加 `append_audio_continuous()` 方法（追加音频片段）
   - 添加 `is_speech_ended()` 方法（检测语音结束）
   - 添加 `stop_streaming()` 方法（停止流式会话）
   - 更新 callback 设置 _speech_ended 标志
3. ✅ 创建 `examples/agent_chat_streaming.py`（4-station 示例）
4. ✅ 添加 `config/providers.yaml` 中 `dev-qwen-streaming` 配置

### 6.4 Phase 4: 优化和完善（待完成）

1. ⏳ 性能基准测试（对比 full/simplified/streaming）
2. ⏳ 错误处理和容错优化
3. ⏳ 监控和日志完善
4. ⏳ 文档更新

## 7. 实施总结

### 已完成的工作

**Phase 1-3 已全部完成**，实现了三种 Pipeline 配置：

1. **Full Pipeline (7 stations)** - 现有方案
   - `Transport → VAD → TurnDetector → ASR → TextAgg → Agent → SentenceAgg → TTS`
   - 最稳定，延迟较高

2. **Simplified Pipeline (6 stations)** - 移除 SentenceAggregator
   - `Transport → VAD → TurnDetector → ASR → TextAgg → Agent → StreamingTTS`
   - 使用 `StreamingTTSStation` 接受 TEXT_DELTA
   - TTS server_commit 模式智能分段
   - 延迟中等

3. **Streaming Pipeline (4 stations)** - 全流式
   - `Transport → StreamingASR → TextAgg → Agent → StreamingTTS`
   - 使用 `StreamingASRStation` 接受 AUDIO_DELTA
   - 使用 `StreamingTTSStation` 接受 TEXT_DELTA
   - ASR 内置 VAD，TTS 智能分段
   - 延迟最低（~500-1000ms TTFT）

### 关键实现

1. **新 Station 类型**
   - `StreamingASRStation`: 连续音频流 ASR
   - `StreamingTTSStation`: 流式文本 TTS

2. **Provider 扩展**
   - ASR: `append_audio_continuous()`, `is_speech_ended()`, `stop_streaming()`
   - TTS: `append_text_stream()`, `finish_stream()`

3. **Chunk 类型分层**
   - AUDIO: `AUDIO_DELTA` → `AUDIO_COMPLETE`
   - TEXT: `TEXT_DELTA` → `TEXT`

4. **配置和示例**
   - `config/providers.yaml`: 添加 `dev-qwen-streaming`
   - `examples/agent_chat_streaming.py`: 4-station 示例

## 7. 关键设计决策

### 7.1 为什么使用 AUDIO_DELTA/AUDIO_COMPLETE？

**理由**：
1. **概念一致性**：与 TEXT_DELTA/TEXT 对称，易于理解
2. **类型安全**：通过类型即可判断数据特征（流式 vs 完整）
3. **DAG 路由**：DAG 根据类型自动路由，无需额外配置
4. **向后兼容**：现有代码继续使用 AUDIO_COMPLETE，新代码使用 AUDIO_DELTA

### 7.2 为什么需要 StreamingASRStation 和 ASRStation 两个 Station？

**理由**：
1. **清晰职责**：批处理和流式是不同的处理模式
2. **接口分离**：`transcribe_stream()` vs `transcribe_continuous()`
3. **状态管理**：流式需要维护长连接，批处理不需要
4. **错误处理**：两种模式的错误恢复策略不同
5. **配置简化**：Pipeline 配置一目了然

### 7.3 VADStation 是否还需要输出 AUDIO_COMPLETE？

**需要**，理由：
1. **支持混合 Pipeline**：批处理 ASR 需要 AUDIO_COMPLETE
2. **向后兼容**：现有 Pipeline 依赖 VAD 的合并功能
3. **灵活性**：同一个 VADStation 可以同时输出给批处理和流式下游

**实现方式**：
- VAD 同时输出 AUDIO_DELTA（透传）和 AUDIO_COMPLETE（合并）
- 下游根据 ALLOWED_INPUT_TYPES 自动选择

### 7.4 StreamingTTSStation 输出是 AUDIO_DELTA 还是 AUDIO_COMPLETE？

**建议：AUDIO_COMPLETE**

**理由**：
1. **语义正确性**：TTS 输出的每个音频片段是完整的、可播放的
2. **Transport 需求**：Transport 期望完整的音频帧
3. **避免混淆**：AUDIO_DELTA 应该保留给 Transport 的原始输入

**AUDIO_DELTA vs AUDIO_COMPLETE 的区别**：
- AUDIO_DELTA：Transport 原始输入，时序连续，可能不完整
- AUDIO_COMPLETE：处理后的音频，完整可播放

## 8. 风险和挑战

### 8.1 StreamingASR 的挑战

1. **WebSocket 生命周期管理**
   - 连接断开如何恢复？
   - 如何处理网络抖动？
   - 超时策略？

2. **VAD 准确性**
   - Qwen 内置 VAD 是否足够准确？
   - 误检测如何处理？
   - 如何调整灵敏度？

3. **状态同步**
   - ASR 内部状态如何管理？
   - Session 切换如何处理？

### 8.2 Chunk 类型迁移风险

1. **兼容性**：现有代码依赖 AUDIO_RAW
2. **测试覆盖**：需要全面测试类型变更
3. **文档更新**：需要更新所有相关文档

### 8.3 性能风险

1. **延迟**：流式模式是否真的更快？
2. **资源**：长连接的资源消耗
3. **稳定性**：复杂度增加是否影响稳定性

## 9. 测试计划

### 9.1 单元测试

- Chunk 类型创建和验证
- Station 输入输出类型匹配
- Provider 接口实现

### 9.2 集成测试

- Full Pipeline 端到端测试
- Simplified Pipeline 端到端测试
- Streaming Pipeline 端到端测试

### 9.3 性能测试

- 延迟测试（首字延迟、端到端延迟）
- 吞吐量测试
- 资源消耗测试

### 9.4 稳定性测试

- 长时间运行测试
- 错误恢复测试
- 边界条件测试

## 10. 总结

本设计方案通过引入 `AUDIO_DELTA`/`AUDIO_COMPLETE` 类型分层，实现了音频和文本数据的概念统一，为 vixio 提供了从批处理到全流式的渐进式迁移路径。

**核心优势**：
1. 概念清晰，易于理解
2. 类型安全，自动路由
3. 灵活配置，支持混合模式
4. 向后兼容，渐进式迁移

**实施路径**：
1. Phase 1: Chunk 类型重构（低风险）
2. Phase 2: Simplified Pipeline（中等收益）
3. Phase 3: Streaming Pipeline（高收益，高风险）
4. Phase 4: 优化完善

通过分阶段实施，可以在保证系统稳定性的前提下，逐步提升系统性能和用户体验。
