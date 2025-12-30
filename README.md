# Vixio

**Quickly add voice interaction capabilities to AI Agents, with Xiaozhi protocol compatibility for seamless hardware integration**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**[中文文档](docs/README_zh.md)**

## Why Vixio?

- Vixio is Agent-centric — quickly add voice capabilities to any Agent without dealing with complex audio processing details.
- Compatible with Xiaozhi protocol for rapid hardware integration.
- Can serve as a Xiaozhi server — start with just one command.

## Features

### 🎯 Core Advantages

- **Flexible DAG Architecture**: Data flow design based on directed acyclic graphs, nodes can be freely combined. Beyond voice conversation, supports transcription, real-time translation, digital humans, and more.

- **Three Operating Modes**:
  - **Pipeline** - Traditional cascade (VAD→ASR→Agent→TTS), maximum control
  - **Streaming** - Bidirectional streaming, low latency
  - **Realtime** - End-to-end model, lowest latency
- **Multiple Providers**: Support for OpenAI, Qwen, Doubao and more, continuously expanding.
- **Ready to Use**: Built-in Xiaozhi hardware protocol support
- **Interface Agnostic**: Interfaces abstracted as transports, can be replaced with any protocol.
- **Local Inference Support**: Unified gRPC abstraction with local inference for various common models.


## Requirements

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (recommended package manager)

## 🚀 Quick Start

### Step 1: Get API Key
Visit: [DashScope Console](https://dashscope.console.aliyun.com/) to obtain your key.

### Step 2: Start Xiaozhi Voice Chat Service with One Command!

```bash
uvx --from "vixio[dev-qwen-streaming]" vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-your-key-here
```

**What you get:**
- WebSocket server running at `http://localhost:8000`
- End-to-end voice AI (Qwen Omni Realtime)
- Low latency
- Ready for Xiaozhi devices or custom clients

### Step 3: Recompile Xiaozhi Firmware
- Run `idf.py menuconfig`
- Select Xiaozhi Assistant
- Change the OTA address to the address shown in the console.

You have now configured the server address in your Xiaozhi device. You can start chatting!

If the default configuration doesn't meet your needs, try customizing:

### Customize Your Bot

```bash
# Use custom prompt
uvx --from "vixio[dev-qwen-streaming]" vixio run xiaozhi-server \
  --preset qwen-realtime \
  --dashscope-key sk-xxx \
  --prompt "You are a professional programming assistant"

# Use pipeline mode (more control)
uvx --from "vixio[dev-qwen-streaming]" vixio run xiaozhi-server \
  --dashscope-key sk-xxx

# Export template for full customization
uvx --from "vixio[xiaozhi]" vixio init xiaozhi-server
cd xiaozhi-server
# Edit .env, config.yaml, prompt.txt
python run.py
```

## Try the Examples

For more advanced customization, refer to the examples in the examples directory.

### Install from Source

```bash
git clone https://github.com/weynechen/vixio.git
cd vixio
uv sync --extra dev-qwen  # or dev-local-cn, dev-grpc, etc.
```

### Browse Configurations
In config/provider.yaml, there are multiple default configurations:
- `dev-in-process`: With this configuration, all local inference runs in a single process. No need to start complex microservices, but each connection starts its own inference service, consuming more resources. Suitable for quick local inference testing.

- `dev-grpc`: With this configuration, local inference runs as individual microservices. The main process connects to microservices via gRPC. You need to manually start each microservice first. You can go to the inference directory and start them individually (uv run each), or use docker compose.

- `dev-qwen-xxx`: This configuration uses Alibaba Cloud services. Configure your key and run — minimal local dependencies.

### Run Examples

- Bidirectional streaming ASR and TTS usage:

```bash
uv run python examples/xiaozhi/streaming.py
```
With cloud-based bidirectional streaming, you can achieve 1-2s first response latency. Maintains autonomous agent with full tool calling capability. Recommended for regular use.


- Realtime:
```bash
uv run python examples/xiaozhi/realtime_chat.py --env dev-qwen-realtime
```
Using end-to-end realtime models, you can achieve < 1s first response latency. However, due to model limitations, tool calling is not available (for now).

- Traditional cascade mode:
```bash
  # Development mode - In-process inference (no external services needed) . 
  uv run python examples/xiaozhi/pipeline.py --env dev-in-process
  
  # Development mode - with gRPC microservices
  uv run python examples/xiaozhi/pipeline.py --env dev-grpc 

  # Or use qwen 
  uv run python examples/xiaozhi/pipeline.py --env dev-qwen-pipeline
```
This mode offers the highest flexibility, but latency is 1.5-3s.

## Available Components

### Transport
- `xiaozhi` - Xiaozhi protocol transport (WebSocket + HTTP)

Other protocols are being designed and developed...

### VAD (Voice Activity Detection)
- `silero-vad-grpc` - Silero VAD via gRPC service
- `silero-vad-local` - Silero VAD local inference

More coming...

### ASR (Automatic Speech Recognition)
- `sherpa-onnx-asr-grpc` - Sherpa-ONNX ASR via gRPC service
- `sherpa-onnx-asr-local` - Sherpa-ONNX ASR local inference
- `qwen` - Qwen platform ASR

More coming...

### TTS (Text-to-Speech)
- `kokoro-cn-tts-grpc` - Kokoro TTS via gRPC service
- `kokoro-cn-tts-local` - Kokoro TTS local inference
- `edge-tts` - Microsoft Edge TTS (cloud)
- `qwen` - Qwen platform TTS

More coming...

### Agent
- `openai-agent` - OpenAI-compatible LLM via LiteLLM

More coming...


## Reference
https://github.com/78/xiaozhi-esp32


## Project Status

**Current Version: v0.1.x (Alpha)**

> **Note**: This project is under active development. APIs may change.

## License

Apache License - see [LICENSE](LICENSE) for details.
