# Vixio

**为 AI Agent 快速添加语音交互能力的框架**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**[English](../README.md)**

## Why Vixio?

Vixio 让你用一条命令就能为任何 Agent 添加语音能力，无需关心复杂的音频处理细节。借助xiaozhi客户端，可以快速的完成和硬件交互。

## 特性

### 🎯 核心优势

- **一行代码启动**：`uvx vixio run xiaozhi-server --preset qwen-realtime` 即可拥有完整语音 Agent
- **灵活的 DAG 架构**：基于有向无环图的数据流设计，节点可自由组合
- **三种工作模式**：
  - **Pipeline** - 传统级联 (VAD→ASR→Agent→TTS)，最大控制力
  - **Streaming** - 双向流式，低延迟
  - **Realtime** - 端到端模型，最简单
- **开箱即用**：内置小志 (Xiaozhi) 硬件协议支持

### 🔧 技术特性

- **模块化设计**：VAD / ASR / Agent / TTS 按需安装
- **多 Provider 支持**：本地推理 (Silero, Sherpa-ONNX, Kokoro) 或云服务 (Qwen, Doubao, Edge-TTS)
- **多场景适用**：语音对话、语音转录、实时翻译等
- **Session 隔离**：每个连接独立的 Provider 实例，支持高并发

## 环境要求

- Python 3.12 或更高版本
- [uv](https://docs.astral.sh/uv/)（推荐的包管理器）

## 🚀 快速开始

一条命令启动语音对话服务！

```bash
# 安装并运行（需要 DashScope API Key）
uvx vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-your-key-here
```

**启动后你将获得：**
- 🎙️ WebSocket 服务运行在 `http://localhost:8000`
- 🤖 端到端语音 AI（Qwen Omni Realtime）
- ⚡ 低延迟，集成 VAD + ASR + LLM + TTS
- 📱 支持小志设备或自定义客户端

**获取 API Key：** [DashScope 控制台](https://dashscope.console.aliyun.com/)

### 自定义你的 Bot

```bash
# 使用自定义提示词
uvx vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-xxx \
  --prompt "你是一个专业的编程助手"

# 使用 pipeline 模式（更多控制）
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx

# 导出模板进行完全自定义
uvx vixio init xiaozhi-server
cd xiaozhi-server
# 编辑 .env, config.yaml, prompt.txt
python run.py
```

## 安装

### 从源码安装（推荐）

```bash
git clone https://github.com/weynechen/vixio.git
cd vixio
uv sync --extra dev-qwen  # 或 dev-local-cn, dev-grpc 等
```

### 使用 uv

1. 仅安装核心依赖：

```bash
uv pip install vixio
```

2. 安装特定 Provider：

```bash
# 中文本地开发环境（VAD + ASR + TTS + Agent）
uv pip install "vixio[dev-local-cn]"

# Qwen 平台集成
uv pip install "vixio[dev-qwen]"

# 或单独安装各组件
uv pip install "vixio[xiaozhi,openai-agent,silero-vad-grpc]"
```

### 使用 pip

```bash
pip install vixio

# 安装可选依赖
pip install "vixio[dev-local-cn]"
```

## 可用组件

### 传输层（Transport）
- `xiaozhi` - 小智协议传输（WebSocket + HTTP）

### VAD（语音活动检测）
- `silero-vad-grpc` - 通过 gRPC 服务使用 Silero VAD
- `silero-vad-local` - Silero VAD 本地推理
...

### ASR（自动语音识别）
- `sherpa-onnx-asr-grpc` - 通过 gRPC 服务使用 Sherpa-ONNX ASR
- `sherpa-onnx-asr-local` - Sherpa-ONNX ASR 本地推理
- `qwen` - 通义千问平台 ASR
...

### TTS（语音合成）
- `kokoro-cn-tts-grpc` - 通过 gRPC 服务使用 Kokoro TTS
- `kokoro-cn-tts-local` - Kokoro TTS 本地推理
- `edge-tts` - 微软 Edge TTS（云服务）
- `qwen` - 通义千问平台 TTS
...

### Agent
- `openai-agent` - 通过 LiteLLM 使用 OpenAI 兼容的 LLM

## 快速开始

1. 查看 `examples/` 目录获取使用示例
2. 在 YAML 配置文件中配置你的 Provider
3. 运行你的语音 Agent 应用


## 项目状态

**当前版本：v0.1.x（Alpha）**

> **注意**：本项目正在积极开发中，API 可能会发生变化。

## 许可证

Apache 许可证 - 详见 [LICENSE](../LICENSE)。
