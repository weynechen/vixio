#!/usr/bin/env python
"""
Xiaozhi Voice Server - Qwen Edition

Usage:
    uv run python run.py
    
    # Or with custom config
    uv run python run.py --config custom_config.yaml
    
    # Or with custom port
    uv run python run.py --port 9000
"""

import dotenv
dotenv.load_dotenv()

import os
import sys
from pathlib import Path

# Disable LiteLLM model cost map download to avoid startup delay
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# Get current directory
CURRENT_DIR = Path(__file__).parent

# Check API Key
API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    print("❌ Error: DASHSCOPE_API_KEY not set!")
    print("Please edit .env file and set DASHSCOPE_API_KEY")
    print("Get your API key from: https://dashscope.console.aliyun.com/")
    sys.exit(1)

# Load prompt from file if exists
PROMPT_FILE = CURRENT_DIR / "prompt.txt"
if PROMPT_FILE.exists() and not os.getenv("VIXIO_PROMPT"):
    with open(PROMPT_FILE, encoding='utf-8') as f:
        CUSTOM_PROMPT = f.read().strip()
    os.environ["VIXIO_PROMPT"] = CUSTOM_PROMPT
    print(f"✓ Loaded prompt from: {PROMPT_FILE}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Xiaozhi Voice Server")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--host", default=None, help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: 8000)")
    parser.add_argument("--env", default=None, help="Environment name in config (auto-detected)")
    args = parser.parse_args()
    
    config_path = CURRENT_DIR / args.config
    
    if not config_path.exists():
        print(f"❌ Error: Config file not found: {config_path}")
        sys.exit(1)
    
    # Detect mode from config
    import yaml
    with open(config_path, encoding='utf-8') as f:
        config_data = yaml.safe_load(f)
    
    # Check if it's a multi-env config or single config
    if "providers" in config_data:
        # Single environment config
        providers = config_data["providers"]
        env_name = None
    else:
        # Multi-environment config, auto-detect or use specified
        if args.env:
            env_name = args.env
        else:
            # Auto-detect first available environment
            available_envs = [k for k in config_data.keys() if k != "version"]
            if not available_envs:
                print("❌ Error: No environment found in config file")
                sys.exit(1)
            env_name = available_envs[0]
            print(f"ℹ️  Auto-detected environment: {env_name}")
        
        if env_name not in config_data:
            print(f"❌ Error: Environment '{env_name}' not found in config")
            sys.exit(1)
        
        providers = config_data[env_name].get("providers", {})
    
    # Detect mode
    if "realtime" in providers:
        mode = "realtime"
    elif "agent" in providers:
        mode = "pipeline"
    else:
        print("❌ Error: Cannot detect mode from config (no 'realtime' or 'agent' provider)")
        sys.exit(1)
    
    host = args.host or os.getenv("VIXIO_HOST", "0.0.0.0")
    port = args.port or int(os.getenv("VIXIO_PORT", "8000"))
    
    print("=" * 70)
    print("🎙️  Xiaozhi Voice Server")
    print("=" * 70)
    print(f"Mode:   {mode.upper()}")
    print(f"Config: {config_path}")
    print(f"Host:   {host}")
    print(f"Port:   {port}")
    print("=" * 70)
    print()
    
    # Import and run
    try:
        if mode == "realtime":
            # Import realtime server
            import asyncio
            from vixio.xiaozhi.runners import run_realtime_server
            
            asyncio.run(run_realtime_server(
                config_path=str(config_path),
                env=env_name,
                host=host,
                port=port,
            ))
        else:
            # Import pipeline server
            import asyncio
            from vixio.xiaozhi.runners import run_pipeline_server
            
            asyncio.run(run_pipeline_server(
                config_path=str(config_path),
                env=env_name,
                host=host,
                port=port,
            ))
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
