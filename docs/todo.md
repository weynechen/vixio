# xiaozhi compatibility
- [x] 延时计算有BUG，有负数产生，需要搞清楚责任链和装饰器怎么工作
- [x] transport 需要抽象和解耦 → 已完成: [transport_architecture.md](./transport_architecture.md)
- [x] function tool 需要接入 → 已完成: [function_tool_design.md](./function_tool_design.md)
- [x] 实现xiaozhi的vision mcp 接口
- [x] 制作python 包
- [x] 更改pipeline为DAG，充分解耦各个station的依赖
- [x] 解决litellm网络卡住的问题
- [x] 调整ASR为streamstation，引入流式ASR provider
- [x] 实现 dev-qwen ,使用 provider中增加qwen，其内部有asr/agent/tts的实现 -- bug存在，ASR和TTS会吞字
- [x] 更新realtimestation
- [x] 提供一个web版本，方便测试
- [x] vixio整个架构是stream的，最小单位是text；但实际上最后transport是基于sentence的。这里是有冲突的。 -- 经论证，只要client支持delta方式的，那vixio也可以使用delta来发送。
- [ ] 使用qwen的sample重写asr和tts.这两个可以支持vad和自己聚合句子，因此完全可以加速。
  - [x] 分析完成 → 设计文档: [qwen-asr-tts-redesign.md](./design/qwen-asr-tts-redesign.md)
  - 关键发现:
    * ASR集成VAD: qwen3-asr-flash-realtime有内置VAD，可省略silero-vad依赖
    * ASR支持context: 通过corpus_text传递上下文改善识别（如专业术语、人名等）
    * TTS支持流式输入: server_commit模式可智能分段，替代SentenceAggregator
    * 可简化pipeline: 从7个station减少到4个 (ASR→TextAgg→Agent→TTS)
      - ✗ 可移除: VAD, TurnDetector, SentenceAggregator (3个)
      - ✓ 必须保留: TextAggregator (ASR→TEXT_DELTA, Agent→TEXT, 格式转换必需)
    * 延迟优化: TTFT从~2000ms降至~1200ms (simplified模式)
  - [x] 实现Qwen3AsrFlashRealtimeProvider (支持VAD + context)
  - [x] 实现Qwen3TtsFlashRealtimeProvider (支持streaming input)
  - [x] 更新ASRProvider接口 (添加context参数)
  - [x] 更新TTSProvider接口 (添加synthesize_stream方法)
  - [x] 更新ASRStation (支持接收TEXT作为context)
  - [x] 更新TTSStation (支持接收TEXT_DELTA流式输入)
  - [x] 创建简化配置 (dev-qwen-simplified)
  - [x] 创建示例文件 (agent_chat_simple.py, agent_chat_full.py)
  - [x] **Phase 1: Chunk类型重构** (2025-12-22)
    * 引入 AUDIO_DELTA 和 AUDIO_COMPLETE 类型
    * 更新 AudioChunk 文档说明和默认类型
    * TransportInputStation 输出 AUDIO_DELTA (流式片段)
    * VADStation: 输入 AUDIO_DELTA，输出 AUDIO_DELTA (透传) + AUDIO_COMPLETE (合并)
    * TurnDetectorStation: 输入 AUDIO_DELTA (透传) + AUDIO_COMPLETE (收集)，输出 AUDIO_COMPLETE
    * ASRStation: 输入 AUDIO_COMPLETE，输出 TEXT_DELTA (批处理模式)
    * TTSStation: 输出 AUDIO_COMPLETE (完整音频帧)
    * RealtimeStation: 输入 AUDIO_DELTA，输出 AUDIO_COMPLETE
    * 所有 ALLOWED_INPUT_TYPES 支持向后兼容 (同时接受 AUDIO_RAW)
  - [x] **Phase 2: 实现 Simplified Pipeline (StreamingTTSStation)** (2025-12-22)
    * 创建 StreamingTTSStation (接受 TEXT_DELTA，输出 AUDIO_COMPLETE)
    * 更新 Qwen3TtsFlashRealtimeProvider:
      - 添加 append_text_stream() 方法（追加文本片段）
      - 添加 finish_stream() 方法（完成流并刷新剩余音频）
    * 支持 server_commit 模式（TTS 智能分段）
    * 更新 stations/__init__.py 导出 StreamingTTSStation
  - [x] **Phase 3: 实现 Streaming Pipeline (StreamingASRStation)** (2025-12-22)
    * 创建 StreamingASRStation (接受 AUDIO_DELTA，输出 TEXT_DELTA)
    * 更新 Qwen3AsrFlashRealtimeProvider:
      - 添加 supports_streaming_input 属性
      - 添加 append_audio_continuous() 方法（追加音频片段）
      - 添加 is_speech_ended() 方法（检测语音结束）
      - 添加 stop_streaming() 方法（停止流式会话）
      - 更新 callback 设置 _speech_ended 标志
    * 支持 ASR 内置 VAD（enable_vad: true）
    * 更新 stations/__init__.py 导出 StreamingASRStation
    * 创建 examples/agent_chat_streaming.py（4-station 全流式示例）
    * 添加 config dev-qwen-streaming 配置
  - [ ] Phase 4: 优化和完善
  - [ ] 测试完整功能 (需要DASHSCOPE_API_KEY)
  - [ ] 性能基准测试 (对比full vs simplified vs streaming延迟)
- [ ] Per-Session Transport 重构：消除 session_id 传递 → 设计文档: [per-session-transport.md](./design/per-session-transport.md)。使用session 对象来彻底隔离session 资源，防止泄露
- [ ] 实现 https://github.com/huggingface/smolagents 的provider。
- [ ] docker inference部署
- [ ] 快速开始的小工具
- [ ] 简单的管理后台，管理提示词这些
- [ ] 更新intranscribe
- [ ] 制作实时翻译示例
- [ ] 使用https://github.com/antvis/x6 或者 https://github.com/microsoft/react-dag-editor 制作DAG前端图展示数据流，延时，调试信息等等
- [ ] 在使用本地推理的情况下，tts往往会成为瓶颈点。每次推理需要100~1000ms。如果是100个并发，就要10~100s的排队了。

# xiaozhi plus
- [ ] 数字人station,并开发esp32-xiaozhi-pro 
- [ ] 接入video in , video 在语音驱动下做describe，辅助场景理解 → 已完成: [vision_design.md](./vision_design.md) 未测试
- [ ] 引入smartturn替换当前的默认turn