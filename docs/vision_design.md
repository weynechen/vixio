# Vision 接入设计文档

## 1. 概述

本文档定义了 Vixio 框架中视觉（Vision）能力的接入设计，包括数据流、处理策略、接口定义等。

### 1.1 设计目标

1. **语音驱动**：视觉是语音交互的增强，不是独立的数据流
2. **不强制 VLM**：支持普通 LLM + 视觉预处理的组合
3. **Pipeline 一致性**：视觉数据通过 `Chunk.metadata` 传递，不旁路 Pipeline
4. **传输层解耦**：Transport 只负责数据采集，不关心视觉处理逻辑

### 1.2 核心场景

**Video Upstream（上行）**：用户语音询问时，Agent 从设备获取图像并解析。

```
用户："这个字怎么读？"
      ↓
设备发送：当前画面 + 语音
      ↓
服务器：理解图像 + 回答问题
```

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          设备端 (Device)                                     │
│                                                                             │
│   ┌─────────────┐     ┌─────────────┐     ┌──────────────┐                 │
│   │   麦克风     │ ──→ │  轻量VAD    │ ──→ │  发送策略     │                 │
│   └─────────────┘     └──────┬──────┘     └──────┬───────┘                 │
│                              │                   │                          │
│   ┌─────────────┐            │                   │                          │
│   │   摄像头     │ ──────────┼───────────────────┤                          │
│   └─────────────┘            │                   │                          │
│                              ↓                   ↓                          │
│                        VAD_START?          定期心跳?                        │
│                              │                   │                          │
│                              ↓                   ↓                          │
│                        发送当前帧          发送低频帧                        │
│                        + 音频流           (如每3秒1帧)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼ WebSocket / WebRTC
┌─────────────────────────────────────────────────────────────────────────────┐
│                          服务器端 (Server)                                   │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         Transport Layer                                │  │
│  │  audio_queue ←── 音频流                                                │  │
│  │  video_queue ←── 图像帧 (VAD触发 + 心跳)                               │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                               │                                             │
│                               ▼                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         Pipeline Layer                                 │  │
│  │                                                                        │  │
│  │  InputStation → VAD → ASR → TextAggregator → AgentStation → TTS       │  │
│  │       │                           │               │                    │  │
│  │       │                           │               ▼                    │  │
│  │       │                           │        VisionStrategy              │  │
│  │       │                           │         ┌────┴────┐                │  │
│  │       │                           │         │         │                │  │
│  │       └── visual_context ─────────┴────────→│  单模态  │  多模态       │  │
│  │           via metadata                      │ Describe │ Passthrough   │  │
│  │                                             └────┬────┘                │  │
│  │                                                  ▼                     │  │
│  │                                               Agent                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
时间轴 ──────────────────────────────────────────────────────────────────────→

设备端:    │ VAD_START                              │ VAD_END
           │    ↓                                   │
           │ 发送 image(trigger=vad_start)          │
           │ 发送 audio stream                      │
           │                                        │

服务器端:  │                                        │
           │ 收到 image → 保存为 speech_context     │
           │ 收到 audio → audio_queue               │ VAD_END
           │                                        │    ↓
           │                                        │ ASR 处理
           │                                        │    ↓
           │                                        │ TextAggregator
           │                                        │ Chunk(TEXT, metadata={visual_context})
           │                                        │    ↓
           │                                        │ AgentStation
           │                                        │ VisionStrategy.process(text, images)
           │                                        │    ↓
           │                                        │ Agent.chat()
```

---

## 3. 视觉处理策略

框架提供两种视觉处理策略，通过配置选择：

### 3.1 单模态策略 (DescribeStrategy)

适用于**普通 LLM**，通过 VLM/YOLO/OCR 等模型预处理图像。

```
图像 + 用户问题 → VisionDescriber → 针对性描述
                                        ↓
                   用户问题 + 描述 → LLM → 回答
```

**关键设计**：Describer 需要知道用户问题，才能给出针对性描述。

```python
# 示例：用户问"这是什么字？"

VLM Prompt:
"用户正在看这张图片并提问。
 用户问题：这是什么字？
 请描述图像中与此问题相关的内容。"

VLM 回答:
"图像中央有一个大字'龙'，采用楷书字体。"

发送给 LLM:
"用户问题：这是什么字？

