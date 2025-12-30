# Xiaozhi Voice Server

基于 Vixio 框架的语音对话服务器（Qwen 版本）。

## 快速开始

### 1. 安装依赖

```bash
# 使用 pip
pip install vixio[quickstart]

# 或使用 uv（推荐）
uv pip install vixio[quickstart]
```

### 2. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，设置 DASHSCOPE_API_KEY
# 获取 API Key: https://dashscope.console.aliyun.com/
```

### 3. 运行服务器

```bash
# 使用 uv（推荐）
uv run python run.py

# 或直接运行
python run.py
```

### 4. 测试连接

服务器启动后，可以通过以下方式测试：

- **WebSocket 端点**: `ws://localhost:8000/xiaozhi/v1/`
- **健康检查**: http://localhost:8000/health
- **连接信息**: http://localhost:8000/connections
- **服务器信息**: http://localhost:8000/

## 自定义配置

### 修改提示词

编辑 `prompt.txt` 文件来自定义 AI 助手的行为：

```bash
# 使用默认提示词
cat prompt.txt

# 或使用其他预设提示词
cp prompts/coding_assistant.txt prompt.txt
cp prompts/storyteller.txt prompt.txt
```

提示词也可以通过环境变量设置：

```bash
export VIXIO_PROMPT="你是一个专业的编程助手"
uv run python run.py
```

### 修改配置文件

编辑 `config.yaml` 可以：
- 调整 provider 参数
- 切换不同的语音/模型
- 修改 VAD/ASR/TTS 设置

### 切换运行模式

本项目支持两种模式：

#### Pipeline 模式（默认）

稳定可控的级联架构：
```
Audio → VAD → TurnDetector → ASR → TextAggregator → Agent → SentenceAggregator → TTS → Audio
```

特点：
- ✅ 每个环节独立可控
- ✅ 稳定性高
- ✅ 适合生产环境

#### Realtime 模式

端到端的低延迟架构：
```
Audio → RealtimeStation → Audio
```

特点：
- ✅ 延迟更低
- ✅ 配置简单
- ✅ 端到端优化

如需切换模式，重新初始化项目：

```bash
# 使用 Realtime 模式
uvx vixio init xiaozhi-server --preset qwen-realtime --output my-realtime-bot
```

## 配置示例

### 自定义端口

```bash
# 方法 1：命令行参数
uv run python run.py --port 9000

# 方法 2：环境变量
export VIXIO_PORT=9000
uv run python run.py
```

### 自定义主机地址

```bash
# 只监听本地
export VIXIO_HOST=127.0.0.1
uv run python run.py
```

### 使用不同的配置文件

```bash
uv run python run.py --config my_config.yaml
```

## 故障排查

### API Key 未设置

```
❌ Error: DASHSCOPE_API_KEY not set!
```

**解决方案**：
1. 编辑 `.env` 文件，设置 `DASHSCOPE_API_KEY`
2. 或导出环境变量：`export DASHSCOPE_API_KEY=sk-xxx`

### 依赖缺失

```
ModuleNotFoundError: No module named 'xxx'
```

**解决方案**：
```bash
# 重新安装依赖
pip install vixio[quickstart]

# 或使用 uv
uv pip install vixio[quickstart]
```

### 端口被占用

```
Error: Address already in use
```

**解决方案**：
```bash
# 使用不同的端口
uv run python run.py --port 9000
```

## 许可证

Apache License
