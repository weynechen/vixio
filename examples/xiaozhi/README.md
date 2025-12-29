# Vixio Examples

演示如何使用 Vixio 框架构建语音对话应用。

## 📁 示例文件

| 文件 | 说明 | 复杂度 |
|------|------|--------|
| `realtime_chat.py` | 端到端实时语音对话 | ⭐ 最简单 |
| `streaming.py` | 流式语音对话，低延迟 | ⭐⭐ |
| `pipeline.py` | 完整管道，最大灵活性和控制 | ⭐⭐⭐ |
| `transcribe.py` | 纯语音转文字，无 AI 对话 | ⭐⭐ |

### 架构对比

```
realtime_chat:  Audio → Realtime(VAD+ASR+LLM+TTS) → Audio     (1 station)
streaming:      Audio → StreamingASR → Agent → StreamingTTS → Audio  (4 stations)
pipeline:       Audio → VAD → TurnDetector → ASR → Agent → TTS → Audio  (7 stations)
transcribe:     Audio → VAD → TurnDetector → ASR → Text   (4 stations)
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd /path/to/vixio
uv sync
```

### 2. 设置环境变量

在项目根目录创建 `.env` 文件：

```bash

cp .env.example .env

# 通用
API_KEY=your-api-key

# 通义千问 (Qwen)
DASHSCOPE_API_KEY=your-dashscope-api-key

# 豆包 (Doubao)
DOUBAO_API_KEY=your-doubao-api-key
```

### 3. 运行示例

```bash
# 实时对话 (最简单，推荐)
uv run python examples/xiaozhi/realtime_chat.py --env dev-qwen-realtime

# 流式对话
uv run python examples/xiaozhi/streaming.py --env dev-qwen-streaming

# 完整管道
uv run python examples/xiaozhi/pipeline.py --env dev-qwen-pipeline

# 纯语音转文字
uv run python examples/xiaozhi/transcribe.py --env dev-qwen
```

启动后，服务器监听 `ws://0.0.0.0:8000/xiaozhi/v1/`。

## 📝 配置文件

配置文件位于 `config/providers.yaml`，定义不同环境的 Provider：

```yaml
dev-qwen-realtime:
  providers:
    realtime:
      provider: qwen-realtime
      config:
        api_key: ${DASHSCOPE_API_KEY}
        model: qwen2.5-omni-7b
```

### 环境变量替换

- `${VAR_NAME}` - 必需的环境变量
- `${VAR_NAME:default}` - 带默认值的环境变量

## 🔧 常用参数

```bash
# 指定配置文件
uv run python examples/xiaozhi/realtime_chat.py --config my_config.yaml

# 开启调试日志
uv run python examples/xiaozhi/pipeline.py --debug LatencyMonitor

# 设置会话超时
uv run python examples/xiaozhi/pipeline.py --turn-timeout 60
```
