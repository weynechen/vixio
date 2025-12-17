# CLI Quick Start 设计文档

## 1. 设计目标

让 vixio 支持两类用户的快速使用：

### 目标用户

1. **小白用户**：一条命令快速体验语音对话服务
2. **开发者用户**：使用 vixio 作为 lib，参考 examples 自定义开发

### 核心原则

- **渐进式复杂度**：从简单到复杂，层层递进
- **零配置体验**：小白用户无需 git clone，一条命令启动
- **灵活可扩展**：开发者可以导出模板完全自定义

## 2. 使用场景

### Level 0: 快速体验（小白用户）

```bash
# 最简单 - 使用 Qwen 云服务
uvx vixio run xiaozhi-server --dashscope-key sk-xxx

# 使用 Realtime 模式（低延迟）
uvx vixio run xiaozhi-server --preset qwen-realtime --dashscope-key sk-xxx

# 自定义提示词
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx \
  --prompt "你是一个专业的编程助手"

# 从文件加载提示词
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx \
  --prompt-file ./my_prompt.txt
```

**特点**：
- 无需 git clone 项目
- 无需配置文件
- 使用 Qwen 服务（ASR + TTS + LLM 共用一个 API Key）
- 内置智能默认值

### Level 1: 导出模板（深入用户）

```bash
# 导出项目模板到当前目录
uvx vixio init xiaozhi-server

# 或指定预设
uvx vixio init xiaozhi-server --preset qwen-realtime

# 生成的目录结构：
# xiaozhi-server/
#   ├── .env.example       # 环境变量模板
#   ├── config.yaml        # 完整配置
#   ├── prompt.txt         # 提示词模板
#   ├── run.py             # 启动脚本
#   └── README.md          # 使用说明

# 使用
cd xiaozhi-server
cp .env.example .env
# 编辑 .env、config.yaml、prompt.txt
uv run python run.py
```

**特点**：
- 导出完整的项目模板
- 可以修改配置文件、提示词
- 适合需要深度自定义的用户

### Level 2: 作为库使用（开发者）

```bash
# 安装
pip install vixio[dev-qwen]

# 参考 examples/ 自己编写代码
```

**特点**：
- 完全灵活
- 参考 examples 目录的示例代码
- 适合开发者深度集成

## 3. 预设配置（Presets）

### 3.1 预设列表

只提供两个预设，简化选择：

#### `qwen` (默认)

使用 Qwen 云服务的级联模式（Pipeline Architecture）：
- VAD: Silero VAD (本地)
- ASR: Qwen ASR Realtime
- LLM: Qwen LLM
- TTS: Qwen TTS Realtime

**优点**：
- 稳定性高
- 可控性强
- 每个环节独立
- 适合生产环境

**架构**：
```
Audio → VAD → TurnDetector → ASR → TextAggregator → Agent → SentenceAggregator → TTS → Audio
```

#### `qwen-realtime`

使用 Qwen Omni Realtime 端到端模式：
- Realtime: Qwen Omni Realtime (VAD + ASR + LLM + TTS 一体化)

**优点**：
- 延迟低
- 配置简单
- 端到端优化

**架构**：
```
Audio → RealtimeStation → Audio
```

### 3.2 预设配置文件

预设配置文件位于 `src/vixio/presets/` 目录（打包到 wheel 中）。

#### `qwen.yaml`

```yaml
# Qwen Pipeline Mode (Default)
# Stable, controllable, production-ready

providers:
  # VAD Provider (Local - Silero ONNX)
  vad:
    provider: silero-vad-local
    config:
      threshold: 0.35
      threshold_low: 0.15
      frame_window_threshold: 8
      use_gpu: false
  
  # ASR Provider (Qwen Cloud - Realtime WebSocket)
  asr:
    provider: qwen-asr-realtime
    config:
      api_key: ${DASHSCOPE_API_KEY}
      model: "qwen3-asr-flash-realtime"
      language: "zh"
      sample_rate: 16000
  
  # Agent Provider (Qwen LLM)
  agent:
    provider: openai-agent
    config:
      api_key: ${DASHSCOPE_API_KEY}
      model: "qwen-plus"
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      temperature: 0.7
      max_tokens: 2000
  
  # TTS Provider (Qwen Cloud - Realtime WebSocket)
  tts:
    provider: qwen-tts-realtime
    config:
      api_key: ${DASHSCOPE_API_KEY}
      model: "qwen3-tts-flash-realtime"
      voice: "Cherry"
      language_type: "Chinese"
      sample_rate: 16000

  # Vision Provider (Optional)
  vlm:
    provider: openai-vlm-remote
    config:
      model: "qwen-vl-max"
      api_key: "${DASHSCOPE_API_KEY}"
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

#### `qwen-realtime.yaml`

```yaml
# Qwen Omni Realtime Mode
# Low latency, end-to-end voice conversation

