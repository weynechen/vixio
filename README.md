# Vixio

A streaming voice-powered agent framework based on pipeline architecture

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**[中文文档](docs/README_zh.md)**

## Features

- **Pipeline Architecture**: Chain multiple stations to form a complete processing flow
- **Station-based Processing**: Each station handles specific tasks (VAD/ASR/Agent/TTS)
- **Streaming Support**: Real-time audio processing with async queue chains
- **Modular Design**: Install only what you need with optional dependencies
- **Multiple Providers**: Support for both local inference and cloud services

## Requirements

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (recommended package manager)

## Installation

### Using uv (Recommended)

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

### TTS (Text-to-Speech)
- `kokoro-cn-tts-grpc` - Kokoro TTS via gRPC service
- `kokoro-cn-tts-local` - Kokoro TTS local inference
- `edge-tts` - Microsoft Edge TTS (cloud)
- `qwen` - Qwen platform TTS

### Agent
- `openai-agent` - OpenAI-compatible LLM via LiteLLM

### docker 
CPU version
```bash
docker compose -f docker-compose-inference.yml up -d --build
```
for China user
```bash
docker compose -f docker-compose-inference.yml -f docker-compose-inference-cn.yml up -d --build
```

GPU version
```bash
docker compose -f docker-compose-inference-gpu.yml up -d --build
```
for china user:
```bash
docker compose -f docker-compose-inference-gpu.yml -f docker-compose-inference-gpu-cn.yml up -d --build
```

## Getting Started

1. Check out the `examples/` directory for usage examples
2. Configure your providers in a YAML config file
3. Run your voice agent application

For detailed configuration and usage guide, see the [documentation](docs/).



## Project Status

**Current Version: v0.1.0 (Alpha)**

> **Note**: This project is under active development. APIs may change.

## License

MIT License - see [LICENSE](LICENSE) for details.
