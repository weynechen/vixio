# Vixio Python 包设计方案

## 概述

本文档描述将 vixio 重构为标准 Python 包的设计方案，采用 src-layout 结构，实现可选依赖系统，完善配置管理。

**目标**：
- 用户可以通过 `pip install vixio` 安装核心包
- 通过 `pip install vixio[xxx]` 按需安装可选组件
- 提供灵活的配置系统（代码/YAML/环境变量）

## 1. 项目结构 (src-layout)

采用 Python 打包最佳实践的 src-layout 结构：

```
vixio/
├── src/
│   └── vixio/
│       ├── __init__.py              # 主入口，版本号，延迟导入
│       ├── core/                     # 核心框架（始终可用）
│       │   ├── __init__.py
│       │   ├── chunk.py              # 数据载体
│       │   ├── pipeline.py           # 流水线
│       │   ├── station.py            # 工作站
│       │   ├── transport.py          # 传输接口
│       │   ├── session.py            # 会话管理
│       │   ├── control_bus.py        # 控制总线
│       │   ├── middleware.py         # 中间件
│       │   ├── output_controller.py  # 输出控制
│       │   ├── protocol.py           # 协议定义
│       │   └── tools/                # 工具系统
│       │       ├── __init__.py
│       │       ├── types.py
│       │       ├── converter.py
│       │       ├── device.py
│       │       └── local.py
│       │
│       ├── providers/                # Provider 接口和实现
│       │   ├── __init__.py           # 延迟导入，按需加载实现
│       │   ├── base.py               # 基类（始终可用）
│       │   ├── vad.py                # VAD 接口
│       │   ├── asr.py                # ASR 接口
│       │   ├── tts.py                # TTS 接口
│       │   ├── agent.py              # Agent 接口
│       │   ├── vision.py             # Vision 接口
│       │   ├── registry.py           # Provider 注册表
│       │   ├── factory.py            # Provider 工厂
│       │   │
│       │   ├── edge_tts/             # [可选] Edge TTS
│       │   │   ├── __init__.py
│       │   │   └── provider.py
│       │   ├── kokoro/               # [可选] Kokoro TTS gRPC 客户端
│       │   │   ├── __init__.py
│       │   │   └── grpc_provider.py
│       │   ├── sherpa_onnx_local/    # [可选] Sherpa ASR gRPC 客户端
│       │   │   ├── __init__.py
│       │   │   └── grpc_provider.py
│       │   ├── silero_vad/           # [可选] Silero VAD gRPC 客户端
│       │   │   ├── __init__.py
│       │   │   └── grpc_provider.py
│       │   ├── openai_agent/         # [可选] OpenAI Agent
│       │   │   ├── __init__.py
│       │   │   ├── provider.py
│       │   │   └── tools.py
│       │   └── vision_describers/    # [可选] 视觉描述器
│       │       ├── __init__.py
│       │       ├── base.py
│       │       ├── composite.py
│       │       └── openai_compatible.py
│       │
│       ├── stations/                 # Station 实现
│       │   ├── __init__.py
│       │   ├── vad.py
│       │   ├── asr.py
│       │   ├── tts.py
│       │   ├── agent.py
│       │   ├── vision.py
│       │   ├── filter.py
│       │   ├── splitter.py
│       │   ├── logger.py
│       │   ├── passthrough.py
│       │   ├── turn_detector.py
│       │   ├── text_aggregator.py
│       │   ├── sentence_aggregator.py
│       │   ├── transport_stations.py
│       │   └── middlewares/
│       │       └── ...
│       │
│       ├── transports/               # Transport 实现
│       │   ├── __init__.py
│       │   └── xiaozhi/              # [可选] 小志协议
│       │       ├── __init__.py
│       │       ├── transport.py
│       │       ├── ota_router.py
│       │       └── vision_router.py
│       │
│       ├── inference/                # gRPC 微服务（重型可选）
│       │   ├── __init__.py
│       │   ├── silero_vad/           # VAD 服务
│       │   │   ├── server.py
│       │   │   ├── client.py
│       │   │   └── pyproject.toml    # 独立依赖
│       │   ├── sherpa_onnx_local/    # ASR 服务
│       │   │   ├── server.py
│       │   │   ├── client.py
│       │   │   └── pyproject.toml
│       │   └── kokoro/               # TTS 服务
│       │       ├── server.py
│       │       ├── client.py
│       │       └── pyproject.toml
│       │
│       ├── config/                   # 配置系统
│       │   ├── __init__.py
│       │   ├── loader.py             # 配置加载器
│       │   ├── schema.py             # 配置 Schema
│       │   └── providers.yaml        # 默认配置
│       │
│       └── utils/                    # 工具函数
│           ├── __init__.py
│           ├── audio/
│           │   ├── __init__.py
│           │   └── converter.py
│           ├── auth.py
│           ├── logger_config.py
│           ├── latency_monitor.py
│           ├── network.py
│           ├── service_manager.py
│           └── text.py
│
├── tests/                            # 测试（不打包）
├── examples/                         # 示例（不打包）
├── docs/                             # 文档（不打包）
├── pyproject.toml                    # 包配置
├── README.md
└── uv.lock
```

