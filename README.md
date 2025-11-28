# Vixio - Streaming Audio Processing Framework

一个基于流水线设计的流式语音agent处理框架，灵感来自 Pipecat。

## 设计理念

- **Pipeline（流水线）**: 串联多个工站形成完整的处理流程
- **Station（工站）**: 负责特定的处理任务（VAD/ASR/Agent/TTS）
- **Chunk（载体）**: 
  - **Data Chunk（产品）**: Audio/Vision/Text，经过工站加工转换
  - **Signal Chunk（消息）**: Control/Event，立即透传并触发工站状态变化
- **Transport（传输层）**: 流水线的输入输出接口，完全解耦传输细节

### Pipeline
线性异步队列链：队列 ->station process -> 队列 -> station proecess -> output

每个station 有一个 task和一个queue。 station process 出来的chunk放入到queue中。

主task从最后一个queue中取出chunk给到output。

注意： 不适合多分钟的情况。

### ControlBus

### Station

### Chunk


## 项目状态

当前版本: **v0.1.0 (开发中)**



**注意**: 本项目目前处于开发阶段，API 可能会发生变化。

