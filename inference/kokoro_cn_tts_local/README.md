# Kokoro TTS Microservice

基于Kokoro v1.1中文模型的gRPC文本转语音服务。

## 功能特性

- **高质量中文TTS**: 使用Kokoro v1.1模型，支持自然流畅的中文语音合成
- **流式输出**: 支持实时流式音频生成，降低延迟
- **多音色**: 支持4种音色（2女2男）
- **会话隔离**: 每个会话独立状态，支持并发请求
- **gRPC接口**: 高性能二进制协议

## 目录结构

```
kokoro/
├── tts.proto           # Proto定义
├── tts_pb2.py          # 生成的代码
├── tts_pb2_grpc.py     # 生成的gRPC代码
├── compile_proto.sh    # Proto编译脚本
├── kokoro.py           # Kokoro模型封装
├── server.py           # gRPC服务端
├── client.py           # gRPC客户端
├── Dockerfile          # Docker镜像
├── __init__.py
└── README.md           # 本文档
```

## 依赖安装

```bash
# 安装Kokoro TTS库
uv add "kokoro>=0.8.1" "misaki[zh]>=0.8.1"

# 或使用pip
pip install "kokoro>=0.8.1" "misaki[zh]>=0.8.1"
```

## 使用方法

### 1. 编译Proto

```bash
cd /path/to/vixio/inference/kokoro
./compile_proto.sh
```

### 2. 启动服务

```bash
# 开发模式
uv run python server.py --port 50053 --log-level INFO

# 指定模型
uv run python server.py --port 50053 --repo-id hexgrad/Kokoro-82M-v1.1-zh
```

### 3. 测试服务

```bash
# 在另一个终端
cd /path/to/vixio
uv run python tests/test_kokoro_simple.py
```

## 可用音色

| ID | 名称 | 性别 | 语言 |
|----|------|------|------|
| zf_001 | Female Voice 001 | 女 | 中文 |
| zf_002 | Female Voice 002 | 女 | 中文 |
| zm_001 | Male Voice 001 | 男 | 中文 |
| zm_002 | Male Voice 002 | 男 | 中文 |

## API接口

### CreateSession

创建TTS会话。

```protobuf
message CreateSessionRequest {
  string session_id = 1;
  string voice = 2;          // "zf_001", "zf_002", "zm_001", "zm_002"
  float speed = 3;           // 语速倍数 (默认: 1.0)
  string lang = 4;           // 语言代码 (默认: "zh")
  int32 sample_rate = 5;     // 采样率 (默认: 24000)
}
```

### Synthesize

合成语音（流式输出）。

```protobuf
message SynthesizeRequest {
  string session_id = 1;
  string text = 2;           // 要合成的文本
  bool join_sentences = 3;   // 是否合并句子 (默认: true)
}

message SynthesizeResponse {
  bytes audio_data = 1;      // Float32 PCM音频数据
  int32 sample_rate = 2;     // 采样率
  bool is_final = 3;         // 是否最后一个chunk
  string session_id = 4;
}
```

## Provider使用

### 配置

在 `config/providers.yaml` 中配置：

```yaml
dev:
  providers:
    tts:
      provider: kokoro-tts-grpc
      config:
        service_url: "localhost:50053"
        voice: "zf_001"
        speed: 1.0
        lang: "zh"
        sample_rate: 24000
```

### 代码示例

```python
from providers.factory import ProviderFactory

# 加载provider
providers = ProviderFactory.create_from_config_file(
    "config/providers.yaml", 
    env="dev"
)
tts_provider = providers['tts']

# 初始化
await tts_provider.initialize()

# 合成语音
sample_rate, audio_data = await tts_provider.synthesize("你好，世界！")

# 流式合成
async for sample_rate, audio_chunk in tts_provider.stream_synthesize("你好，世界！"):
    # 处理音频块
    pass

# 清理
await tts_provider.cleanup()
```

## 性能

### 资源使用

- **内存**: ~500MB（模型加载后）
- **CPU**: 中等（推理时）
- **GPU**: 可选（支持CUDA加速）

### 延迟

- **首chunk延迟**: ~200-500ms
- **流式间隔**: ~50-100ms per chunk
- **总合成时间**: 取决于文本长度

### 并发能力

- **单副本**: 支持10-20并发会话
- **K8s多副本**: 支持100+并发会话

## Docker部署

```bash
# 构建镜像
docker build -t vixio-kokoro-tts -f inference/kokoro_cn_tts_local/Dockerfile .

# 运行容器
docker run -p 50053:50053 vixio-kokoro-tts
```

## Kubernetes部署

```bash
# 部署到K8s
kubectl apply -f k8s/kokoro-tts-service.yaml

# 查看状态
kubectl get pods -l app=kokoro-tts-service
```

## 故障排查

### 服务启动失败

```bash
# 检查依赖
uv run python -c "import kokoro; print('OK')"

# 检查模型下载
ls ~/.cache/huggingface/hub/
```

### 音频质量问题

- 检查采样率设置（推荐24000Hz）
- 调整语速参数（speed: 0.8-1.2）
- 尝试不同音色

### 内存不足

- 使用更小的模型
- 限制并发会话数
- 启用模型量化

## 参考文档

- [Kokoro TTS GitHub](https://github.com/hexgrad/kokoro)
- [架构文档](../../ARCHITECTURE.md)
- [Provider开发指南](../../providers/README.md)