### 主要改动

1. **目录移动**：将所有模块移动到 `src/vixio/` 下
2. **导入修改**：所有 import 从 `from providers.xxx` 改为 `from vixio.providers.xxx`
3. **配置更新**：pyproject.toml 的 package-dir 改为 `{"" = "src"}`

## 2. 依赖分层设计

### 核心依赖 (`pip install vixio`)

最小化核心依赖，只包含框架必需的包：

```toml
dependencies = [
    # Web 框架
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "websockets>=14.0,<15.0",
    "httpx>=0.28.0",
    "aiohttp>=3.13.0",
    "aiohttp-cors>=0.8.0",
    
    # 音频基础处理
    "numpy>=1.26.0,<1.27.0",
    "opuslib_next>=1.1.5",
    "pydub>=0.25.1",
    
    # 配置和日志
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "ruamel.yaml>=0.18.16",
    "loguru>=0.7.3",
    
    # gRPC 客户端（连接微服务）
    "grpcio>=1.76.0",
    "grpcio-tools>=1.76.0",
    
    # 工具
    "PyJWT>=2.10.0",
    "psutil>=7.0.0",
    "aioconsole>=0.8.2",
]
```

### 可选依赖 (`pip install vixio[xxx]`)

| Extra 名称 | 依赖包 | 说明 |
|-----------|--------|------|
| `openai-agent` | openai-agents[litellm], openai | OpenAI Agent Provider |
| `edge-tts` | edge-tts | Microsoft Edge TTS |
| `dashscope` | dashscope | 阿里云语音服务 |
| `langchain` | langchain, langchain-openai | LangChain 集成 |
| `langgraph` | langgraph | LangGraph 集成 |
| `mcp` | mcp, mcp-proxy | MCP 工具协议 |
| `silero-vad-local` | onnxruntime-gpu, silero-vad | 本地 VAD（支持 in-process 模式） |
| `sherpa-asr-local` | sherpa-onnx | 本地 ASR（支持 in-process 模式） |
| `kokoro-tts-local` | torch, kokoro, misaki[zh] | 本地 TTS（支持 in-process 模式） |
| `local-inference` | 以上三个的组合 | 所有本地推理 |
| `dev` | pytest, black, ruff, mypy... | 开发工具 |
| `all` | 除 local-inference 外的所有可选 | 完整安装（不含重型推理） |

### pyproject.toml 配置

```toml
[project.optional-dependencies]
# Agent 框架
openai-agent = [
    "openai-agents[litellm]>=0.4.2",
    "openai>=2.7.0",
]

# TTS providers
edge-tts = ["edge-tts>=7.2.3"]
dashscope = ["dashscope>=1.24.6"]

# MCP 工具协议
mcp = [
    "mcp>=1.20.0",
    "mcp-proxy>=0.10.0",
]

# Agent 框架集成
langchain = [
    "langchain>=0.1.0",
    "langchain-openai>=0.0.5",
]
langgraph = ["langgraph>=0.0.20"]

# 本地推理（支持 in-process 模式，开发零配置）
silero-vad-local = [
    "onnxruntime-gpu>=1.16.0",
    "silero-vad>=5.0",
]
sherpa-asr-local = ["sherpa-onnx==1.12.15"]
kokoro-tts-local = [
    "torch>=2.0.0",
    "kokoro>=0.8.1",
    "misaki[zh]>=0.8.1",
]
local-inference = [
    "vixio[silero-vad-local,sherpa-asr-local,kokoro-tts-local]",
]

# 开发工具
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "black>=23.7.0",
    "isort>=5.12.0",
    "mypy>=1.5.0",
    "ruff>=0.0.287",
]

# 完整安装（不含 local-inference）
all = [
    "vixio[openai-agent,edge-tts,dashscope,mcp,langchain,langgraph]",
]
```

## 3. 延迟导入机制

为避免导入时因缺少可选依赖而报错，实现延迟导入机制。

### 3.1 providers/__init__.py 示例