providers:
  # Realtime Provider (Qwen Omni - End-to-End Voice)
  realtime:
    provider: qwen-omni-realtime
    config:
      api_key: ${DASHSCOPE_API_KEY}
      model: "qwen3-omni-flash-realtime"
      voice: "Cherry"
      instructions: ${PROMPT}
      input_sample_rate: 16000
      output_sample_rate: 24000
      vad_threshold: 0.5
      silence_duration_ms: 800

  # Vision Provider (Optional)
  vlm:
    provider: openai-vlm-remote
    config:
      model: "qwen-vl-max"
      api_key: "${DASHSCOPE_API_KEY}"
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

## 4. CLI 命令设计

### 4.1 `vixio run` 命令

快速运行预制服务，无需 git clone。

#### 命令格式

```bash
vixio run <template> [options]
```

#### 参数

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--preset` | `-p` | 预设配置 (qwen/qwen-realtime) | qwen |
| `--dashscope-key` | `-k` | DashScope API Key | 从环境变量读取 |
| `--prompt` | | 自定义提示词（一行文本） | 默认提示词 |
| `--prompt-file` | | 从文件加载提示词 | - |
| `--host` | | 服务监听地址 | 0.0.0.0 |
| `--port` | | 服务监听端口 | 8000 |

#### 示例

```bash
# 1. 最简单 - 使用环境变量
export DASHSCOPE_API_KEY=sk-xxx
uvx vixio run xiaozhi-server

# 2. 指定 API Key
uvx vixio run xiaozhi-server --dashscope-key sk-xxx

# 3. 使用 Realtime 模式
uvx vixio run xiaozhi-server --preset qwen-realtime --dashscope-key sk-xxx

# 4. 自定义提示词
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx \
  --prompt "你是一个友好的AI助手，擅长讲故事"

# 5. 从文件加载提示词
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx \
  --prompt-file ./prompts/coding_assistant.txt

# 6. 自定义端口
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx \
  --port 9000
```

### 4.2 `vixio init` 命令

导出项目模板到指定目录，用于深度自定义。

#### 命令格式

```bash
vixio init <template> [options]
```

#### 参数

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--preset` | `-p` | 预设配置 (qwen/qwen-realtime) | qwen |
| `--output` | `-o` | 输出目录 | ./<template> |

#### 示例

```bash
# 1. 导出到当前目录
uvx vixio init xiaozhi-server

# 2. 指定输出目录
uvx vixio init xiaozhi-server --output ./my-voice-bot

# 3. 使用 Realtime 预设
uvx vixio init xiaozhi-server --preset qwen-realtime

# 生成的目录结构：
# xiaozhi-server/
#   ├── .env.example          # 环境变量模板
#   ├── config.yaml           # 完整配置（来自预设）
#   ├── prompt.txt            # 提示词模板
#   ├── run.py                # 启动脚本
#   └── README.md             # 使用说明

# 使用
cd xiaozhi-server
cp .env.example .env
# 编辑 DASHSCOPE_API_KEY
# 编辑 config.yaml（可选）
# 编辑 prompt.txt（可选）
uv run python run.py
```

## 5. 提示词管理

### 5.1 默认提示词

内置一个通用的聊天助手提示词：

```
你是一个友好的AI语音助手。用简洁自然的语气回答问题，像和朋友聊天一样。
回答要简短，适合语音播放。总是先用一个简短的句子回答核心问题，以句号结束，不要用逗号。
如果需要详细说明，再分成多个简短的句子。
```

### 5.2 提示词优先级

