# Providers 架构说明

## Provider 注册机制

所有 Provider 必须使用 `@register_provider` 装饰器注册，注册名称即为 Provider 的唯一标识符。

### 统一的命名规则

```python
from providers.registry import register_provider

@register_provider("silero-vad-grpc")  # ← 注册名称（唯一标识符）
class LocalSileroVADProvider(VADProvider):
    def __init__(self, service_url: str, threshold: float = 0.5):
        # name 自动使用注册名称，无需手动传入
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        ...
```

### 配置文件使用

在 `config/providers.yaml` 中使用注册名称：

```yaml
dev:
  providers:
    vad:
      provider: silero-vad-grpc  # ← 使用注册名称
      config:
        service_url: "localhost:50051"
        threshold: 0.5
    
    tts:
      provider: edge-tts  # ← 使用注册名称
      config:
        voice: "zh-CN-XiaoxiaoNeural"
    
    agent:
      provider: openai-agent  # ← 使用注册名称
      config:
        api_key: ${OPENAI_API_KEY}
        model: "gpt-4"
```

### 创建 Provider 实例

```python
from providers.factory import ProviderFactory

# 方式1：从配置创建
provider = ProviderFactory.create("silero-vad-grpc", {
    "service_url": "localhost:50051",
    "threshold": 0.5
})

# provider.name == "silero-vad-grpc" ✅

# 方式2：从配置文件创建
providers = ProviderFactory.create_from_config_file(
    "config/providers.yaml",
    env="dev"
)
# providers["vad"].name == "silero-vad-grpc" ✅
```

## 已注册的 Providers

### Local Providers (gRPC Clients)

本地部署的微服务，通过 gRPC 连接：

| 注册名称 | Class | 类型 | 微服务地址 |
|---------|-------|------|-----------|
| `silero-vad-grpc` | `LocalSileroVADProvider` | VAD | `localhost:50051` (dev) |
| `kokoro-tts-grpc` | `LocalKokoroTTSProvider` | TTS | `localhost:50053` (dev) |

**特点**：
- `is_local = True`
- 需要启动对应的微服务
- 重依赖在微服务中（torch, models）

### Remote Providers (API Clients)

第三方云服务，通过 REST API 连接：

| 注册名称 | Class | 类型 | 服务商 |
|---------|-------|------|-------|
| `edge-tts` | `EdgeTTSProvider` | TTS | Microsoft Edge |
| `openai-agent` | `OpenAIAgentProvider` | Agent | OpenAI/Compatible |

**特点**：
- `is_local = False`
- 无需启动本地服务
- 无重依赖（只需 HTTP 客户端）

## 设计原则

### 1. 唯一的名称来源

❌ **旧设计（混淆）**：

```python
@register_provider("silero-vad-grpc")  # 注册名称
class LocalSileroVADProvider(VADProvider):
    def __init__(self, ..., name: str = "SileroVAD-gRPC"):  # 实例名称（不一致！）
        super().__init__(name=name)
```

配置文件：`provider: silero-vad-grpc`  
实例日志：`SileroVAD-gRPC initialized`  
→ 两个名称不一致，容易混淆

✅ **新设计（统一）**：

```python
@register_provider("silero-vad-grpc")  # 唯一标识符
class LocalSileroVADProvider(VADProvider):
    def __init__(self, ...):  # 无 name 参数
        # 自动使用注册名称
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
```

配置文件：`provider: silero-vad-grpc`  
实例日志：`silero-vad-grpc initialized`  
→ 完全一致 ✅

### 2. 强制注册

所有 Provider 必须使用 `@register_provider` 装饰器：

```python
# ❌ 错误：没有注册
class MyProvider(VADProvider):
    pass

# ✅ 正确：已注册
@register_provider("my-provider")
class MyProvider(VADProvider):
    pass
```

### 3. 构造函数简化

移除不必要的 `name` 参数：