```python
"""
Provider interfaces and implementations

Provider 接口始终可用，实现类需要安装对应的可选依赖。
"""

# ============================================================
# Provider 接口（始终可用）
# ============================================================
from vixio.providers.base import BaseProvider
from vixio.providers.vad import VADProvider, VADEvent
from vixio.providers.asr import ASRProvider
from vixio.providers.tts import TTSProvider
from vixio.providers.agent import AgentProvider, Tool
from vixio.providers.vision import VisionProvider

# Registry and Factory（始终可用）
from vixio.providers.registry import ProviderRegistry, register_provider
from vixio.providers.factory import ProviderFactory

# ============================================================
# Provider 实现类（延迟导入）
# ============================================================

def get_edge_tts_provider():
    """
    Load EdgeTTSProvider class.
    
    Requires: pip install vixio[edge-tts]
    """
    try:
        from vixio.providers.edge_tts.provider import EdgeTTSProvider
        return EdgeTTSProvider
    except ImportError as e:
        raise ImportError(
            "EdgeTTSProvider requires edge-tts package. "
            "Install with: pip install vixio[edge-tts]"
        ) from e


def get_openai_agent_provider():
    """
    Load OpenAIAgentProvider class.
    
    Requires: pip install vixio[openai-agent]
    """
    try:
        from vixio.providers.openai_agent.provider import OpenAIAgentProvider
        return OpenAIAgentProvider
    except ImportError as e:
        raise ImportError(
            "OpenAIAgentProvider requires openai-agents package. "
            "Install with: pip install vixio[openai-agent]"
        ) from e


def get_silero_vad_provider():
    """
    Load LocalSileroVADProvider class (gRPC client).
    
    Note: This is the gRPC client, not the inference server.
    The inference server requires: pip install vixio[inference-vad]
    """
    from vixio.providers.silero_vad.grpc_provider import LocalSileroVADProvider
    return LocalSileroVADProvider


def get_sherpa_asr_provider():
    """
    Load LocalSherpaASRProvider class (gRPC client).
    
    Note: This is the gRPC client, not the inference server.
    The inference server requires: pip install vixio[inference-asr]
    """
    from vixio.providers.sherpa_onnx_local.grpc_provider import LocalSherpaASRProvider
    return LocalSherpaASRProvider


def get_kokoro_tts_provider():
    """
    Load LocalKokoroTTSProvider class (gRPC client).
    
    Note: This is the gRPC client, not the inference server.
    The inference server requires: pip install vixio[inference-tts]
    """
    from vixio.providers.kokoro.grpc_provider import LocalKokoroTTSProvider
    return LocalKokoroTTSProvider


# ============================================================
# 便捷导出（尝试导入，失败则跳过）
# ============================================================

__all__ = [
    # Interfaces
    "BaseProvider",
    "VADProvider",
    "VADEvent",
    "ASRProvider",
    "TTSProvider",
    "AgentProvider",
    "Tool",
    "VisionProvider",
    # Registry and Factory
    "ProviderRegistry",
    "register_provider",
    "ProviderFactory",
    # Lazy loaders
    "get_edge_tts_provider",
    "get_openai_agent_provider",
    "get_silero_vad_provider",
    "get_sherpa_asr_provider",
    "get_kokoro_tts_provider",
]

# 尝试直接导出常用 provider（可选依赖已安装时）
try:
    from vixio.providers.edge_tts.provider import EdgeTTSProvider
    __all__.append("EdgeTTSProvider")
except ImportError:
    pass

try:
    from vixio.providers.openai_agent.provider import OpenAIAgentProvider
    __all__.append("OpenAIAgentProvider")
except ImportError:
    pass
```

### 3.2 各 Provider 实现的内部处理

```python
# providers/edge_tts/provider.py

class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", ...):
        super().__init__(name="edge-tts")
        
        # 在实例化时检查依赖
        try:
            import edge_tts
            self.edge_tts = edge_tts
        except ImportError as e:
            raise ImportError(
                "EdgeTTSProvider requires edge-tts package. "
                "Install with: pip install vixio[edge-tts]"
            ) from e
```

### 3.3 本地推理 Provider 双模式设计

对于本地推理 Provider（VAD/ASR/TTS），采用 **双模式架构**，支持开发零配置和生产高性能。

#### 设计原理

```
┌─────────────────────────────────────────────────────────────┐
│                   SileroVADProvider                         │
├─────────────────────────────────────────────────────────────┤
│  mode="auto" (默认)                                          │
│    ├── 尝试连接 gRPC 服务 → 成功 → gRPC 模式                  │
│    └── 连接失败 → 检查本地依赖 → 有 → In-Process 模式         │
│                                 └── 无 → 报错提示安装         │
│                                                              │
│  mode="local"   → 强制 In-Process（开发/测试）               │
│  mode="grpc"    → 强制 gRPC（生产）                          │
└─────────────────────────────────────────────────────────────┘
```