```
1. 命令行 --prompt（最高优先级）
2. 命令行 --prompt-file
3. 环境变量 VIXIO_PROMPT
4. 配置文件中的 prompt
5. 内置默认提示词（最低优先级）
```

### 5.3 提示词示例

用户可以创建自己的提示词文件：

**coding_assistant.txt**:
```
你是一个专业的编程助手，擅长 Python、JavaScript 和系统设计。
回答要简洁明了，适合语音交流。先给出核心答案，再解释细节。
代码示例要简短，重点突出关键部分。
```

**storyteller.txt**:
```
你是一个充满想象力的讲故事高手。
用生动有趣的语言讲故事，节奏要适合语音播放。
每次回答一小段，保持悬念，让听众想继续听下去。
```

## 6. 实现要点

### 6.1 目录结构

```
src/vixio/
├── presets/                    # 预设配置（打包到 wheel）
│   ├── __init__.py
│   ├── qwen.yaml              # Qwen Pipeline 模式
│   └── qwen-realtime.yaml     # Qwen Realtime 模式
│
├── templates/                  # 项目模板（打包到 wheel）
│   └── xiaozhi-server/
│       ├── .env.example
│       ├── config.yaml
│       ├── prompt.txt
│       ├── run.py
│       └── README.md
│
└── cli.py                      # CLI 实现
```

### 6.2 pyproject.toml 配置

```toml
[tool.setuptools.package-data]
vixio = [
    "presets/*.yaml",
    "templates/**/*",
]

[project.scripts]
vixio = "vixio.cli:main"
```

### 6.3 CLI 实现逻辑

#### `vixio run` 命令实现

```python
def run_command(args):
    """
    Run xiaozhi-server directly from package.
    
    Steps:
    1. Load preset config from package
    2. Override with CLI arguments
    3. Set environment variables
    4. Start server
    """
    # 1. Load preset
    preset_name = args.preset or "qwen"
    preset_path = get_preset_path(preset_name)
    
    # 2. Prepare environment variables
    env_vars = {}
    
    # API Key
    if args.dashscope_key:
        env_vars["DASHSCOPE_API_KEY"] = args.dashscope_key
    elif not os.getenv("DASHSCOPE_API_KEY"):
        print("Error: DASHSCOPE_API_KEY not provided!")
        print("Use: --dashscope-key <key> or export DASHSCOPE_API_KEY=<key>")
        sys.exit(1)
    
    # Prompt
    if args.prompt:
        env_vars["VIXIO_PROMPT"] = args.prompt
    elif args.prompt_file:
        with open(args.prompt_file) as f:
            env_vars["VIXIO_PROMPT"] = f.read().strip()
    else:
        env_vars["VIXIO_PROMPT"] = get_default_prompt()
    
    # 3. Set environment
    for key, value in env_vars.items():
        os.environ[key] = value
    
    # 4. Start server
    if preset_name == "qwen-realtime":
        start_realtime_server(preset_path, args)
    else:
        start_pipeline_server(preset_path, args)


def start_pipeline_server(config_path, args):
    """Start pipeline mode server (like agent_chat.py)"""
    # Import and run agent_chat.py logic
    from vixio.examples import agent_chat
    asyncio.run(agent_chat.main(
        config_path=config_path,
        host=args.host,
        port=args.port,
    ))


def start_realtime_server(config_path, args):
    """Start realtime mode server (like realtime_chat.py)"""
    # Import and run realtime_chat.py logic
    from vixio.examples import realtime_chat
    asyncio.run(realtime_chat.main(
        config_path=config_path,
        host=args.host,
        port=args.port,
    ))
```

#### `vixio init` 命令实现

