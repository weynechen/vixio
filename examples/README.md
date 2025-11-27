# Vixio Examples

演示如何使用 Vixio 框架构建语音对话应用。

## 📁 文件说明

### `agent_chat.py` - 完整的 AI 语音助手

支持三种部署模式的完整语音对话服务器：

**Pipeline 流程**：
```
Audio Input → VAD → TurnDetector → ASR → TextAggregator → Agent → SentenceSplitter → TTS → Audio Output
```

**特性**：
- ✅ 支持 dev/docker/k8s 三种部署模式
- ✅ 从配置文件加载 Providers
- ✅ 微服务架构（VAD/ASR/TTS 通过 gRPC）
- ✅ 每个会话独立的 Provider 实例
- ✅ 自动清理资源

## 🚀 使用方法

### 前置要求

1. **设置环境变量**：

```bash
# .env 文件
API_KEY=your-api-key-here
LITELLM_MODEL=deepseek/deepseek-chat
BASE_URL=https://api.deepseek.com  # 可选
```

2. **安装主项目依赖**：

```bash
cd /path/to/vixio
uv sync
```

### 开发模式（Dev）

使用本地 gRPC 微服务。

#### 1. 安装微服务依赖

```bash
# 一键安装所有微服务依赖
./scripts/dev/setup-services.sh
```

#### 2. 启动微服务

```bash
# 启动所有微服务（VAD, ASR, TTS）
./scripts/dev/start-all.sh
```

这会启动：
- **Silero VAD**: `localhost:50051`
- **Sherpa ONNX ASR**: `localhost:50052` (TODO)
- **Kokoro TTS**: `localhost:50053` (可选)

#### 3. 运行示例

```bash
# 使用开发配置
uv run python examples/agent_chat.py --env dev
```

#### 4. 停止服务

```bash
./scripts/dev/stop-all.sh
```

### Docker 模式

使用 Docker Compose 部署。

#### 1. 启动服务

```bash
# 构建并启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

#### 2. 运行示例

主服务也在 Docker 中运行：

```bash
docker-compose exec main python examples/agent_chat.py --env docker
```

或者在宿主机运行（连接 Docker 中的微服务）：

```bash
# 需要修改配置文件中的 service_url 为 localhost:50051
uv run python examples/agent_chat.py --env docker
```

#### 3. 停止服务

```bash
docker-compose down
```

### Kubernetes 模式

使用 K8s 部署，支持水平扩展（HPA）。

#### 1. 部署服务

```bash
# 部署所有微服务
kubectl apply -f k8s/

# 查看状态
kubectl get pods
kubectl get hpa
```

#### 2. 运行示例

在 K8s 集群内运行：

```bash
kubectl exec -it <main-pod> -- python examples/agent_chat.py --env k8s
```

#### 3. 查看日志

```bash
# VAD 服务日志
kubectl logs -l app=silero-vad-service -f

# 主服务日志
kubectl logs -l app=vixio-main -f
```

## 📝 配置文件

配置文件位于 `config/providers.yaml`，定义了三种部署模式的 Provider 配置：

```yaml
dev:
  providers:
    vad:
      provider: silero-vad-grpc
      config:
        service_url: "localhost:50051"
        threshold: 0.5
    
    agent:
      provider: openai-agent
      config:
        api_key: ${API_KEY}
        model: ${LITELLM_MODEL:deepseek/deepseek-chat}
    
    tts:
      provider: edge-tts
      config:
        voice: "zh-CN-XiaoxiaoNeural"
```

### 环境变量替换

配置文件支持环境变量替换：

- `${VAR_NAME}` - 必需的环境变量
- `${VAR_NAME:default_value}` - 可选的环境变量（带默认值）

## 🔧 自定义配置

### 使用自定义配置文件

```bash
uv run python examples/agent_chat.py --env dev --config my_config.yaml
```

### 修改 Provider

编辑 `config/providers.yaml`：

```yaml
dev:
  providers:
    # 切换 TTS 为 Kokoro（本地 gRPC）
    tts:
      provider: kokoro-tts-grpc
      config:
        service_url: "localhost:50053"
        voice: "zf_001"
        speed: 1.0
```

### 添加新 Provider

1. 实现 Provider 类（继承 `BaseProvider`）
2. 使用 `@register_provider` 装饰器注册
3. 在配置文件中添加配置
4. 无需修改示例代码！

## 📊 监控和调试

### 查看服务状态

```bash
# 开发模式
curl http://localhost:8000/health
curl http://localhost:8000/connections

# 查看微服务日志
tail -f logs/silero_vad.log
tail -f logs/kokoro_tts.log
```

### 调试日志

修改日志级别：

```python
from utils import configure_logger

# 在导入其他模块之前调用
configure_logger(level="DEBUG", log_dir="my_logs")
```

## 🐛 故障排查

### VAD 服务连接失败

```
ERROR: Failed to connect to VAD service at localhost:50051
```

**解决方案**：

1. 检查 VAD 服务是否运行：
   ```bash
   ps aux | grep "silero_vad/server.py"
   ```

2. 重启微服务：
   ```bash
   ./scripts/dev/stop-all.sh
   ./scripts/dev/start-all.sh
   ```

3. 查看日志：
   ```bash
   tail -f logs/silero_vad.log
   ```

### API_KEY 未设置

```
ERROR: API_KEY environment variable not set!
```

**解决方案**：

```bash
# 方式1：在 .env 文件中设置
echo "API_KEY=your-key-here" >> .env

# 方式2：导出环境变量
export API_KEY=your-key-here
```

### 依赖缺失

```
ModuleNotFoundError: No module named 'xxx'
```

**解决方案**：

```bash
# 主项目依赖
cd /path/to/vixio
uv sync

# 微服务依赖
./scripts/dev/setup-services.sh
```

## 📚 相关文档

- [微服务架构](../micro_services/README.md) - 微服务设计和实现
- [Provider 系统](../providers/README.md) - Provider 注册和使用
- [独立依赖管理](../micro_services/DEPENDENCIES.md) - 依赖隔离说明

## 💡 最佳实践

### 1. 开发流程

```bash
# 1. 修改代码
vim providers/my_provider.py

# 2. 重启相关微服务
./scripts/dev/stop-all.sh
./scripts/dev/start-all.sh

# 3. 运行示例测试
uv run python examples/agent_chat.py --env dev
```

### 2. 性能测试

```bash
# 并发测试
cd tests
uv run python test_xiaozhi_concurrent.py --sessions 20
```

### 3. 生产部署

1. 使用 K8s 模式
2. 配置 HPA 自动扩展
3. 设置资源限制
4. 启用监控和日志收集

```bash
kubectl apply -f k8s/
kubectl get hpa -w
```

## 🎯 下一步

1. **实现 ASR 微服务** - 完成 Sherpa ONNX ASR 的 gRPC 实现
2. **添加更多 Providers** - TTS、ASR 的其他实现
3. **优化性能** - 降低延迟，提高吞吐量
4. **添加测试** - 单元测试和集成测试