#### 模式对比

| 模式 | 使用场景 | 优点 | 缺点 |
|------|---------|------|------|
| `local` | 开发、测试、单机部署 | 零配置、无需启动服务 | 占用主进程资源 |
| `grpc` | 生产、多实例、GPU 共享 | 资源隔离、可扩展 | 需要独立部署服务 |
| `auto` | 通用（默认） | 自动适配环境 | 首次连接有延迟 |

#### Provider 实现示例

```python
# providers/silero_vad/provider.py

from enum import Enum
from typing import Literal, Optional

class ServiceMode(str, Enum):
    AUTO = "auto"      # 自动选择：优先 gRPC，fallback 到 local
    LOCAL = "local"    # 强制本地 in-process
    GRPC = "grpc"      # 强制 gRPC 客户端


class SileroVADProvider(VADProvider):
    """
    Silero VAD Provider with dual-mode support.
    
    Modes:
    - auto (default): Try gRPC first, fallback to local if service unavailable
    - local: In-process inference (requires: pip install vixio[silero-vad-local])
    - grpc: Connect to gRPC service (requires: running vad-service)
    
    Examples:
        # Auto mode (recommended for development)
        vad = SileroVADProvider()
        
        # Force local mode
        vad = SileroVADProvider(mode="local")
        
        # Force gRPC mode (production)
        vad = SileroVADProvider(mode="grpc", service_url="vad-service:50051")
    """
    
    def __init__(
        self,
        mode: Literal["auto", "local", "grpc"] = "auto",
        service_url: str = "localhost:50051",
        threshold: float = 0.35,
        threshold_low: float = 0.15,
        frame_window_threshold: int = 8,
        **kwargs
    ):
        super().__init__(name="silero-vad")
        self.mode = ServiceMode(mode)
        self.service_url = service_url
        self.threshold = threshold
        self.threshold_low = threshold_low
        self.frame_window_threshold = frame_window_threshold
        
        self._backend = None  # LocalBackend or GrpcBackend
        self._actual_mode: Optional[ServiceMode] = None
    
    async def initialize(self) -> None:
        """Initialize with automatic mode selection"""
        
        if self.mode == ServiceMode.LOCAL:
            self._backend = await self._init_local()
            self._actual_mode = ServiceMode.LOCAL
            
        elif self.mode == ServiceMode.GRPC:
            self._backend = await self._init_grpc()
            self._actual_mode = ServiceMode.GRPC
            
        else:  # AUTO mode
            # Step 1: Try gRPC first
            try:
                self._backend = await self._init_grpc()
                self._actual_mode = ServiceMode.GRPC
                self.logger.info(
                    f"VAD using gRPC mode (connected to {self.service_url})"
                )
            except Exception as e:
                # Step 2: Fallback to local
                self.logger.debug(f"gRPC connection failed: {e}")
                self.logger.info("gRPC service unavailable, trying local mode...")
                
                self._backend = await self._init_local()
                self._actual_mode = ServiceMode.LOCAL
                self.logger.info("VAD using local in-process mode")
    
    async def _init_local(self):
        """Initialize local in-process backend"""
        try:
            from vixio.inference.silero_vad.local_backend import LocalSileroBackend
        except ImportError:
            raise ImportError(
                "Local VAD mode requires silero-vad package.\n"
                "Install with: pip install vixio[silero-vad-local]\n"
                "Or start a gRPC service: vixio serve vad"
            )
        
        backend = LocalSileroBackend(
            threshold=self.threshold,
            threshold_low=self.threshold_low,
            frame_window_threshold=self.frame_window_threshold,
        )
        await backend.initialize()
        return backend
    
    async def _init_grpc(self):
        """Initialize gRPC client backend"""
        from vixio.inference.silero_vad.grpc_backend import GrpcSileroBackend
        
        backend = GrpcSileroBackend(
            service_url=self.service_url,
            threshold=self.threshold,
            threshold_low=self.threshold_low,
            frame_window_threshold=self.frame_window_threshold,
        )
        await backend.connect()  # Raises exception if connection failed
        return backend
    
    async def detect(self, audio_data: bytes, event: VADEvent = VADEvent.CHUNK) -> bool:
        """Detect voice activity"""
        if self._backend is None:
            raise RuntimeError("Provider not initialized. Call initialize() first.")
        return await self._backend.detect(audio_data, event)
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self._backend:
            await self._backend.cleanup()
            self._backend = None
    
    def get_config(self) -> dict:
        """Get provider configuration"""
        return {
            "name": self.name,
            "mode": self.mode.value,
            "actual_mode": self._actual_mode.value if self._actual_mode else None,
            "service_url": self.service_url,
            "threshold": self.threshold,
        }
```