```python
def init_command(args):
    """
    Export template to directory.
    
    Steps:
    1. Get template from package
    2. Copy to output directory
    3. Substitute preset config
    4. Show usage instructions
    """
    template_name = args.template  # e.g., "xiaozhi-server"
    preset_name = args.preset or "qwen"
    output_dir = args.output or f"./{template_name}"
    
    # 1. Get template path from package
    template_path = get_template_path(template_name)
    
    # 2. Check output directory
    if os.path.exists(output_dir):
        print(f"Error: Directory {output_dir} already exists!")
        sys.exit(1)
    
    # 3. Copy template
    shutil.copytree(template_path, output_dir)
    
    # 4. Copy preset config
    preset_path = get_preset_path(preset_name)
    shutil.copy(preset_path, f"{output_dir}/config.yaml")
    
    # 5. Show instructions
    print(f"✓ Template created at: {output_dir}")
    print(f"✓ Preset: {preset_name}")
    print("")
    print("Next steps:")
    print(f"  cd {output_dir}")
    print(f"  cp .env.example .env")
    print(f"  # Edit .env and set DASHSCOPE_API_KEY")
    print(f"  # Edit prompt.txt (optional)")
    print(f"  # Edit config.yaml (optional)")
    print(f"  uv run python run.py")
```

### 6.4 模板文件内容

#### `.env.example`

```bash
# DashScope API Key (Required)
# Get it from: https://dashscope.console.aliyun.com/
DASHSCOPE_API_KEY=your-dashscope-api-key-here

# Optional: Custom prompt (will override prompt.txt)
# VIXIO_PROMPT=你是一个友好的AI助手
```

#### `prompt.txt`

```
你是一个友好的AI语音助手。用简洁自然的语气回答问题，像和朋友聊天一样。
回答要简短，适合语音播放。总是先用一个简短的句子回答核心问题，以句号结束，不要用逗号。
如果需要详细说明，再分成多个简短的句子。
```

#### `run.py`

```python
#!/usr/bin/env python
"""
Xiaozhi Voice Server - Qwen Edition

Usage:
    uv run python run.py
    
    # Or with custom config
    uv run python run.py --config custom_config.yaml
"""

import dotenv
dotenv.load_dotenv()

import asyncio
import os
import sys
from pathlib import Path

# Get current directory
CURRENT_DIR = Path(__file__).parent

# Load environment variables
API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    print("Error: DASHSCOPE_API_KEY not set!")
    print("Please edit .env file and set DASHSCOPE_API_KEY")
    sys.exit(1)

# Load prompt
PROMPT_FILE = CURRENT_DIR / "prompt.txt"
if PROMPT_FILE.exists():
    with open(PROMPT_FILE) as f:
        CUSTOM_PROMPT = f.read().strip()
    os.environ["VIXIO_PROMPT"] = CUSTOM_PROMPT

# Import vixio
from vixio.cli import run_server

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    
    config_path = CURRENT_DIR / args.config
    
    # Detect mode from config
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    if "realtime" in config.get("providers", {}):
        mode = "realtime"
    else:
        mode = "pipeline"
    
    print(f"Starting Xiaozhi Server ({mode} mode)...")
    print(f"Config: {config_path}")
    print(f"Listen: {args.host}:{args.port}")
    
    run_server(
        config_path=str(config_path),
        host=args.host,
        port=args.port,
        mode=mode,
    )
```

#### `README.md`

```markdown
# Xiaozhi Voice Server

基于 Vixio 框架的语音对话服务器（Qwen 版本）。

## 快速开始

1. 安装依赖：
   ```bash
   pip install vixio[dev-qwen]
   # 或使用 uv
   uv pip install vixio[dev-qwen]
   ```

2. 配置环境变量：
   ```bash
   cp .env.example .env
   # 编辑 .env，设置 DASHSCOPE_API_KEY
   ```

3. 运行服务器：
   ```bash
   uv run python run.py
   ```

4. 测试连接：
   - WebSocket: `ws://localhost:8000/xiaozhi/v1/`
   - Health: http://localhost:8000/health

## 自定义

### 修改提示词

编辑 `prompt.txt` 文件。

### 修改配置

编辑 `config.yaml` 文件：
- 切换 provider
- 调整参数
- 添加功能

### 切换模式

本项目使用 Pipeline 模式（VAD → ASR → LLM → TTS）。

如需使用 Realtime 模式，重新初始化：
```bash
uvx vixio init xiaozhi-server --preset qwen-realtime --output my-realtime-bot
```

## 文档