```python
# ❌ 旧版：允许自定义 name（但从不需要）
def __init__(self, service_url: str, name: str = "DefaultName"):
    super().__init__(name=name)

# ✅ 新版：自动使用注册名称
def __init__(self, service_url: str):
    name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
    super().__init__(name=name)
```

## 目录结构

```
providers/
├── base.py                 # BaseProvider 接口
├── vad.py                  # VADProvider 接口
├── asr.py                  # ASRProvider 接口
├── tts.py                  # TTSProvider 接口
├── agent.py                # AgentProvider 接口
├── registry.py             # ProviderRegistry + @register_provider
├── factory.py              # ProviderFactory
├── __init__.py             # 导入所有providers（触发注册）
│
├── silero_vad/             # Silero VAD Provider
│   ├── __init__.py
│   └── grpc_provider.py    # @register_provider("silero-vad-grpc")
│
├── kokoro/                 # Kokoro TTS Provider
│   ├── __init__.py
│   └── grpc_provider.py    # @register_provider("kokoro-tts-grpc")
│
├── edge_tts/               # Edge TTS Provider
│   ├── __init__.py
│   └── provider.py         # @register_provider("edge-tts")
│
└── openai_agent/           # OpenAI Agent Provider
    ├── __init__.py
    └── provider.py         # @register_provider("openai-agent")
```

## 添加新 Provider

### 1. 创建 Provider 类

```python
# providers/my_service/grpc_provider.py
from providers.vad import VADProvider, VADEvent
from providers.registry import register_provider

@register_provider("my-service-vad")
class MyServiceVADProvider(VADProvider):
    def __init__(self, service_url: str, threshold: float = 0.5):
        # 自动使用注册名称
        name = getattr(self.__class__, '_registered_name', self.__class__.__name__)
        super().__init__(name=name)
        
        self.service_url = service_url
        self.threshold = threshold
    
    async def detect(self, audio_data: bytes, event: VADEvent) -> bool:
        # 实现检测逻辑
        ...
```

### 2. 在 `providers/__init__.py` 中导入

```python
# providers/__init__.py
from providers.my_service.grpc_provider import MyServiceVADProvider
```

导入即触发注册！

### 3. 在配置文件中使用

```yaml
# config/providers.yaml
dev:
  providers:
    vad:
      provider: my-service-vad  # 使用注册名称
      config:
        service_url: "localhost:50054"
        threshold: 0.6
```

### 4. 创建实例

```python
provider = ProviderFactory.create("my-service-vad", {
    "service_url": "localhost:50054",
    "threshold": 0.6
})

print(provider.name)  # "my-service-vad"
```

## 查看已注册 Providers

```python
from providers.registry import ProviderRegistry

# 查看所有已注册的providers
all_providers = ProviderRegistry.list_providers()
print(all_providers.keys())
# dict_keys(['silero-vad-grpc', 'kokoro-tts-grpc', 'edge-tts', 'openai-agent'])

# 查看特定类别的providers
vad_providers = ProviderRegistry.list_providers(category="vad")
tts_providers = ProviderRegistry.list_providers(category="tts")

# 检查是否已注册
if ProviderRegistry.is_registered("silero-vad-grpc"):
    print("Silero VAD is registered!")
```

## 总结

### ✅ 新设计优势

1. **唯一标识符** - `@register_provider("name")` 即是配置文件中的名称，也是实例的 `name`
2. **强制注册** - 所有 Provider 必须注册才能使用
3. **简化构造** - 移除不必要的 `name` 参数
4. **统一风格** - 所有 Provider 遵循相同的注册和命名规则
5. **易于维护** - 配置、日志、代码中的名称完全一致

### 🚫 避免的错误

1. ❌ 不要在构造函数中添加 `name` 参数
2. ❌ 不要忘记 `@register_provider` 装饰器
3. ❌ 注册名称不要与实例名称不一致
4. ❌ 不要直接实例化（使用 `ProviderFactory`）

