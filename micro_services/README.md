# Micro Services

本地部署的gRPC微服务，按提供商聚合。每个微服务**完全自包含**，包括：
- Proto定义和生成代码
- 服务端和客户端实现
- **独立的依赖管理** (pyproject.toml)
- Docker部署配置

## 目录结构

```
micro_services/
├── silero_vad/              # Silero VAD微服务
│   ├── pyproject.toml      # ✨ 独立依赖管理
│   ├── vad.proto           # Proto定义
│   ├── vad_pb2.py          # 生成的代码
│   ├── vad_pb2_grpc.py     # 生成的gRPC代码
│   ├── compile_proto.sh    # Proto编译脚本
│   ├── server.py           # gRPC服务端
│   ├── client.py           # gRPC客户端
│   ├── Dockerfile          # Docker镜像
│   └── __init__.py
│
├── sherpa_onnx_local/      # Sherpa ONNX ASR微服务
│   ├── pyproject.toml      # ✨ 独立依赖管理
│   ├── asr.proto           # Proto定义
│   ├── asr_pb2.py
│   ├── asr_pb2_grpc.py
│   ├── compile_proto.sh
│   └── __init__.py
│
├── kokoro/                 # Kokoro TTS微服务
│   ├── pyproject.toml      # ✨ 独立依赖管理
│   ├── tts.proto
│   ├── tts_pb2.py
│   ├── tts_pb2_grpc.py
│   ├── compile_proto.sh
│   ├── kokoro.py           # 模型封装
│   ├── server.py
│   ├── client.py
│   ├── Dockerfile
│   └── __init__.py
│
└── README.md               # 本文档
```

## 设计原则

### 1. 完全自包含 + 独立依赖

每个微服务目录包含：
- **独立依赖** - pyproject.toml (仅包含本服务需要的依赖)
- **Proto定义** - 自己的.proto文件
- **生成代码** - 编译后的*_pb2.py和*_pb2_grpc.py
- **编译脚本** - compile_proto.sh
- **服务实现** - server.py和client.py
- **部署配置** - Dockerfile

**优点**：
- ✅ **依赖隔离** - 避免版本冲突（如不同服务需要不同torch版本）
- ✅ **独立开发** - 每个服务可以独立安装和测试
- ✅ **减小镜像** - Docker镜像只包含必要依赖
- ✅ **清晰所有权** - 依赖和代码在同一目录
- ✅ **易于维护** - 升级某个服务的依赖不影响其他服务

### 2. 按提供商聚合

每个微服务对应一个具体的提供商实现：
- `silero_vad/` - Silero VAD模型
- `sherpa_onnx_local/` - Sherpa ONNX ASR
- `kokoro/` - Kokoro TTS

### 3. 统一接口

所有微服务通过gRPC暴露服务：
- CreateSession - 创建会话
- Process - 处理数据（Detect/Transcribe/Synthesize）
- DestroySession - 销毁会话
- GetStats - 获取统计信息

## 安装依赖

### 方法1: 一键安装所有服务依赖（推荐）

```bash
cd /path/to/vixio
./scripts/dev/setup-services.sh
```

这会为每个微服务单独安装依赖，互不干扰。

### 方法2: 手动安装单个服务依赖

```bash
# 安装Silero VAD依赖
cd micro_services/silero_vad
uv sync

# 安装Sherpa ONNX ASR依赖
cd micro_services/sherpa_onnx_local
uv sync

# 安装Kokoro TTS依赖
cd micro_services/kokoro
uv sync
```

## 编译Proto

每个服务都有自己的编译脚本：

```bash
# 编译Silero VAD proto
cd silero_vad
./compile_proto.sh

# 编译Sherpa ONNX ASR proto
cd sherpa_onnx_local
./compile_proto.sh

# 编译Kokoro TTS proto
cd kokoro
./compile_proto.sh
```

## 启动服务

### 开发模式

```bash
# 启动Silero VAD服务
cd silero_vad
uv run python server.py --port 50051

# 或使用启动脚本（推荐）
cd ../..
./scripts/dev/start-all.sh
```

### Docker模式

```bash
docker-compose up -d silero-vad-service
```

### K8s模式

```bash
kubectl apply -f k8s/silero-vad-service.yaml
```

## 添加新服务

### 示例：添加新的VAD服务（WebRTC VAD）

1. **创建目录**
```bash
mkdir -p micro_services/webrtc_vad
cd micro_services/webrtc_vad
```

2. **创建Proto** (`vad.proto`)
```protobuf
syntax = "proto3";
package webrtc_vad;

service VADService {
  rpc CreateSession(CreateSessionRequest) returns (CreateSessionResponse);
  rpc Detect(DetectRequest) returns (DetectResponse);
  rpc DestroySession(DestroySessionRequest) returns (Empty);
}
```

3. **创建编译脚本** (`compile_proto.sh`)
```bash
#!/bin/bash
uv run python -m grpc_tools.protoc \
    -I. --python_out=. --grpc_python_out=. \
    vad.proto
sed -i 's/^import vad_pb2/from . import vad_pb2/' vad_pb2_grpc.py
```

4. **实现服务端** (`server.py`)
```python
from . import vad_pb2, vad_pb2_grpc

class VADService:
    def __init__(self):
        # 初始化WebRTC VAD
        pass
```

5. **实现客户端** (`client.py`)
```python
from . import vad_pb2, vad_pb2_grpc

class VADServiceClient:
    def __init__(self, service_url: str):
        self.service_url = service_url
```

6. **创建Provider** (`providers/webrtc_vad/grpc_provider.py`)
```python
from micro_services.webrtc_vad.client import VADServiceClient

@register_provider("webrtc-vad-grpc")
class LocalWebRTCVADProvider(VADProvider):
    # 实现Provider接口
    pass
```

## 与Provider层的关系

```
应用层
  ↓ 使用
providers/                      # Provider接口（应用层）
  └── silero_vad/
      └── grpc_provider.py     # LocalSileroVADProvider
            ↓ 使用
micro_services/                 # gRPC微服务（基础设施层）
  └── silero_vad/              # 完全自包含
      ├── vad.proto           # Proto定义（自己的）
      ├── server.py           # 服务端（独立进程）
      └── client.py           # 客户端（被Provider使用）
```

**依赖关系**：
- `providers/` → `micro_services/` (导入client)
- `micro_services/` ✗ `providers/` (不依赖)
- 应用层只看到 `providers/`

## 参考文档

- [架构文档](../ARCHITECTURE.md)
- [快速开始](../QUICK_START.md)
- [Provider开发指南](../providers/README.md)