- [Vixio 文档](https://github.com/weynechen/vixio)
- [DashScope 文档](https://help.aliyun.com/zh/model-studio/)
```

## 7. 包依赖

### 7.1 必需依赖（默认安装）

```toml
dependencies = [
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "ruamel.yaml>=0.18.16",
    "loguru>=0.7.3",
]
```

### 7.2 可选依赖（按需安装）

```toml
[project.optional-dependencies]

# Quick start with Qwen (recommended for beginners)
quickstart = [
    "vixio[xiaozhi,openai-agent,silero-vad-local,qwen]",
]

# Same as quickstart (for compatibility)
dev-qwen = [
    "vixio[xiaozhi,openai-agent,silero-vad-local,qwen]",
]
```

### 7.3 安装指南

```bash
# For quick start users
pip install vixio[quickstart]

# Or
uvx --from vixio[quickstart] vixio run xiaozhi-server
```

## 8. 文档更新

需要更新以下文档：

1. **README.md** - 添加 Quick Start 章节
2. **examples/README.md** - 添加 CLI 使用说明
3. **新增本文档** - `docs/cli_quickstart_design.md`

## 9. 发布检查清单

- [ ] 创建 `src/vixio/presets/` 目录
- [ ] 创建 `qwen.yaml` 和 `qwen-realtime.yaml` 预设
- [ ] 创建 `src/vixio/templates/xiaozhi-server/` 模板
- [ ] 更新 `cli.py` 添加 `run` 和 `init` 命令
- [ ] 更新 `pyproject.toml` 包含 package-data
- [ ] 测试 `uvx vixio run` 命令
- [ ] 测试 `uvx vixio init` 命令
- [ ] 更新 README.md
- [ ] 发布新版本到 PyPI

## 10. 示例场景

### 场景 1: 小白用户第一次使用

```bash
# 1. 获取 API Key
# 访问 https://dashscope.console.aliyun.com/

# 2. 一条命令启动
export DASHSCOPE_API_KEY=sk-xxx
uvx vixio run xiaozhi-server

# 3. 测试
# 打开浏览器访问 http://localhost:8000
```

### 场景 2: 用户想自定义提示词

```bash
# 创建提示词文件
echo "你是一个专业的编程助手" > my_prompt.txt

# 使用自定义提示词
uvx vixio run xiaozhi-server \
  --dashscope-key sk-xxx \
  --prompt-file my_prompt.txt
```

### 场景 3: 用户想深度自定义

```bash
# 1. 导出模板
uvx vixio init xiaozhi-server

# 2. 配置
cd xiaozhi-server
cp .env.example .env
# 编辑 .env, config.yaml, prompt.txt

# 3. 运行
uv run python run.py
```

### 场景 4: 开发者使用作为库

```bash
# 1. 安装
pip install vixio[dev-qwen]

# 2. 参考 examples 编写自己的代码
# 查看 examples/agent_chat.py
# 查看 examples/realtime_chat.py

# 3. 深度集成到自己的项目
```

## 11. 优势总结

### 对比现有方案

| 步骤 | 旧方案 | 新方案（Quick Start） |
|------|--------|----------------------|
| 安装 | `pip install vixio` | ✓ 内置在 uvx 命令中 |
| 获取代码 | `git clone ...` | ✗ 不需要 |
| 配置环境 | 编辑 `.env` 文件 | ✓ 命令行参数 |
| 运行 | `uv run python examples/agent_chat.py --env dev-qwen` | `uvx vixio run xiaozhi-server --dashscope-key xxx` |
| **总步骤** | **4 步** | **1 步** |

### 核心价值

1. **降低门槛**：从 4 步到 1 步，零配置快速体验
2. **保持灵活**：支持渐进式复杂度，满足不同用户需求
3. **统一体验**：`uvx` 命令统一入口，无需 git clone
4. **易于维护**：预设配置和模板内置在包中，版本一致

## 12. 后续优化

可选的未来增强功能：

1. **预设扩展**：添加更多语言/地区的预设
2. **交互式向导**：`vixio run xiaozhi-server --interactive`
3. **配置验证**：启动前检查配置有效性
4. **健康检查**：自动检测 API Key 是否有效
5. **使用统计**：（可选）匿名统计使用情况，改进产品

---

**文档版本**: v1.0  
**更新日期**: 2024-12-17  
**作者**: Vixio Team