#### 使用示例

**开发环境（零配置）**：

```bash
# 安装本地推理依赖
pip install vixio[silero-vad-local]
```

```python
# app.py - 直接使用，无需启动任何服务
from vixio.providers import SileroVADProvider

async def main():
    # Auto mode: 自动使用 local 模式（因为没有 gRPC 服务）
    vad = SileroVADProvider()
    await vad.initialize()
    
    # 使用
    has_voice = await vad.detect(audio_data)
    print(f"Voice detected: {has_voice}")
    
    await vad.cleanup()
```

**生产环境（gRPC 服务）**：

```bash
# 终端 1: 启动 VAD 服务
vixio serve vad --port 50051

# 或使用 Docker
docker-compose up vad-service
```

```python
# app.py - 连接 gRPC 服务
from vixio.providers import SileroVADProvider

async def main():
    # 强制 gRPC 模式
    vad = SileroVADProvider(
        mode="grpc",
        service_url="vad-service:50051"
    )
    await vad.initialize()
    
    has_voice = await vad.detect(audio_data)
    await vad.cleanup()
```

#### CLI 命令（生产服务启动）

```bash
# 启动单个服务
vixio serve vad --port 50051
vixio serve asr --port 50052
vixio serve tts --port 50053

# 启动所有服务
vixio serve all

# 查看可用服务
vixio serve --list
```

#### inference 目录结构

```
src/vixio/inference/
├── __init__.py
├── silero_vad_local/
│   ├── __init__.py
│   ├── local_backend.py      # In-process 推理实现
│   ├── grpc_backend.py       # gRPC 客户端
│   ├── server.py             # gRPC 服务端（vixio serve vad）
│   ├── vad_pb2.py            # gRPC 生成文件
│   └── vad_pb2_grpc.py
├── sherpa_onnx_local/
│   ├── __init__.py
│   ├── local_backend.py
│   ├── grpc_backend.py
│   └── server.py
└── kokoro_cn_local/
    ├── __init__.py
    ├── local_backend.py
    ├── grpc_backend.py
    └── server.py
```

## 4. 配置系统设计

### 4.1 配置架构

采用 **双配置文件** 方案，结合 `.env` 和 `config.yaml` 的优势：

```
.env                    # 敏感信息 + 环境特定配置（不提交 git）
config.yaml             # 应用结构化配置（可提交 git）
```

#### 配置优先级（从高到低）

1. **代码直接配置** - 最高优先级，用于测试或特殊场景
2. **环境变量** (`.env` / `VIXIO_XXX`) - 敏感信息和环境差异
3. **配置文件** (`config.yaml`) - 应用结构化配置
4. **默认值** - 代码内置默认值

#### 职责划分

| 配置项 | `.env` 环境变量 | `config.yaml` |
|--------|----------------|---------------|
| API Keys | ✅ | ❌ |
| 密码/Token | ✅ | ❌ |
| 服务端点 URL | ✅ | ⚠️ 可作为默认值 |
| 日志级别 | ✅ | ✅ |
| Provider 类型 | ⚠️ 简单场景 | ✅ |
| Provider 详细配置 | ❌ | ✅ |
| Pipeline 配置 | ❌ | ✅ |

### 4.2 配置 Schema

```python
# config/schema.py

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class ProviderConfig(BaseModel):
    """单个 Provider 的配置"""
    type: str = Field(..., description="Provider type name")
    config: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific config")


class VADConfig(ProviderConfig):
    """VAD Provider 配置"""
    type: str = "silero-vad-grpc"
    config: Dict[str, Any] = Field(default_factory=lambda: {
        "service_url": "localhost:50051",
        "threshold": 0.35,
        "threshold_low": 0.15,
        "frame_window_threshold": 8,
    })


class ASRConfig(ProviderConfig):
    """ASR Provider 配置"""
    type: str = "sherpa-onnx-grpc"
    config: Dict[str, Any] = Field(default_factory=lambda: {
        "service_url": "localhost:50052",
    })


class TTSConfig(ProviderConfig):
    """TTS Provider 配置"""
    type: str = "edge-tts"
    config: Dict[str, Any] = Field(default_factory=lambda: {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
    })


class AgentConfig(ProviderConfig):
    """Agent Provider 配置"""
    type: str = "openai-agent"
    config: Dict[str, Any] = Field(default_factory=lambda: {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
    })


class VixioConfig(BaseModel):
    """Vixio 全局配置"""
    vad: VADConfig = Field(default_factory=VADConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    
    # 全局设置
    log_level: str = "INFO"
    log_file: Optional[str] = None
```

