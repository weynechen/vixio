# Sherpa ONNX ASR Microservice

基于 Sherpa ONNX 的 gRPC 语音识别服务。

## 功能特性

- **多语言支持**: 中文、英文、日语、韩语、粤语
- **流式识别**: 支持实时音频流处理
- **会话隔离**: 每个会话独立状态，互不干扰
- **gRPC接口**: 高性能二进制协议

## 依赖管理

本服务使用**独立的 pyproject.toml**：

```toml
dependencies = [
    "numpy>=1.24.0",
    "grpcio>=1.60.0",
    "grpcio-tools>=1.60.0",
    "sherpa-onnx>=1.12.15",
    "loguru>=0.7.0",
]
```

### 安装依赖

```bash
cd micro_services/sherpa_onnx_local
uv sync
```

## 模型准备

### 下载 SenseVoice 模型

```bash
# 从主项目根目录运行
cd models

# 下载 SenseVoice 多语言模型
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2

# 解压
tar -xjf sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
```

模型结构：
```
models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/
├── model.int8.onnx      # 量化模型（推荐）
├── model.onnx           # 原始模型
└── tokens.txt           # 词表
```

## 使用方法

### 1. 编译Proto

```bash
cd micro_services/sherpa_onnx_local
./compile_proto.sh
```

### 2. 启动服务

```bash
# 使用默认模型路径
uv run python server.py --port 50052

# 指定模型路径
uv run python server.py --port 50052 \
  --model-path ../../models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
```

### 3. 测试服务

```bash
# 测试客户端
cd micro_services/sherpa_onnx_local
uv run python client.py
```

## API接口

### CreateSession

创建 ASR 会话。

```protobuf
message CreateSessionRequest {
  string session_id = 1;
  string language = 2;  // "auto" / "zh" / "en" / "ja" / "ko" / "yue"
}
```

### Transcribe

转录音频为文本。

```protobuf
message TranscribeRequest {
  string session_id = 1;
  repeated bytes audio_chunks = 2;  // PCM 16kHz 16-bit mono
}
```

### DestroySession

销毁会话。

```protobuf
message DestroySessionRequest {
  string session_id = 1;
}
```

## 性能指标

### 资源使用

- **内存**: 200-400MB（模型加载后）
- **CPU**: 1-2核（识别时）
- **模型大小**: ~400MB（int8 量化版）

### 延迟

- **单句识别**: ~100-300ms
- **实时因子**: 0.1-0.3（比实时快3-10倍）

### 并发能力

- **单副本**: 5-10并发会话（受CPU限制）
- **K8s HPA (2-10副本)**: 50-100并发会话

### 吞吐量

- **单副本**: ~100-200 请求/秒
- **K8s (10副本)**: ~1000-2000 请求/秒

## Docker部署

### 构建镜像

```bash
cd /path/to/vixio

docker build -t vixio-sherpa-asr:latest \
    -f micro_services/sherpa_onnx_local/Dockerfile .
```

### 运行

```bash
docker run -p 50052:50052 \
    -v $(pwd)/models:/app/models \
    vixio-sherpa-asr:latest
```

## Kubernetes部署

```bash
# 部署
kubectl apply -f ../../k8s/sherpa-asr-service.yaml

# 查看状态
kubectl get pods -l app=sherpa-asr-service
kubectl get hpa sherpa-asr-hpa -w

# 查看日志
kubectl logs -l app=sherpa-asr-service -f
```

### HPA配置

- **minReplicas**: 2
- **maxReplicas**: 10
- **targetCPUUtilization**: 70%

## 支持的语言

| 语言 | 代码 | 说明 |
|------|------|------|
| 自动检测 | `auto` | 自动检测语言 |
| 中文 | `zh` | 简体中文/繁体中文 |
| 英文 | `en` | English |
| 日语 | `ja` | 日本語 |
| 韩语 | `ko` | 한국어 |
| 粤语 | `yue` | 粵語 |

## 故障排查

### 模型加载失败

```bash
# 检查模型文件
ls -lh models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/

# 确保包含
# - model.int8.onnx
# - tokens.txt
```

### 内存不足

使用 int8 量化模型（默认）：
- 模型文件：`model.int8.onnx` (约400MB)
- 比 `model.onnx` (约900MB) 小2倍

### 识别精度低

1. 检查音频格式：
   - 必须是 PCM 16-bit
   - 采样率 16kHz
   - 单声道 (mono)

2. 尝试指定语言：
   ```python
   await client.create_session("session-1", language="zh")
   ```

## 开发建议

### 1. 虚拟环境

```bash
cd micro_services/sherpa_onnx_local
uv sync  # 创建独立的 .venv/
```

### 2. 测试

```bash
cd micro_services/sherpa_onnx_local
uv run pytest tests/
```

### 3. 日志

服务日志：
```bash
tail -f logs/sherpa_asr.log
```

## 参考资料

- [Sherpa ONNX GitHub](https://github.com/k2-fsa/sherpa-onnx)
- [SenseVoice 模型](https://github.com/FunAudioLLM/SenseVoice)
- [微服务架构](../../ARCHITECTURE.md)

