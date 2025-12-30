# Vixio

**A framework for quickly adding voice interaction to AI Agents**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**[中文文档](docs/README_zh.md)**

## Why Vixio?

Add voice capabilities to any Agent with a single command — no need to handle complex audio processing details.

## Features

### 🎯 Core Advantages

- **One-line startup**: `uvx vixio run xiaozhi-server --preset qwen-realtime` gives you a complete voice Agent
- **Flexible DAG architecture**: Data flow design based on directed acyclic graph, nodes can be freely combined
- **Three operating modes**:
  - **Pipeline** - Traditional cascade (VAD→ASR→Agent→TTS), maximum control
  - **Streaming** - Bidirectional streaming, low latency
  - **Realtime** - End-to-end model, simplest
- **Ready to use**: Built-in Xiaozhi hardware protocol support

### 🔧 Technical Features

- **Modular design**: Install VAD / ASR / Agent / TTS on demand
- **Multiple providers**: Local inference (Silero, Sherpa-ONNX, Kokoro) or cloud services (Qwen, Doubao, Edge-TTS)
- **Multi-purpose**: Voice conversation, transcription, real-time translation, etc.
- **Session isolation**: Independent provider instances per connection, supports high concurrency

## Requirements

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (recommended package manager)

## 🚀 Quick Start

Get started with Vixio in just one command! Experience real-time voice conversation powered by Qwen Omni:

```bash
# Install and run in one step (requires DashScope API key)
uvx vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-your-key-here
```

**What you get:**
- 🎙️ WebSocket server running at `http://localhost:8000`
- 🤖 End-to-end voice AI with Qwen Omni Realtime
- ⚡ Low latency, integrated VAD + ASR + LLM + TTS
- 📱 Ready for xiaozhi devices or custom clients

**Get your API key:** [DashScope Console](https://dashscope.console.aliyun.com/)

### Customize Your Bot

```bash
# Use custom prompt
uvx vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-xxx \
  --prompt "你是一个专业的编程助手"

# Use pipeline mode (more control)
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx

# Export template for full customization
uvx vixio init xiaozhi-server
cd xiaozhi-server
# Edit .env, config.yaml, prompt.txt
python run.py
```

---

## Installation

### Install from source (Recommended)

```bash
git clone https://github.com/weynechen/vixio.git
cd vixio
uv sync --extra dev-qwen  # or dev-local-cn, dev-grpc, etc.
```

### Using uv

1. Install with core dependencies only:

```bash
uv pip install vixio
```

2. Install with specific providers:

```bash
# For Chinese local development (VAD + ASR + TTS + Agent)
uv pip install "vixio[dev-local-cn]"

# For Qwen platform integration
uv pip install "vixio[dev-qwen]"

# Or install individual components
uv pip install "vixio[xiaozhi,openai-agent,silero-vad-grpc]"
```

### Using pip

```bash
pip install vixio

# With optional dependencies
pip install "vixio[dev-local-cn]"
```


## Available Components

### Transports
- `xiaozhi` - Xiaozhi protocol transport (WebSocket + HTTP)

### VAD (Voice Activity Detection)
- `silero-vad-grpc` - Silero VAD via gRPC service
- `silero-vad-local` - Silero VAD local inference

### ASR (Automatic Speech Recognition)
- `sherpa-onnx-asr-grpc` - Sherpa-ONNX ASR via gRPC service
- `sherpa-onnx-asr-local` - Sherpa-ONNX ASR local inference
- `qwen` - Qwen platform ASR
...

### TTS (Text-to-Speech)
- `kokoro-cn-tts-grpc` - Kokoro TTS via gRPC service
- `kokoro-cn-tts-local` - Kokoro TTS local inference
- `edge-tts` - Microsoft Edge TTS (cloud)
- `qwen` - Qwen platform TTS
...

### Agent
- `openai-agent` - OpenAI-compatible LLM via LiteLLM


## Getting Started

1. Check out the `examples/` directory for usage examples
2. Configure your providers in a YAML config file
3. Run your voice agent application


## Project Status

**Current Version: v0.1.x (Alpha)**

> **Note**: This project is under active development. APIs may change.

## License

Apache License - see [LICENSE](LICENSE) for details.
