# Stations 参考文档

本文档整理了 vixio 中各个 Station 的输入、输出、信号等关键信息。

## 核心 Stations

### 数据流概览

```
Audio → VAD → TurnDetector → ASR → TextAggregator → Agent → SentenceAggregator → TTS → Audio
```

### Station 配置表

| Station | 基类 | ALLOWED_INPUT_TYPES | EMITS_COMPLETION | AWAITS_COMPLETION | LATENCY_METRIC |
|---------|------|---------------------|------------------|-------------------|----------------|
| **VADStation** | DetectorStation | `AUDIO_RAW` | ❌ | ❌ | - |
| **TurnDetectorStation** | DetectorStation | `AUDIO_RAW`, `VAD_START/END`, `BOT_SPEAKING_*` | ❌ | ❌ | - |
| **ASRStation** | StreamStation | `AUDIO_RAW` | ✅ | ❌ | `asr_complete` |
| **TextAggregatorStation** | BufferStation | `TEXT_DELTA` | ✅ | ✅ | - |
| **AgentStation** | StreamStation | `TEXT` | ✅ | ❌ | `agent_first_token` |
| **SentenceAggregatorStation** | BufferStation | `TEXT_DELTA` | ✅ | ✅ | - |
| **TTSStation** | StreamStation | `TEXT` | ✅ | ✅ | `tts_first_audio_ready` |

### 输入输出详情

| Station | 输入 | 输出 | 说明 |
|---------|------|------|------|
| **VADStation** | 流式 `AUDIO_RAW` | `VAD_START` + merged `AUDIO_RAW` + `VAD_END` | 检测语音活动，buffer 并合并音频 |
| **TurnDetectorStation** | merged `AUDIO_RAW` + `VAD_END` | merged `AUDIO_RAW` | 判断 turn 结束，可聚合多段 VAD 音频 |
| **ASRStation** | merged `AUDIO_RAW` | `BOT_THINKING` + `TEXT_DELTA` + `STREAM_COMPLETE` | 转录音频，输出流式文本 |
| **TextAggregatorStation** | `TEXT_DELTA` | `TEXT` + `STREAM_COMPLETE` | 聚合 ASR 输出的 delta 为完整文本 |
| **AgentStation** | `TEXT` | `TEXT_DELTA` + `STREAM_COMPLETE` | 调用 LLM，输出流式回复 |
| **SentenceAggregatorStation** | `TEXT_DELTA` | `TEXT` (per sentence) + `STREAM_COMPLETE` | 按句子切分 Agent 输出 |
| **TTSStation** | `TEXT` | `TTS_START` + `AUDIO_RAW` + `TTS_STOP` + `STREAM_COMPLETE` | 合成语音 |

## Completion 信号流

```
VAD ──(no complete)──► TurnDetector ──(no complete)──► ASR
                                                        │
                                                        ▼ STREAM_COMPLETE
                                                   TextAggregator
                                                        │
                                                        ▼ STREAM_COMPLETE
                                                      Agent
                                                        │
                                                        ▼ STREAM_COMPLETE
                                                 SentenceAggregator
                                                        │
                                                        ▼ STREAM_COMPLETE
                                                       TTS
```

## Station 类型说明

### DetectorStation
- **用途**：状态检测器，发出事件
- **特点**：不处理数据，只监测状态变化并发出事件
- **例子**：VADStation, TurnDetectorStation

### StreamStation
- **用途**：流式处理
- **特点**：一对多转换，可能长时间处理，支持超时和中断
- **例子**：ASRStation, AgentStation, TTSStation

### BufferStation
- **用途**：缓冲聚合
- **特点**：收集 delta 直到 completion 信号，然后输出聚合结果
- **例子**：TextAggregatorStation, SentenceAggregatorStation

## 事件类型说明

### VAD 事件
- `EVENT_VAD_START`: 语音活动开始
- `EVENT_VAD_END`: 语音活动结束

### Bot 状态事件
- `EVENT_BOT_STARTED_SPEAKING`: Bot 开始说话
- `EVENT_BOT_STOPPED_SPEAKING`: Bot 停止说话
- `EVENT_BOT_THINKING`: Bot 开始处理（ASR 开始时发出）

### TTS 事件
- `EVENT_TTS_START`: TTS 开始合成
- `EVENT_TTS_SENTENCE_START`: 句子合成开始
- `EVENT_TTS_SENTENCE_END`: 句子合成结束
- `EVENT_TTS_STOP`: TTS 结束

### 完成事件
- `EVENT_STREAM_COMPLETE`: 流处理完成，触发下游 `on_completion`

## DAG 连接示例

### 标准语音对话

```python
# 主链
dag.add_edge("transport_in", "vad", "turn_detector", "asr", 
             "text_agg", "agent", "sentence_agg", "tts", "transport_out")

# STT 结果分支（显示识别文本）
dag.add_edge("asr", "transport_out")
```

### 简化配置（无 TurnDetector）

```python
# VAD 直接输出到 ASR（适用于简单场景）
dag.add_edge("transport_in", "vad", "asr", "text_agg", "agent", 
             "sentence_agg", "tts", "transport_out")
```

## Middleware 自动配置

各 Station 类型自动应用的 Middleware：

| Station 类型 | InputValidator | SignalHandler | InterruptDetector | LatencyMonitor | ErrorHandler |
|-------------|----------------|---------------|-------------------|----------------|--------------|
| DetectorStation | ✅ | ✅ | ❌ | ❌ | ✅ |
| StreamStation | ✅ | ✅ | ✅ | ✅ | ✅ |
| BufferStation | ✅ | ✅ | ❌ | ❌ | ✅ |
