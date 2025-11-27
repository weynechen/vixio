# Vixio 并发安全性审查报告

## 审查日期
2025-11-26

## 审查范围
全系统共享资源和并发安全性分析

---

## ✅ 已修复的问题

### 1. **VAD 模型推理竞态条件** (已修复)
- **问题**: `SharedModelSileroVADProvider` 共享 PyTorch 模型，推理时无锁保护
- **影响**: 两个会话同时 VAD 检测时，其中一个会失败
- **修复**: 添加 `_inference_lock` 保护模型推理
- **位置**: `vixio/providers/silero_vad/shared_provider.py:139-143`

### 2. **Opus Codec 编码竞态条件** (已修复)
- **问题**: 全局单例 `OpusCodec`，encoder/decoder 不是线程安全
- **影响**: 两个会话同时播放语音时卡顿、数据错乱
- **修复**: 每个会话创建独立的 `OpusCodec` 实例
- **位置**: `vixio/transports/xiaozhi/transport.py:98-100, 599-615`

### 3. **ASR 模型推理竞态条件** (已修复)
- **问题**: `SharedModelSherpaOnnxProvider` 共享 ONNX recognizer，decode_stream() 方法线程安全性未知
- **影响**: 两个会话同时 ASR 识别时，可能导致识别错误或崩溃
- **修复**: 添加 `_inference_lock` 保护 decode_stream 调用
- **位置**: `vixio/providers/sherpa_onnx_local/shared_provider.py:146-155`

---

## ⚠️ 潜在问题

### 1. **LatencyMonitor 并发写入 - 低风险** 🟢

**位置**: `vixio/providers/sherpa_onnx_local/shared_provider.py`

**当前实现**:
```python
# Line 149: 每次 transcribe 创建独立的 stream
stream = self._shared_recognizer.create_stream()
stream.accept_waveform(self.sample_rate, audio_float)
self._shared_recognizer.decode_stream(stream)
```

**分析**:
- ✅ 每个 transcribe 调用创建独立的 `stream` (状态隔离)
- ❓ `recognizer.decode_stream()` 方法是否线程安全需要验证
- ❓ Sherpa-ONNX 内部是否使用 GIL 或内部锁

**风险等级**: 🟡 中等
- 如果 Sherpa-ONNX 内部有 GIL 保护，无问题
- 如果没有，可能在极端并发时出现问题

**建议**:
1. 查阅 Sherpa-ONNX 文档确认线程安全性
2. 或者添加推理锁（类似 VAD 的修复）:
```python
_inference_lock = threading.Lock()

async def transcribe(self, audio_chunks: List[bytes]) -> str:
    stream = self._shared_recognizer.create_stream()
    stream.accept_waveform(self.sample_rate, audio_float)
    
    # 加锁保护 decode
    with self._inference_lock:
        self._shared_recognizer.decode_stream(stream)
    
    result = stream.result.text.strip()
```

**测试方法**:
- 多设备同时说话并 ASR 识别
- 观察是否有识别错误或崩溃

---

### 2. **LatencyMonitor 并发写入 - 低风险** 🟢

**位置**: `vixio/utils/latency_monitor.py`

**当前实现**:
```python
# Line 43: 全局单例，字典存储数据
self.sessions: Dict[str, Dict[int, Dict[str, float]]] = {}

# Line 61-86: 无锁的字典写入
def record(self, session_id: str, turn_id: int, event: str, timestamp: Optional[float] = None):
    if session_id not in self.sessions:
        self.sessions[session_id] = {}
    if turn_id not in self.sessions[session_id]:
        self.sessions[session_id][turn_id] = {}
    self.sessions[session_id][turn_id][event] = timestamp or time.time()
```

**风险分析**:
- ❌ 字典操作不是原子的（特别是嵌套字典）
- ⚠️ 多个会话同时 `record()` 可能导致数据不一致
- 🟢 但只是监控数据，不影响核心功能

**影响**:
- 最坏情况：latency 数据丢失或不准确
- 不会导致系统崩溃或功能异常

**建议修复** (优先级低):
```python
import threading

class LatencyMonitor:
    def __init__(self, log_dir: str = "logs"):
        self.sessions: Dict[str, Dict[int, Dict[str, float]]] = {}
        self._lock = threading.Lock()  # 添加锁
    
    def record(self, session_id: str, turn_id: int, event: str, timestamp: Optional[float] = None):
        with self._lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = {}
            if turn_id not in self.sessions[session_id]:
                self.sessions[session_id][turn_id] = {}
            self.sessions[session_id][turn_id][event] = timestamp or time.time()
```

