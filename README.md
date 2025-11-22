# Vixio - Streaming Audio Processing Framework

一个基于流水线设计的流式语音agent处理框架，灵感来自 Pipecat。

## 设计理念

- **Pipeline（流水线）**: 串联多个工站形成完整的处理流程
- **Station（工站）**: 负责特定的处理任务（VAD/ASR/Agent/TTS）
- **Chunk（载体）**: 
  - **Data Chunk（产品）**: Audio/Vision/Text，经过工站加工转换
  - **Signal Chunk（消息）**: Control/Event，立即透传并触发工站状态变化
- **Transport（传输层）**: 流水线的输入输出接口，完全解耦传输细节


## 项目状态

当前版本: **v0.1.0 (开发中)**



**注意**: 本项目目前处于开发阶段，API 可能会发生变化。