### 4.3 配置加载器

```python
# config/loader.py

from pathlib import Path
from typing import Optional, Union
from pydantic_settings import BaseSettings
from ruamel.yaml import YAML

from vixio.config.schema import VixioConfig


class VixioSettings(BaseSettings):
    """
    Environment-based settings with VIXIO_ prefix.
    
    Reads from:
    1. Environment variables (VIXIO_XXX)
    2. .env file in current directory
    
    Example:
        VIXIO_LOG_LEVEL=DEBUG
        VIXIO_VAD_SERVICE_URL=localhost:50051
    """
    
    # 配置文件路径
    config_file: Optional[str] = "config.yaml"
    
    # 日志配置
    log_level: str = "INFO"
    log_file: Optional[str] = None
    
    # 服务端点（敏感信息从环境变量读取）
    vad_service_url: str = "localhost:50051"
    asr_service_url: str = "localhost:50052"
    tts_service_url: str = "localhost:50053"
    
    # 默认 Provider
    default_vad: str = "silero-vad-grpc"
    default_asr: str = "sherpa-onnx-grpc"
    default_tts: str = "edge-tts"
    default_agent: str = "openai-agent"
    
    class Config:
        env_prefix = "VIXIO_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore unknown env vars


def load_config(
    config_path: Optional[Union[str, Path]] = None,
    env_settings: Optional[VixioSettings] = None,
) -> VixioConfig:
    """
    Load Vixio configuration.
    
    Configuration sources (priority high to low):
    1. Environment variables (.env + VIXIO_XXX)
    2. YAML config file (config.yaml)
    3. Default values
    
    Args:
        config_path: Path to YAML config file. If None, uses VIXIO_CONFIG_FILE
                     env var or defaults to "config.yaml"
        env_settings: Pre-loaded environment settings
        
    Returns:
        VixioConfig instance
    """
    # Step 1: Load environment variables (.env file auto-loaded)
    if env_settings is None:
        env_settings = VixioSettings()
    
    # Step 2: Determine config file path
    if config_path is None:
        config_path = env_settings.config_file
    
    # Step 3: Initialize with defaults
    config = VixioConfig()
    
    # Step 4: Load YAML config file (if exists)
    if config_path:
        yaml = YAML()
        path = Path(config_path)
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.load(f)
                if yaml_config:
                    config = VixioConfig(**yaml_config)
    
    # Step 5: Environment variables override YAML config
    config.log_level = env_settings.log_level
    if env_settings.log_file:
        config.log_file = env_settings.log_file
    
    # Override service endpoints from environment
    if config.vad.type == "silero-vad-grpc":
        config.vad.config["service_url"] = env_settings.vad_service_url
    if config.asr.type == "sherpa-onnx-grpc":
        config.asr.config["service_url"] = env_settings.asr_service_url
    if config.tts.type == "kokoro-tts-grpc":
        config.tts.config["service_url"] = env_settings.tts_service_url
    
    return config


# ============================================================
# Global config management
# ============================================================

_global_config: Optional[VixioConfig] = None


def get_config() -> VixioConfig:
    """
    Get global config (lazy load).
    
    Auto-discovers config from:
    1. .env file in current directory
    2. config.yaml in current directory (or VIXIO_CONFIG_FILE)
    """
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config


def set_config(config: VixioConfig) -> None:
    """Set global config manually"""
    global _global_config
    _global_config = config


def reload_config() -> VixioConfig:
    """Force reload config from files"""
    global _global_config
    _global_config = load_config()
    return _global_config
```

### 4.4 配置文件示例

#### `.env` 文件（敏感信息，不提交 git）

```bash
# .env

# ============================================================
# API Keys（敏感信息）
# ============================================================
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
DASHSCOPE_API_KEY=your-dashscope-api-key

# ============================================================
# 环境特定配置（dev/staging/prod 不同）
# ============================================================
VIXIO_LOG_LEVEL=INFO
VIXIO_LOG_FILE=logs/vixio.log

# 服务端点（开发环境用 localhost，生产环境用服务名）
VIXIO_VAD_SERVICE_URL=localhost:50051
VIXIO_ASR_SERVICE_URL=localhost:50052
VIXIO_TTS_SERVICE_URL=localhost:50053

# 可选：覆盖默认 Provider
# VIXIO_DEFAULT_TTS=edge-tts
# VIXIO_DEFAULT_AGENT=openai-agent
```