视觉上下文：
[图像 1: 图像中央有一个大字'龙'，采用楷书字体。]"
```

### 3.2 多模态策略 (PassthroughStrategy)

适用于 **VLM**（GPT-4o、GLM-4V、Gemini 等），直接传递图像。

```
图像 + 用户问题 → VLM → 回答
```

### 3.3 策略对比

| 策略 | 适用模型 | 优点 | 缺点 |
|------|---------|------|------|
| **单模态** | 普通 LLM | 兼容性好、成本可控 | 信息可能损失、延迟增加 |
| **多模态** | VLM | 信息完整、延迟低 | 需要 VLM、成本高 |

---

## 4. 设备端发送时序

### 4.1 推荐方案：设备端 VAD 触发 + 心跳

**设备端本地 VAD 检测到说话时，立即发送当前帧 + 音频流。**

**优点**：
- 图像和语音**时间对齐**：用户说"这个"时的画面
- 无额外 RTT 延迟
- 按需发送，节省带宽

**设备要求**：
- 需要轻量级 VAD（WebRTC VAD、Silero 等）
- 现代 MCU (ESP32-S3) 可支持

### 4.2 协议设计

```python
# 设备发送的消息类型

# 1. 音频帧（持续发送）
{
    "type": "audio",
    "data": "<opus/pcm bytes>"
}

# 2. 图像帧（VAD 触发 或 心跳）
{
    "type": "image",
    "data": "<jpeg bytes>",
    "trigger": "vad_start" | "heartbeat",  # 触发原因
    "timestamp": 1701234567890,
    "width": 640,
    "height": 480,
    "format": "jpeg"
}
```

### 4.3 降级方案

如果设备不支持本地 VAD：
- 退化为**低频心跳**模式（如每 3 秒发送一帧）
- 服务器使用**最近收到的帧**作为 visual_context
- 体验略差但可用

---

## 5. 接口定义

### 5.1 数据结构

```python
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum


@dataclass
class ImageContent:
    """
    图像内容 - 视觉上下文的载体
    
    设计原则：框架无关的通用表示
    """
    image: bytes | str  # base64 字符串、URL 或原始字节
    mime_type: str = "image/jpeg"
    
    # 元数据
    source: str = ""            # "camera", "screenshot", "upload"
    trigger: str = ""           # "vad_start", "heartbeat", "request"
    capture_time: Optional[float] = None
    width: int = 0
    height: int = 0


@dataclass  
class MultimodalMessage:
    """
    多模态消息 - Agent 的统一输入格式
    
    无论使用哪种策略，Agent 最终收到的都是这个格式
    """
    text: str                                    # 文字内容（可能包含图像描述）
    images: Optional[List[ImageContent]] = None  # 原始图像（仅多模态策略使用）
    
    @property
    def has_images(self) -> bool:
        return self.images is not None and len(self.images) > 0
```

### 5.2 视觉处理策略接口

```python
from abc import ABC, abstractmethod


class VisionStrategy(ABC):
    """
    视觉处理策略基类
    
    框架层组件，负责将 (文字, 图像) 转换为 Agent 可处理的格式
    """
    
    @abstractmethod
    async def process(
        self, 
        text: str, 
        images: Optional[List[ImageContent]]
    ) -> MultimodalMessage:
        """
        处理视觉输入
        
        Args:
            text: 用户文字输入（来自 ASR）
            images: 图像列表（可能为空）
            
        Returns:
            MultimodalMessage: Agent 可处理的统一格式
        """
        pass


class DescribeStrategy(VisionStrategy):
    """
    单模态策略：将图像转换为文字描述
    """
    
    def __init__(self, describer: "VisionDescriber"):
        self.describer = describer
    
    async def process(
        self, 
        text: str, 
        images: Optional[List[ImageContent]]
    ) -> MultimodalMessage:
        if not images:
            return MultimodalMessage(text=text)
        
        # 将用户问题传给 describer，获取针对性描述
        descriptions = []
        for img in images:
            desc = await self.describer.describe(image=img, query=text)
            descriptions.append(desc)
        
        # 构建增强文本
        description_text = "\n".join([
            f"[图像 {i+1}: {desc}]" 
            for i, desc in enumerate(descriptions)
        ])
        
        enhanced_text = f"用户问题：{text}\n\n视觉上下文：\n{description_text}"
        
        return MultimodalMessage(text=enhanced_text, images=None)


class PassthroughStrategy(VisionStrategy):
    """
    多模态策略：直接传递图像给 Agent
    """
    
    async def process(
        self, 
        text: str, 
        images: Optional[List[ImageContent]]
    ) -> MultimodalMessage:
        return MultimodalMessage(text=text, images=images)