---

### 3. **Logger 全局单例 - 安全** ✅

**位置**: `vixio/utils/logger_config.py`

**分析**:
- Loguru 的 `logger` 是线程安全的（内部有锁保护）
- 多个会话并发写入日志没有问题

**结论**: 无风险 ✅

---

## ✅ 安全的设计

### 1. **会话级资源隔离** ✅

以下资源都是会话级别的，完全隔离：

| 资源类型 | 存储位置 | 隔离方式 |
|---------|---------|---------|
| WebSocket 连接 | `Transport._connections[session_id]` | 会话级字典 |
| Pipeline 实例 | `SessionManager._pipelines[session_id]` | 每连接独立创建 |
| ControlBus | `SessionManager._control_buses[session_id]` | 每连接独立创建 |
| Opus Codec | `Transport._opus_codecs[session_id]` | 每连接独立创建 |
| 发送队列 | `Transport._send_queues[session_id]` | 每连接独立队列 |
| 音频流控 | `Transport._audio_flow_control[session_id]` | 会话级字典 |

**结论**: 会话间完全隔离，无竞争 ✅

### 2. **Provider 实例隔离** ✅

在 `agent_chat.py` 中，每个连接通过工厂函数创建独立的 provider 实例：

```python
async def create_pipeline():
    # ✅ 每个会话独立创建
    vad_provider = SharedModelSileroVADProvider(**vad_config)
    asr_provider = SharedModelSherpaOnnxProvider(**asr_config)
    agent_provider = OpenAIAgentProvider(**agent_config)
    tts_provider = EdgeTTSProvider(**tts_config)
    
    return Pipeline([...])
```

虽然模型是共享的，但状态是隔离的：
- VAD: 独立的 `_pcm_buffer`, `_voice_window`
- ASR: 每次 transcribe 创建独立的 `stream`
- Agent: 每个实例独立的对话历史
- TTS: 无状态（每次调用独立）

**结论**: Provider 状态隔离，安全 ✅

### 3. **ControlBus 使用 asyncio 原语** ✅

```python
class ControlBus:
    def __init__(self):
        self._current_turn_id = 0
        self._interrupt_queue = asyncio.Queue()  # 线程安全队列
        self._interrupt_event = asyncio.Event()  # 线程安全事件
        self._lock = asyncio.Lock()              # 异步锁
```

**分析**:
- `asyncio.Queue` 是协程安全的
- `asyncio.Lock` 保护 turn_id 更新
- 每个会话有独立的 ControlBus 实例

**结论**: 安全 ✅

---

## 🎯 总结与建议

### 当前状态
| 组件 | 状态 | 风险等级 |
|------|------|----------|
| VAD 模型推理 | ✅ 已修复 | 🟢 安全 |
| Opus Codec | ✅ 已修复 | 🟢 安全 |
| ASR 模型推理 | ✅ 已修复 | 🟢 安全 |
| LatencyMonitor | ⚠️ 无锁写入 | 🟢 低风险 |
| 会话资源隔离 | ✅ 设计良好 | 🟢 安全 |
| Provider 实例 | ✅ 独立创建 | 🟢 安全 |
| Logger | ✅ 线程安全 | 🟢 安全 |

### 优先级修复建议

#### 🔴 高优先级
无待修复项 - 核心问题已全部解决 ✅

#### 🟢 低优先级
1. **LatencyMonitor 加锁**
   - 添加 `threading.Lock` 保护字典写入
   - 防止极端情况下数据不一致

### 测试建议

**并发压力测试**:
```python
# 同时连接 10 个设备
# 同时播放语音
# 同时进行 VAD/ASR/TTS
# 观察日志是否有异常
```

**关键指标**:
- ✅ 无音频卡顿
- ✅ 无 VAD 检测丢失
- ✅ ASR 识别准确
- ✅ 无 Python 异常

---

## 架构优势

当前架构在并发安全方面的优势：

1. **清晰的会话隔离** - 每个连接独立的 Pipeline/ControlBus/Providers
2. **合理的资源共享** - 只共享重量级模型（VAD/ASR），状态完全隔离
3. **细粒度锁保护** - 只在必要的地方加锁（VAD/ASR 推理）
4. **无全局可变状态** - 除 Logger/LatencyMonitor 外无全局共享状态

**总体评价**: 并发安全性良好，设计合理 🎉

