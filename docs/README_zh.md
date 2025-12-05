# Vixio

一个基于流水线架构的流式语音 Agent 框架。

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**[English](../README.md)**

## 特性

- **流水线架构**：串联多个工站形成完整的处理流程
- **工站化处理**：每个工站负责特定任务（VAD/ASR/Agent/TTS）
- **流式支持**：基于异步队列链的实时音频处理
- **模块化设计**：按需安装所需组件
- **多提供商支持**：支持本地推理和云服务

## 环境要求

- Python 3.12 或更高版本
- [uv](https://docs.astral.sh/uv/)（推荐的包管理器）

## 安装

### 使用 uv（推荐）

1. 仅安装核心依赖：

```bash
uv pip install vixio
```

2. 安装特定提供商：

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

### ASR（自动语音识别）
- `sherpa-onnx-asr-grpc` - 通过 gRPC 服务使用 Sherpa-ONNX ASR
- `sherpa-onnx-asr-local` - Sherpa-ONNX ASR 本地推理
- `qwen` - 通义千问平台 ASR

### TTS（语音合成）
- `kokoro-cn-tts-grpc` - 通过 gRPC 服务使用 Kokoro TTS
- `kokoro-cn-tts-local` - Kokoro TTS 本地推理
- `edge-tts` - 微软 Edge TTS（云服务）
- `qwen` - 通义千问平台 TTS

### Agent
- `openai-agent` - 通过 LiteLLM 使用 OpenAI 兼容的 LLM

## 快速开始

1. 查看 `examples/` 目录获取使用示例
2. 在 YAML 配置文件中配置你的提供商
3. 运行你的语音 Agent 应用

详细配置和使用指南请参阅[文档](.)。

## 项目状态

**当前版本：v0.1.0（Alpha）**

> **注意**：本项目正在积极开发中，API 可能会发生变化。

## 许可证

MIT 许可证 - 详见 [LICENSE](../LICENSE)。
