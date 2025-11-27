# 音频格式处理架构

## 设计原则

### 核心原则：Chunk 统一使用 PCM

**所有在 Pipeline 中流转的 AudioChunk 必须是 PCM 格式**

- **格式**：16-bit signed integer, little-endian
- **采样率**：默认 16000 Hz
- **声道**：默认 1 (mono)

### 职责分工

```
┌─────────────────────────────────────────────────────────────┐
│  Client (Xiaozhi Device)                                    │
│  - Sends: Opus encoded audio                                │
│  - Receives: Opus encoded audio                             │
└─────────────────┬───────────────────────────────────────────┘
                  │ Opus
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Transport Layer (XiaozhiTransport)                         │
│  ✓ Decode: Opus -> PCM (input)                             │
│  ✓ Encode: PCM -> Opus (output)                            │
└─────────────────┬───────────────────────────────────────────┘
                  │ PCM
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Pipeline (Chunk Flow)                                      │
│  - AudioChunk ALWAYS contains PCM data                      │
│  - VAD -> TurnDetector -> ASR -> Agent -> TTS              │
└─────────────────┬───────────────────────────────────────────┘
                  │ PCM
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Provider Layer                                             │
│  - Receives: PCM from chunks                                │
│  - If special format needed: Provider converts internally   │
│  - Returns: PCM for chunks                                  │
└─────────────────────────────────────────────────────────────┘
```

## 各层职责

### 1. Transport 层

**职责**：协议相关的格式转换

- **输入转换**：将协议格式（如 Opus）解码为 PCM
- **输出转换**：将 PCM 编码为协议格式（如 Opus）
- **示例**：`XiaozhiTransport` 负责 Opus ↔ PCM 转换

```python
# 输入时：Opus -> PCM
async def _decode_message(self, message, session_id):
    if msg_type == XiaozhiMessageType.AUDIO:
        opus_data = message.get("audio_data", b"")
        pcm_data = self.opus_codec.decode(opus_data)  # Opus -> PCM
        return AudioChunk(
            type=ChunkType.AUDIO_RAW,  # Always PCM
            data=pcm_data,
            ...
        )

# 输出时：PCM -> Opus
async def _encode_chunk(self, chunk):
    if chunk.type == ChunkType.AUDIO_RAW:
        pcm_data = chunk.data
        opus_data = self.opus_codec.encode(pcm_data)  # PCM -> Opus
        return {"type": "audio", "audio_data": opus_data}
```

### 2. Chunk 层

**职责**：统一的数据载体

- **AudioChunk** 只包含 PCM 数据
- **不包含**格式字段（如 `format="opus"`）
- **保证**：所有 Pipeline 中的音频都是 PCM

```python
@dataclass
class AudioChunk(Chunk):
    """
    Audio data chunk - ALWAYS contains PCM audio
    
    Transport layers are responsible for format conversion.
    """
    type: ChunkType = ChunkType.AUDIO_RAW
    data: bytes  # PCM bytes (16-bit signed, little-endian)
    sample_rate: int = 16000
    channels: int = 1
```

### 3. Provider 层

**职责**：音频处理逻辑

- **接收**：PCM 数据（从 Chunk）
- **处理**：如需特殊格式，自行转换
- **返回**：PCM 数据（给 Chunk）

```python
class SileroVADProvider:
    def detect(self, audio_data: bytes) -> bool:
        """
        Expects PCM audio (16-bit signed, little-endian)
        Transport layer handles format conversion
        """
        # Process PCM directly
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        ...
```

### 4. Station 层

**职责**：处理 Chunk 流

- **只处理** `ChunkType.AUDIO_RAW`
- **假设**：所有音频都是 PCM
- **不关心**：原始格式是什么（Transport 已转换）

```python
class VADStation:
    async def process_chunk(self, chunk):
        # Only process PCM audio
        if chunk.type != ChunkType.AUDIO_RAW:
            yield chunk
            return
        
        # Process PCM audio
        has_voice = self.vad.detect(chunk.data)
        ...
```

## 音频转换工具

### 位置：`utils/audio/convert.py`

提供常用的音频格式转换工具，供 Transport 和 Provider 使用。

### 当前支持：Opus

```python
from utils.audio import OpusCodec, opus_to_pcm, pcm_to_opus

# 方式1：使用 Codec 实例（推荐用于 Transport）
codec = OpusCodec(sample_rate=16000, channels=1, frame_duration_ms=60)
pcm_data = codec.decode(opus_packet)
opus_packet = codec.encode(pcm_data)

# 方式2：使用快捷函数（推荐用于简单场景）
pcm_data = opus_to_pcm(opus_packet)
opus_packet = pcm_to_opus(pcm_data)

# 方式3：使用缓存的 Codec（推荐用于高频调用）
codec = get_opus_codec()  # 16kHz mono 配置会被缓存
```