#### `config.yaml` 文件（应用配置，可提交 git）

```yaml
# config.yaml

# ============================================================
# 日志配置（可被环境变量覆盖）
# ============================================================
log_level: INFO
log_file: logs/vixio.log

# ============================================================
# VAD 配置
# ============================================================
vad:
  type: silero-vad-grpc
  config:
    # service_url 通常由环境变量 VIXIO_VAD_SERVICE_URL 覆盖
    service_url: localhost:50051
    threshold: 0.35
    threshold_low: 0.15
    frame_window_threshold: 8

# ============================================================
# ASR 配置
# ============================================================
asr:
  type: sherpa-onnx-grpc
  config:
    service_url: localhost:50052

# ============================================================
# TTS 配置
# ============================================================
tts:
  type: edge-tts
  config:
    voice: zh-CN-XiaoxiaoNeural
    rate: "+0%"
    volume: "+0%"

# ============================================================
# Agent 配置
# ============================================================
agent:
  type: openai-agent
  config:
    # model 和 api_key 等敏感信息从环境变量读取
    model: gpt-4o-mini
    temperature: 0.7
    system_prompt: "You are a helpful voice assistant."
```

### 4.5 Docker 部署配置

#### docker-compose.yml 示例

```yaml
version: '3.8'

services:
  vixio-app:
    image: vixio:latest
    ports:
      - "8000:8000"
    environment:
      # 敏感信息通过环境变量传入
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}
      
      # Vixio 配置
      - VIXIO_LOG_LEVEL=INFO
      - VIXIO_VAD_SERVICE_URL=vad-service:50051
      - VIXIO_ASR_SERVICE_URL=asr-service:50052
      - VIXIO_TTS_SERVICE_URL=tts-service:50053
    volumes:
      # 挂载配置文件（结构化配置）
      - ./config.yaml:/app/config.yaml:ro
      - ./logs:/app/logs
    depends_on:
      - vad-service
      - asr-service
      - tts-service

  vad-service:
    image: vixio-vad:latest
    ports:
      - "50051:50051"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  asr-service:
    image: vixio-asr:latest
    ports:
      - "50052:50052"
    volumes:
      - ./models:/app/models:ro

  tts-service:
    image: vixio-tts:latest
    ports:
      - "50053:50053"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

#### 配置加载顺序（Docker 环境）

```
1. 容器内环境变量（docker-compose environment）
     ↓ 覆盖
2. 挂载的 config.yaml 文件
     ↓ 覆盖
3. 代码默认值
```

#### Kubernetes ConfigMap + Secret 示例

```yaml
# configmap.yaml - 非敏感配置
apiVersion: v1
kind: ConfigMap
metadata:
  name: vixio-config
data:
  config.yaml: |
    log_level: INFO
    vad:
      type: silero-vad-grpc
      config:
        threshold: 0.35
    tts:
      type: edge-tts
      config:
        voice: zh-CN-XiaoxiaoNeural

---
# secret.yaml - 敏感配置
apiVersion: v1
kind: Secret
metadata:
  name: vixio-secrets
type: Opaque
stringData:
  OPENAI_API_KEY: "sk-your-api-key"

---
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vixio
spec:
  template:
    spec:
      containers:
        - name: vixio
          image: vixio:latest
          envFrom:
            - secretRef:
                name: vixio-secrets
          env:
            - name: VIXIO_VAD_SERVICE_URL
              value: "vad-service:50051"
            - name: VIXIO_ASR_SERVICE_URL
              value: "asr-service:50052"
          volumeMounts:
            - name: config
              mountPath: /app/config.yaml
              subPath: config.yaml
      volumes:
        - name: config
          configMap:
            name: vixio-config
```

### 4.6 多环境配置管理

支持多环境配置文件：

```
config/
├── config.yaml           # 基础配置（默认）
├── config.dev.yaml       # 开发环境覆盖
├── config.staging.yaml   # 测试环境覆盖
└── config.prod.yaml      # 生产环境覆盖
```

通过环境变量指定配置文件：

```bash
# .env
VIXIO_CONFIG_FILE=config/config.prod.yaml
```

```python
# 代码中
import os
from vixio.config import load_config

config_file = os.getenv("VIXIO_CONFIG_FILE", "config.yaml")
config = load_config(config_file)
```

## 5. 使用示例

### 5.1 最小安装

```bash
# 安装核心包
pip install vixio

