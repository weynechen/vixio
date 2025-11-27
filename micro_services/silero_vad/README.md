# Silero VAD Microservice

基于Silero VAD模型的gRPC语音活动检测服务。

## 功能特性

- **高精度VAD**: 使用Silero VAD v5模型，准确检测语音活动
- **VAD周期锁**: 细粒度锁机制（START→END），支持高并发
- **会话隔离**: 每个会话独立状态，互不干扰
- **流式处理**: 支持实时音频流处理
- **gRPC接口**: 高性能二进制协议

## 依赖管理

本服务使用**独立的 pyproject.toml**，依赖与其他服务隔离。

### 安装依赖

```bash
cd /path/to/vixio/micro_services/silero_vad

# 安装依赖
uv sync

# 查看已安装的包
uv pip list
```

### 依赖列表

```toml
dependencies = [
    "torch>=2.0.0",           # PyTorch框架
    "numpy>=1.24.0",          # 数值计算
    "grpcio>=1.60.0",         # gRPC运行时
    "grpcio-tools>=1.60.0",   # Proto编译工具
    "silero-vad>=5.0",        # Silero VAD模型
    "loguru>=0.7.0",          # 日志库
]
```

## 使用方法

### 1. 编译Proto

```bash
cd /path/to/vixio/micro_services/silero_vad
./compile_proto.sh
```

### 2. 启动服务

```bash
# 使用uv运行（推荐）
uv run python server.py --port 50051 --log-level INFO

# 或直接运行
python server.py --port 50051
```

### 3. 测试服务

```bash
# 简单测试
cd /path/to/vixio
uv run python tests/test_vad_simple.py

# 并发测试
uv run python tests/test_vad_concurrent.py --sessions 20
```

## API接口

### CreateSession

创建VAD会话。

```protobuf
message CreateSessionRequest {
  string session_id = 1;
  float threshold = 2;               // 检测阈值 (默认: 0.5)
  float threshold_low = 3;           // 低阈值 (默认: 0.2)
  int32 frame_window_threshold = 4;  // 帧窗口阈值 (默认: 3)
}
```

### Detect (VAD周期锁)

检测语音活动，支持VAD事件。

```protobuf
message DetectRequest {
  string session_id = 1;
  bytes audio_data = 2;      // PCM 16kHz 16-bit mono
  string event = 3;          // "start" / "chunk" / "end"
}
```

**VAD事件说明**：
- `"start"` - 开始VAD周期，获取锁
- `"chunk"` - 继续检测，锁已持有
- `"end"` - 结束VAD周期，释放锁

**锁机制**：
```
Session-A: [VAD1: START→CHUNK...→END] ─── [VAD2: START→CHUNK...→END]
                   ↑ 锁住                          ↑ 锁住
Session-B:                    [VAD1: START...] ← 可以交错！
```

## 性能指标

### 资源使用

- **内存**: 150-200Mi（模型加载后）
- **CPU**: 200-500m（检测时）
- **模型大小**: ~5MB（Silero VAD）

### 延迟

- **单帧检测**: <5ms
- **VAD决策**: ~10-20ms（基于窗口）

### 并发能力

- **单副本**: 10-20并发会话
- **K8s HPA (2-10副本)**: 100-200并发会话

### 吞吐量

- **单副本**: ~1000-2000 请求/秒
- **K8s (10副本)**: ~10000-20000 请求/秒

## Docker部署

### 构建镜像

```bash
cd /path/to/vixio

# 构建（使用服务独立的依赖）
docker build -t vixio-silero-vad:latest \
    -f micro_services/silero_vad/Dockerfile .

# 运行
docker run -p 50051:50051 vixio-silero-vad:latest
```

### 镜像大小

```
vixio-silero-vad:latest    ~1.2GB
```

如果使用共享依赖（所有服务的依赖）：
```
vixio-monolith:latest      ~3.5GB  ❌ 大3倍！
```

## Kubernetes部署

```bash
# 部署
kubectl apply -f ../../k8s/silero-vad-service.yaml

# 查看状态
kubectl get pods -l app=silero-vad-service
kubectl get hpa silero-vad-hpa -w

# 查看日志
kubectl logs -l app=silero-vad-service -f
```

### HPA配置

- **minReplicas**: 2
- **maxReplicas**: 10
- **targetCPUUtilization**: 70%

## 故障排查

### 依赖冲突

```bash
# 检查当前依赖
cd micro_services/silero_vad
uv pip list

# 重新安装依赖
rm -rf .venv
uv sync

# 检查torch版本
uv run python -c "import torch; print(torch.__version__)"
```

### 服务启动失败

```bash
# 检查依赖是否完整
cd micro_services/silero_vad
uv run python -c "import torch, silero_vad, grpc; print('OK')"

# 查看日志
tail -f ../../logs/silero_vad.log
```

### Proto编译错误

```bash
# 重新编译proto
cd micro_services/silero_vad
./compile_proto.sh

# 检查生成的文件
ls -la vad_pb2*.py
```

## 开发建议

### 1. 虚拟环境隔离

每个服务使用uv创建独立的虚拟环境：

```bash
cd micro_services/silero_vad
uv sync  # 创建 .venv/
```

### 2. 依赖锁定

使用lock文件确保可重复构建：

```bash
cd micro_services/silero_vad
uv lock  # 生成 uv.lock
```

提交 `uv.lock` 到版本控制。

### 3. 测试

在服务目录内运行测试：

```bash
cd micro_services/silero_vad
uv run pytest tests/
```

## 参考文档

- [Silero VAD GitHub](https://github.com/snakers4/silero-vad)
- [微服务架构](../../ARCHITECTURE.md)
- [依赖管理](../DEPENDENCIES.md)