### Opus Codec 特性

- **自动初始化**：延迟创建 encoder/decoder
- **自动填充**：PCM 数据不足时自动补零
- **批量处理**：支持多帧编码/解码
- **错误处理**：编解码失败时记录日志

## 数据流示例

### 完整的语音对话流程

```
1. Client -> Transport (Opus)
   Xiaozhi设备发送 Opus 音频包（60ms）
   
2. Transport -> Pipeline (PCM)
   XiaozhiTransport.decode(): Opus -> PCM
   创建 AudioChunk(type=AUDIO_RAW, data=pcm_bytes)
   
3. Pipeline Processing (PCM)
   VAD -> TurnDetector -> ASR -> Agent -> TTS
   所有 Station 处理 PCM 数据
   
4. Pipeline -> Transport (PCM)
   TTS 输出 AudioChunk(type=AUDIO_RAW, data=pcm_bytes)
   
5. Transport -> Client (Opus)
   XiaozhiTransport.encode(): PCM -> Opus
   发送 Opus 音频包给设备
```

### VAD 处理示例

原 xiaozhi-server 的处理方式：
```
Opus (60ms) -> [VAD内部解码] -> PCM -> [缓冲累积] -> 512 samples -> [检测]
```

新架构的处理方式：
```
Opus (60ms) -> [Transport解码] -> PCM -> [VAD缓冲累积] -> 512 samples -> [检测]
```

**优势**：
- 职责清晰：Transport 负责解码，VAD 负责检测
- 易于测试：可以直接用 PCM 数据测试 VAD
- 易于扩展：添加新的 Transport 不影响 VAD

## 添加新的音频格式

### 步骤

1. **在 `utils/audio/convert.py` 中添加转换工具**

```python
class Mp3Codec:
    def decode(self, mp3_data: bytes) -> bytes:
        """Decode MP3 to PCM"""
        ...
    
    def encode(self, pcm_data: bytes) -> bytes:
        """Encode PCM to MP3"""
        ...
```

2. **在 Transport 中使用转换工具**

```python
class MyTransport(TransportBase):
    def __init__(self):
        self.mp3_codec = Mp3Codec()
    
    async def _decode_message(self, message, session_id):
        mp3_data = message.get("audio_data")
        pcm_data = self.mp3_codec.decode(mp3_data)  # MP3 -> PCM
        return AudioChunk(type=ChunkType.AUDIO_RAW, data=pcm_data, ...)
```

3. **Pipeline 无需修改**

所有 Station 和 Provider 继续处理 PCM 数据，无需任何改动。

## 常见问题

### Q: 为什么不在 Chunk 中保留格式信息？

**A**: 简化架构，职责明确
- Chunk 是 Pipeline 的统一数据格式
- 格式转换是 Transport 的职责
- 避免每个 Station 都要处理格式判断

### Q: 如果某个 Provider 需要特殊格式怎么办？

**A**: Provider 自行转换
- Provider 接收 PCM
- 内部转换为所需格式
- 输出时转换回 PCM

示例：
```python
class SpecialASRProvider:
    def transcribe(self, pcm_data: bytes) -> str:
        # Convert PCM to provider's required format
        special_format = self._pcm_to_special(pcm_data)
        
        # Call provider API
        result = self.api.recognize(special_format)
        
        return result
```

### Q: 性能影响如何？

**A**: 影响很小
- Opus 编解码非常快（C 实现）
- 只在边界处转换一次
- Pipeline 内部零拷贝传递

### Q: 为什么 VAD 需要缓冲？

**A**: Silero VAD 模型要求
- 模型期望至少 512 采样点（32ms）
- Opus 每包 960 采样点（60ms）
- 缓冲确保有足够数据进行检测
- 滑动窗口提高检测稳定性

## 测试

### 单元测试

```bash
# 测试音频转换工具
pytest tests/test_audio_convert.py -v

# 测试 Transport 格式转换
pytest tests/test_xiaozhi_transport.py::test_audio_format_conversion -v
```

### 集成测试

```bash
# 完整的语音对话测试
uv run examples/agent_chat.py
```

## 总结

**核心思想**：边界清晰，职责明确

- **Transport**：格式转换边界层
- **Chunk**：统一 PCM 数据载体
- **Pipeline**：纯粹的数据处理流
- **Provider**：灵活的处理实现

这样的设计让系统更容易理解、测试和扩展。