# 按需安装可选组件
pip install vixio[edge-tts]        # Edge TTS
pip install vixio[openai-agent]   # OpenAI Agent
```

### 5.2 代码配置方式

```python
import vixio
from vixio.core import Pipeline, AudioChunk
from vixio.stations import VADStation, ASRStation, TTSStation, AgentStation

# 获取 Provider 类（延迟加载）
from vixio.providers import get_edge_tts_provider, get_openai_agent_provider

EdgeTTSProvider = get_edge_tts_provider()
OpenAIAgentProvider = get_openai_agent_provider()

# 创建 Pipeline
pipeline = Pipeline()

# 添加 Stations（使用代码配置 Provider）
pipeline.add_station(TTSStation(
    provider=EdgeTTSProvider(
        voice="zh-CN-XiaoxiaoNeural",
        rate="+10%"
    )
))

pipeline.add_station(AgentStation(
    provider=OpenAIAgentProvider(
        model="gpt-4o-mini",
        temperature=0.7
    )
))

# 运行
await pipeline.start()
```

### 5.3 配置文件方式

```python
import vixio
from vixio.config import load_config
from vixio.providers import ProviderFactory

# 加载配置
config = load_config("config.yaml")

# 使用 Factory 创建 Provider
factory = ProviderFactory()
tts_provider = factory.create(config.tts.type, **config.tts.config)
agent_provider = factory.create(config.agent.type, **config.agent.config)

# 创建 Pipeline
pipeline = vixio.Pipeline()
pipeline.add_station(vixio.stations.TTSStation(provider=tts_provider))
pipeline.add_station(vixio.stations.AgentStation(provider=agent_provider))
```

### 5.4 环境变量 + 配置文件方式（推荐）

```bash
# .env 文件 - 敏感信息和环境特定配置
OPENAI_API_KEY=sk-your-key
VIXIO_LOG_LEVEL=DEBUG
VIXIO_VAD_SERVICE_URL=vad-service:50051
```

```yaml
# config.yaml - 应用结构化配置
tts:
  type: edge-tts
  config:
    voice: zh-CN-XiaoxiaoNeural
```

```python
import vixio
from vixio.config import load_config, get_config

# 方式 1: 加载指定配置文件（.env 自动读取）
config = load_config("config.yaml")

# 方式 2: 使用全局配置（自动发现配置文件）
config = get_config()

print(config.log_level)  # DEBUG（来自 .env）
print(config.tts.config["voice"])  # zh-CN-XiaoxiaoNeural（来自 config.yaml）
```

### 5.5 Docker 环境使用

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f vixio-app
```

```python
# 代码中无需修改，配置自动从环境变量和挂载的配置文件读取
import vixio
from vixio.config import get_config

config = get_config()
# VIXIO_VAD_SERVICE_URL=vad-service:50051 自动生效
```

## 6. 实施步骤

### Phase 1: 结构重组

1. 创建 `src/vixio/` 目录
2. 移动现有模块到新位置
3. 更新 pyproject.toml 的 package-dir 配置

### Phase 2: 导入修复

1. 全局替换导入路径
   - `from providers.` → `from vixio.providers.`
   - `from core.` → `from vixio.core.`
   - 以此类推
2. 更新所有 `__init__.py` 文件

### Phase 3: 延迟导入

1. 实现 `providers/__init__.py` 的延迟导入
2. 实现 `transports/__init__.py` 的延迟导入
3. 添加依赖检查和友好错误提示

### Phase 4: 配置系统

1. 实现 `config/schema.py`
2. 实现 `config/loader.py`
3. 更新 `config/__init__.py` 导出

### Phase 5: 依赖调整

1. 更新 pyproject.toml 的可选依赖
2. 移动重型依赖到 extras

### Phase 6: 验证测试

1. 测试最小安装: `pip install .`
2. 测试可选安装: `pip install ".[edge-tts,openai-agent]"`
3. 测试开发安装: `pip install -e ".[all,dev]"`
4. 运行现有测试用例

## 7. 注意事项

### 7.1 inference 目录

`inference/` 目录包含重型推理服务（VAD/ASR/TTS 的实际推理代码），这些服务：

- **应该单独部署**（Docker/K8s）
- **vixio 包只包含 gRPC 客户端**（providers 下的 grpc_provider.py）
- 安装 `vixio[inference-xxx]` 只是为了**本地开发/测试**时运行服务

### 7.2 开发环境

```bash
# 开发时安装所有依赖
uv pip install -e ".[all,dev]"

# 运行测试
pytest tests/

# 代码格式化
black src/
ruff check src/
```

### 7.3 发布流程

```bash
# 构建
python -m build

# 上传到 PyPI
twine upload dist/*
```