```

### 5.3 视觉描述器接口

```python
class VisionDescriber(ABC):
    """
    视觉描述器基类
    
    将图像转换为文字描述，用于单模态策略
    
    关键设计：describe 需要知道用户的问题（query）
    """
    
    @abstractmethod
    async def describe(
        self, 
        image: ImageContent, 
        query: str
    ) -> str:
        """
        根据用户问题描述图像
        
        Args:
            image: 图像内容
            query: 用户的问题（用于引导描述方向）
            
        Returns:
            针对性的图像描述
        """
        pass


class VLMDescriber(VisionDescriber):
    """使用 VLM 描述图像（如 GLM-4V-Flash、GPT-4o-mini）"""
    
    def __init__(
        self, 
        model: str, 
        api_key: str,
        base_url: Optional[str] = None,
        base_prompt: str = None
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.base_prompt = base_prompt or (
            "用户正在看这张图片并提问。"
            "请根据用户的问题，提供相关的图像描述。"
            "只描述与问题相关的内容，不要描述无关信息。"
        )
    
    async def describe(self, image: ImageContent, query: str) -> str:
        prompt = f"{self.base_prompt}\n\n用户问题：{query}\n\n请描述图像中与此问题相关的内容。"
        # 调用 VLM API...
        pass


class YOLODescriber(VisionDescriber):
    """使用 YOLO 检测物体"""
    
    async def describe(self, image: ImageContent, query: str) -> str:
        # 检测物体，返回: "检测到: 人(1), 猫(2), 椅子(1)"
        pass


class OCRDescriber(VisionDescriber):
    """使用 OCR 识别文字"""
    
    async def describe(self, image: ImageContent, query: str) -> str:
        # 识别文字，返回: "图中文字: ..."
        pass


class CompositeDescriber(VisionDescriber):
    """组合多个描述器"""
    
    def __init__(self, describers: List[VisionDescriber]):
        self.describers = describers
    
    async def describe(self, image: ImageContent, query: str) -> str:
        results = []
        for describer in self.describers:
            desc = await describer.describe(image, query)
            results.append(desc)
        return " | ".join(results)
```

### 5.4 Agent 接口

```python
class AgentProvider(BaseProvider):
    """
    Agent provider interface
    
    统一接收 MultimodalMessage，由框架决定如何处理视觉
    """
    
    @abstractmethod
    async def chat(
        self,
        message: MultimodalMessage,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """
        Send a message to Agent and get streaming response.
        
        Args:
            message: 多模态消息（框架已处理）
            context: Optional context
            
        Yields:
            Response chunks (pure text deltas)
        """
        pass
```

---

## 6. Pipeline 集成

### 6.1 InputStation 扩展

```python
class InputStation(Station):
    """
    输入站 - 同时处理音频和可选的视频输入
    
    视频作为"增强上下文"注入到音频流中（原设计已支持扩展）
    """
    
    def __init__(
        self,
        session_id: str,
        read_queue: asyncio.Queue,           # 音频队列（原有）
        protocol: ProtocolBase,
        audio_codec: Optional[Any] = None,
        video_queue: Optional[asyncio.Queue] = None,  # 视频队列（新增）
        name: str = "InputStation"
    ):
        super().__init__(name=name)
        self._audio_queue = audio_queue
        self._video_queue = video_queue
        self._speech_context_frame: Optional[ImageContent] = None
        self._latest_frame: Optional[ImageContent] = None
        
        if video_queue:
            self._video_task = asyncio.create_task(self._collect_video_frames())
    
    async def _collect_video_frames(self):
        """后台协程：采集视频帧"""
        while self._running:
            try:
                frame_msg = await asyncio.wait_for(
                    self._video_queue.get(), timeout=0.1
                )
                image = self._parse_image(frame_msg)
                
                if frame_msg.get("trigger") == "vad_start":
                    # VAD 触发的帧 → 高优先级
                    self._speech_context_frame = image
                else:
                    # 心跳帧 → 低优先级
                    self._latest_frame = image
                    
            except asyncio.TimeoutError:
                continue
    
    def get_visual_context(self) -> Optional[ImageContent]:
        """获取视觉上下文，优先使用 VAD 触发的帧"""
        return self._speech_context_frame or self._latest_frame
    
    def clear_speech_context(self):
        """清除语音上下文帧（每轮对话后调用）"""
        self._speech_context_frame = None
```

### 6.2 AgentStation 集成

```python
class AgentStation(StreamStation):
    """
    Agent workstation - 集成视觉处理策略
    """
    
    def __init__(
        self,
        agent_provider: AgentProvider,
        vision_strategy: Optional[VisionStrategy] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.agent = agent_provider
        self.vision_strategy = vision_strategy or PassthroughStrategy()
    
    def _extract_images(
        self, 
        visual_context: Any
    ) -> Optional[List[ImageContent]]:
        """从 metadata 提取图像"""
        if visual_context is None:
            return None
        if isinstance(visual_context, ImageContent):
            return [visual_context]
        if isinstance(visual_context, list):
            return visual_context
        return None
    
    async def process_chunk(self, chunk: Chunk) -> AsyncIterator[Chunk]:
        if chunk.is_signal():
            yield chunk
            return
        
        # 提取文字（来自 ASR）
        text = chunk.data if isinstance(chunk.data, str) else str(chunk.data or "")
        
        # 提取图像（来自 metadata）
        images = self._extract_images(chunk.metadata.get("visual_context"))
        
        # 日志
        if images:
            self.logger.info(
                f"Agent processing: '{text[:50]}...' with {len(images)} image(s)"
            )
        
        # 通过策略处理
        multimodal_msg = await self.vision_strategy.process(text, images)
        
        # Passthrough input
        yield chunk
        
        # Stream agent response
        async for delta in self.agent.chat(multimodal_msg):
            yield TextDeltaChunk(
                data=delta, 
                source=self.name,
                session_id=chunk.session_id,
                turn_id=chunk.turn_id
            )
```

---

## 7. 使用示例

### 7.1 单模态配置（普通 LLM + VLM 描述）

```python
from providers.openai_agent import OpenAIAgentProvider
from providers.vision import VLMDescriber, DescribeStrategy

# 创建描述器
describer = VLMDescriber(
    model="glm-4v-flash",
    api_key="your-api-key",
    base_url="https://open.bigmodel.cn/api/paas/v4/"
)

# 创建策略
vision_strategy = DescribeStrategy(describer=describer)

# 创建 Agent
agent = OpenAIAgentProvider(
    model="deepseek/deepseek-chat",
    api_key="your-api-key"
)

# 创建 Station
agent_station = AgentStation(
    agent_provider=agent,
    vision_strategy=vision_strategy
)
```

### 7.2 多模态配置（VLM 直接处理）

```python
from providers.openai_agent import OpenAIAgentProvider
from providers.vision import PassthroughStrategy

# 创建策略（直接传递）
vision_strategy = PassthroughStrategy()

# 创建 VLM Agent
agent = OpenAIAgentProvider(
    model="gpt-4o",
    api_key="your-api-key"
)

# 创建 Station
agent_station = AgentStation(
    agent_provider=agent,
    vision_strategy=vision_strategy
)
```

### 7.3 组合描述器（YOLO + OCR）

```python
from providers.vision import (
    YOLODescriber, 
    OCRDescriber, 
    CompositeDescriber,
    DescribeStrategy
)

# 组合多个描述器
describer = CompositeDescriber([
    YOLODescriber(model_path="yolov8n.pt"),
    OCRDescriber(lang="chi_sim")
])

vision_strategy = DescribeStrategy(describer=describer)
```

---

## 8. 依赖关系分析

### 8.1 依赖图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              核心抽象层                                      │
│                                                                             │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         │
│   │  ImageContent    │  │ MultimodalMessage│  │   VisionStrategy │         │
│   │  (数据结构)       │  │  (数据结构)       │  │   (抽象基类)      │         │
│   └──────────────────┘  └──────────────────┘  └────────┬─────────┘         │
│                                                        │                    │
│   ┌──────────────────┐                                 │                    │
│   │ VisionDescriber  │ ←───────────────────────────────┤                    │
│   │  (抽象基类)       │                                 │                    │
│   └────────┬─────────┘                                 │                    │
│            │                                           │                    │
└────────────│───────────────────────────────────────────│────────────────────┘
             │                                           │
             │ 实现                                       │ 实现
             ▼                                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              具体实现层                                      │
│                                                                             │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         │
│   │  VLMDescriber    │  │  YOLODescriber   │  │   OCRDescriber   │         │
│   └──────────────────┘  └──────────────────┘  └──────────────────┘         │
│                                                                             │
│   ┌──────────────────┐  ┌──────────────────┐                               │
│   │ DescribeStrategy │  │PassthroughStrategy│                              │
│   │    (依赖 ↓)       │  └──────────────────┘                               │
│   └────────┬─────────┘                                                      │
│            │                                                                │
│            ▼                                                                │
│   ┌──────────────────┐                                                      │
│   │ VisionDescriber  │  ← 依赖抽象，不依赖具体实现                            │
│   │   (抽象)         │                                                      │
│   └──────────────────┘                                                      │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                              Station 层                                     │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                          AgentStation                                 │  │
│   │                                                                       │  │
│   │   依赖:                                                               │  │
│   │   - VisionStrategy (抽象) ✓                                           │  │
│   │   - AgentProvider (抽象) ✓                                            │  │
│   │   - ImageContent (数据结构) ✓                                         │  │
│   │   - MultimodalMessage (数据结构) ✓                                    │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                          InputStation                                 │  │
│   │                                                                       │  │
│   │   依赖:                                                               │  │
│   │   - ImageContent (数据结构) ✓                                         │  │
│   │   - ProtocolBase (抽象) ✓                                             │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.2 依赖反转验证

| 组件 | 依赖 | 类型 | 是否符合 DIP |
|------|------|------|-------------|
| AgentStation | VisionStrategy | 抽象基类 | ✓ |
| AgentStation | AgentProvider | 抽象基类 | ✓ |
| DescribeStrategy | VisionDescriber | 抽象基类 | ✓ |
| InputStation | ProtocolBase | 抽象基类 | ✓ |
| 所有组件 | ImageContent | 数据结构 | ✓ (值对象，无行为) |
| 所有组件 | MultimodalMessage | 数据结构 | ✓ (值对象，无行为) |

**结论**：设计符合依赖反转原则（DIP）
- 高层模块（Station）依赖抽象（Strategy、Provider）
- 抽象不依赖具体实现
- 数据结构作为值对象，跨层共享是合理的

---

## 9. 文件分布

### 9.1 新增文件

```
vixio/
├── providers/
│   ├── vision.py                    # 视觉相关数据结构和策略
│   │   ├── ImageContent             # 图像内容数据类
│   │   ├── MultimodalMessage        # 多模态消息数据类
│   │   ├── VisionStrategy           # 策略基类
│   │   ├── DescribeStrategy         # 单模态策略
│   │   └── PassthroughStrategy      # 多模态策略
│   │
│   └── vision_describers/           # 视觉描述器实现
│       ├── __init__.py
│       ├── base.py                  # VisionDescriber 基类
│       ├── vlm.py                   # VLM 描述器 (GLM-4V, GPT-4o-mini)
│       ├── yolo.py                  # YOLO 物体检测描述器
│       ├── ocr.py                   # OCR 文字识别描述器
│       └── composite.py             # 组合描述器
│
└── docs/
    └── vision_design.md             # 本设计文档
```

### 9.2 修改文件

| 文件 | 修改内容 | 状态 |
|------|---------|------|
| `providers/agent.py` | `chat()` 方法签名改为接收 `Union[str, MultimodalMessage]` | ✅ 已完成 |
| `providers/openai_agent/provider.py` | 实现多模态消息处理，支持图像输入 | ✅ 已完成 |
| `stations/agent.py` | 集成 `VisionStrategy`，从 `chunk.metadata` 提取视觉上下文 | ✅ 已完成 |
| `stations/transport_stations.py` | `InputStation` 增加 `video_queue` 支持，注入 visual_context | ✅ 已完成 |
| `transports/xiaozhi/protocol.py` | 添加 IMAGE 消息类型，解析图像消息 | ✅ 已完成 |
| `transports/xiaozhi/transport.py` | 处理图像消息，路由到 `video_queue` | ✅ 已完成 |
| `core/transport.py` | 添加 `_video_queues` 和 `get_video_queue()` 方法 | ✅ 已完成 |

### 9.3 可选修改

| 文件 | 修改内容 | 条件 |
|------|---------|------|
| `core/chunk.py` | 确认 `VideoChunk` 定义是否满足需求 | 如需扩展 |
| `stations/text_aggregator.py` | 合并 `visual_context` metadata | 如 metadata 传递有问题 |

---

## 9. 总结

| 层级 | 职责 | 组件 |
|------|------|------|
| **设备端** | 本地 VAD + 图像采集 | 轻量 VAD、摄像头 |
| **Transport** | 接收音视频，注入 metadata | InputStation |
| **框架策略** | 决定如何处理视觉输入 | VisionStrategy |
| **视觉处理** | 图像→描述（单模态时） | VisionDescriber |
| **Agent** | 统一处理 MultimodalMessage | AgentProvider |

**设计原则**：
1. 视觉数据通过 `Chunk.metadata` 传递，保持 Pipeline 一致性
2. 策略可配置，同一 Agent 可切换单模态/多模态
3. 描述器可组合，VLM + YOLO + OCR 任意组合
4. 设备端 VAD 触发，保证图像和语音时间对齐

