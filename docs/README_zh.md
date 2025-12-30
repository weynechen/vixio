# Vixio

**为 AI Agent 快速添加语音交互能力，同时兼容xiaozhi协议，利用众多的xiaozhi设备，快速接入硬件**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**[English](../README.md)**

## Why Vixio?

- Vixio 以Agent为核心，快速为任何 Agent 添加语音能力，无需关心复杂的音频处理细节。
- 兼容xiaozhi协议，快速接入硬件。
- 同时可作为xiaozhi的server ，一行代码即可快速启动。

## 特性

### 🎯 核心优势

- **灵活的 DAG 架构**：基于有向无环图的数据流设计，节点可自由组合。除语音对话外，还有转录、实时翻译、数字人等的功能。

- **三种工作模式**：
  - **Pipeline** - 传统级联 (VAD→ASR→Agent→TTS)，最大控制力
  - **Streaming** - 双向流式，低延迟
  - **Realtime** - 端到端模型，延迟最低
- **提供多个服务商**。提供包括openai,qwen,doubao等的服务商的支持，持续更新中。
- **开箱即用**：内置小志 (Xiaozhi) 硬件协议支持
- **接口无关**: 接口抽象为transport,可替换成其他任意协议。
- **支持本地推理** : 提供grpc的统一抽象，同时提供多种常用的模型的本地inference。


## 环境要求

- Python 3.12 或更高版本
- [uv](https://docs.astral.sh/uv/)（推荐的包管理器）

## 🚀 快速开始

### Step1 : 获取 API Key
访问： [DashScope 控制台](https://dashscope.console.aliyun.com/) ，获得密钥。

### Step2 : 一条命令启动`xiaozhi`语音对话服务！

```bash
uvx --from "vixio[dev-qwen-streaming]" vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-your-key-here
```

**启动后你将获得：**
- WebSocket 服务运行在 `http://localhost:8000`
- 端到端语音 AI（Qwen Omni Realtime）
- 低延迟
- 支持小志设备或自定义客户端

### Step3 : 重新编译xiaozhi固件
- 执行 `idf.py menuconfig` 
- 选择 Xiaozhi Asssistant 
- 将OTA地址修改为控制台显示的地址。 

你完成了将服务器地址填写到xiaozhi的设备中。由此，即可开始对话。

如果不满足与默认的配置，可以尝试自定义 ： 

### 自定义你的 Bot

```bash
# 使用自定义提示词
uvx --from "vixio[dev-qwen-streaming]" vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-xxx \
  --prompt "你是一个专业的编程助手"

# 使用 pipeline 模式（更多控制）
uvx --from "vixio[dev-qwen-streaming]" vixio run xiaozhi-server \
  --dashscope-key sk-xxx

# 导出模板进行完全自定义
uvx --from "vixio[xiaozhi]" vixio init xiaozhi-server
cd xiaozhi-server
# 编辑 .env, config.yaml, prompt.txt
python run.py
```

## 尝试示例

如果有更进一步的自定义需求，可参考example中的示例

### 从源码安装

```bash
git clone https://github.com/weynechen/vixio.git
cd vixio
uv sync --extra dev-qwen  # 或 dev-local-cn, dev-grpc 等
```
### 浏览配置
config/provider.yaml中，有多种默认配置：
- `dev-in-process` ： 使用此配置，将所有的本地推理放在单个进程内进行。此方式无需启动复杂的微服务，但每个连接会启动一个推理服务，资源损耗大。适合快速进行本地推理测试。

- `dev-grpc` : 使用此配置，将本地推理作为单个微服务启动。主进程通过grpc连接微服务。此方式，需要先手动启动每个微服务。 可以进入到 inference 目录逐个启动(逐个uv run)，或者使用docker compose 。

- `dev-qwen-xxx` : 此配置使用阿里云端的服务。配置好自己的密钥即可运行，本地依赖较小。

### 运行example

- 双向流式ASR和TTS的使用：

```bash
uv run python examples/xiaozhi/streaming.py
```
借助云端双向流式，可以做到 1～2s 的首句回复延时。同时保持自主的agent ， 完整的工具调用能力。平时建议使用该方式。


- realtime : 
```bash
uv run python examples/xiaozhi/realtime_chat.py --env dev-qwen-realtime
```
使用端到端的时时模型，可以做到 < 1S 的首句回复延时。但受限于模型的能力，无法调用工具。（暂时）

- 传统集联模式。
```bash
# Development mode - In-process inference (no external services needed) . 
  uv run python examples/xiaozhi/pipeline.py --env dev-in-process
  
  # Development mode - with gRPC microservices
  uv run python examples/xiaozhi/pipeline.py --env dev-grpc 

  # Or use qwen 
  uv run python examples/xiaozhi/pipeline.py --env dev-qwen-pipeline
```
此模式的自由度最高，但延时为 1.5 ~ 3S 。
注意，此模式下，需要下载模型文件。可能需要较久的启动时间。

## 可用组件

### 传输层（Transport）
- `xiaozhi` - 小智协议传输（WebSocket + HTTP）

其他协议正在制定和开发中 ...

### VAD（语音活动检测）
- `silero-vad-grpc` - 通过 gRPC 服务使用 Silero VAD
- `silero-vad-local` - Silero VAD 本地推理

持续补充中...

### ASR（自动语音识别）
- `sherpa-onnx-asr-grpc` - 通过 gRPC 服务使用 Sherpa-ONNX ASR
- `sherpa-onnx-asr-local` - Sherpa-ONNX ASR 本地推理
- `qwen` - 通义千问平台 ASR

持续补充中...

### TTS（语音合成）
- `kokoro-cn-tts-grpc` - 通过 gRPC 服务使用 Kokoro TTS
- `kokoro-cn-tts-local` - Kokoro TTS 本地推理
- `edge-tts` - 微软 Edge TTS（云服务）
- `qwen` - 通义千问平台 TTS

持续补充中...

### Agent
- `openai-agent` - 通过 LiteLLM 使用 OpenAI 兼容的 LLM

持续补充中...

## 参考
https://github.com/78/xiaozhi-esp32

## 项目状态

**当前版本：v0.1.x（Alpha）**

> **注意**：本项目正在积极开发中，API 可能会发生变化。

## 许可证

Apache 许可证 - 详见 [LICENSE](../LICENSE)。
