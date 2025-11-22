# Vixio - Streaming Audio Processing Framework

一个基于流水线设计的流式音频处理框架，灵感来自 Pipecat。

## 🎯 设计理念

- **Pipeline（流水线）**: 串联多个工站形成完整的处理流程
- **Station（工站）**: 负责特定的处理任务（VAD/ASR/Agent/TTS）
- **Chunk（载体）**: 
  - **Data Chunk（产品）**: Audio/Vision/Text，经过工站加工转换
  - **Signal Chunk（消息）**: Control/Event，立即透传并触发工站状态变化
- **Transport（传输层）**: 流水线的输入输出接口，完全解耦传输细节

## 🎯 支持的 Providers

- **VAD**: Silero VAD（本地模型）
- **ASR**: Sherpa-ONNX（本地推理）
- **TTS**: Edge TTS（微软服务）

## 📦 项目状态

当前版本: **v0.1.0 (开发中)**

文件结构已创建完成，正在实施核心功能。

## 📚 文档

- [架构概览](ARCHITECTURE.md) - 系统架构和核心概念
- [设计方案](doc/vixio%20设计方案.md) - 完整的设计文档
- [实施路线图](ROADMAP.md) - 开发计划和进度

## 🚀 快速开始

（待实现核心功能后添加）

## 🏗️ 目录结构

```
vixio/
├── core/          # 核心抽象层（Chunk, Station, Pipeline, Transport, Session）
├── stations/      # Station 实现（VAD, ASR, TTS 等）
├── providers/     # Provider 接口和实现
│   ├── *.py      # 接口定义（base, vad, asr, tts, agent, vision）
│   ├── silero_vad/        # Silero VAD 实现
│   ├── sherpa_onnx_local/ # Sherpa-ONNX ASR 实现
│   └── edge_tts/          # Edge TTS 实现
├── transports/    # Transport 实现（Xiaozhi, WebSocket, HTTP）
├── utils/         # 工具函数
├── config/        # 配置管理
├── examples/      # 使用示例
└── tests/         # 测试用例
```

## 🛠️ 开发进度

参考 [ROADMAP.md](ROADMAP.md)

- [x] 阶段 0: 准备工作 - 文件结构已创建
- [ ] 阶段 1: 核心基础（Chunk, Station, Pipeline）
- [ ] 阶段 2: Transport 层
- [ ] 阶段 3: 基础 Stations
- [ ] 阶段 4: 辅助 Stations
- [ ] 阶段 5: Session 管理
- [ ] 阶段 6: 端到端测试
- [ ] 阶段 7: 文档和示例
- [ ] 阶段 8: 生产部署

## 💡 示例

### 简单的语音对话服务器

```python
# 待核心功能实现后添加示例代码
```

## 🤝 贡献

欢迎贡献代码、报告问题和提出建议！

## 📄 许可证

（待添加）

---

**注意**: 本项目目前处于开发阶段，API 可能会发生变化。

