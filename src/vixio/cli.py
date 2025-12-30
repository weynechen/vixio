"""
Vixio CLI - Command Line Interface

Usage:
    # Quick start - run from package
    vixio run xiaozhi-server --dashscope-key sk-xxx
    
    # Initialize project template
    vixio init xiaozhi-server
    
    # Serve inference services
    vixio serve vad --port 50051
    vixio serve asr --port 50052
    vixio serve tts --port 50053
    vixio serve all
    
    # Show version
    vixio --version
"""

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def get_version() -> str:
    """Get vixio version"""
    try:
        from vixio import __version__
        return __version__
    except ImportError:
        return "unknown"


def get_inference_dir() -> str:
    """Get the inference directory path"""
    # Try relative to this file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # src/vixio/cli.py -> inference/
    inference_dir = os.path.join(current_dir, '..', '..', '..', 'inference')
    inference_dir = os.path.abspath(inference_dir)
    
    if os.path.exists(inference_dir):
        return inference_dir
    
    # Try from current working directory
    cwd_inference = os.path.join(os.getcwd(), 'inference')
    if os.path.exists(cwd_inference):
        return cwd_inference
    
    return None


def serve_vad(port: int = 50051) -> None:
    """Start VAD gRPC service"""
    inference_dir = get_inference_dir()
    
    if inference_dir:
        service_dir = os.path.join(inference_dir, 'silero_vad')
        if os.path.exists(service_dir):
            print(f"Starting VAD service on port {port}...")
            print(f"Working directory: {service_dir}")
            try:
                subprocess.run(
                    ['uv', 'run', 'python', 'server.py', '--port', str(port)],
                    cwd=service_dir,
                    check=True
                )
            except FileNotFoundError:
                print("Error: uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh")
                sys.exit(1)
            except subprocess.CalledProcessError as e:
                print(f"Service exited with error: {e}")
                sys.exit(1)
            return
    
    # Try importing as installed package
    try:
        from vixio_silero_vad_service import server
        print(f"Starting VAD service on port {port}...")
        import asyncio
        asyncio.run(server.serve(port))
    except ImportError:
        print("Error: VAD service not found.")
        print("Options:")
        print("  1. Run from project root: cd inference/silero_vad && uv run python server.py")
        print("  2. Install: pip install vixio[silero-vad-local]")
        sys.exit(1)


def serve_asr(port: int = 50052) -> None:
    """Start ASR gRPC service"""
    inference_dir = get_inference_dir()
    
    if inference_dir:
        service_dir = os.path.join(inference_dir, 'sherpa_onnx_local')
        if os.path.exists(service_dir):
            print(f"Starting ASR service on port {port}...")
            print(f"Working directory: {service_dir}")
            try:
                subprocess.run(
                    ['uv', 'run', 'python', 'server.py', '--port', str(port)],
                    cwd=service_dir,
                    check=True
                )
            except FileNotFoundError:
                print("Error: uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh")
                sys.exit(1)
            except subprocess.CalledProcessError as e:
                print(f"Service exited with error: {e}")
                sys.exit(1)
            return
    
    # Try importing as installed package
    try:
        from vixio_sherpa_asr_service import server
        print(f"Starting ASR service on port {port}...")
        import asyncio
        asyncio.run(server.serve(port))
    except ImportError:
        print("Error: ASR service not found.")
        print("Options:")
        print("  1. Run from project root: cd inference/sherpa_onnx_local && uv run python server.py")
        print("  2. Install: pip install vixio[sherpa-asr-local]")
        sys.exit(1)


def serve_tts(port: int = 50053) -> None:
    """Start TTS gRPC service"""
    inference_dir = get_inference_dir()
    
    if inference_dir:
        service_dir = os.path.join(inference_dir, 'kokoro_cn_tts_local')
        if os.path.exists(service_dir):
            print(f"Starting TTS service on port {port}...")
            print(f"Working directory: {service_dir}")
            try:
                subprocess.run(
                    ['uv', 'run', 'python', 'server.py', '--port', str(port)],
                    cwd=service_dir,
                    check=True
                )
            except FileNotFoundError:
                print("Error: uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh")
                sys.exit(1)
            except subprocess.CalledProcessError as e:
                print(f"Service exited with error: {e}")
                sys.exit(1)
            return
    
    # Try importing as installed package
    try:
        from vixio_kokoro_tts_service import server
        print(f"Starting TTS service on port {port}...")
        import asyncio
        asyncio.run(server.serve(port))
    except ImportError:
        print("Error: TTS service not found.")
        print("Options:")
        print("  1. Run from project root: cd inference/kokoro_cn_tts_local && uv run python server.py")
        print("  2. Install: pip install vixio[kokoro-tts-local]")
        sys.exit(1)


def serve_all() -> None:
    """Start all services (not recommended for production)"""
    import multiprocessing
    
    print("Starting all services...")
    print("Note: For production, run each service separately.")
    
    processes = []
    
    # Start VAD
    p_vad = multiprocessing.Process(target=serve_vad, args=(50051,))
    p_vad.start()
    processes.append(("VAD", p_vad))
    
    # Start ASR
    p_asr = multiprocessing.Process(target=serve_asr, args=(50052,))
    p_asr.start()
    processes.append(("ASR", p_asr))
    
    # Start TTS
    p_tts = multiprocessing.Process(target=serve_tts, args=(50053,))
    p_tts.start()
    processes.append(("TTS", p_tts))
    
    print("\nServices started:")
    print("  - VAD: localhost:50051")
    print("  - ASR: localhost:50052")
    print("  - TTS: localhost:50053")
    print("\nPress Ctrl+C to stop all services.")
    
    try:
        for name, p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\nStopping services...")
        for name, p in processes:
            p.terminate()
            p.join()
        print("All services stopped.")


def list_services() -> None:
    """List available services"""
    print("Available services:")
    print("  - vad: Silero VAD (Voice Activity Detection)")
    print("  - asr: Sherpa-ONNX ASR (Speech Recognition)")
    print("  - tts: Kokoro TTS (Text to Speech)")
    print("")
    print("Usage:")
    print("  vixio serve vad --port 50051")
    print("  vixio serve asr --port 50052")
    print("  vixio serve tts --port 50053")
    print("  vixio serve all")
    print("")
    print("Alternative (from project root):")
    print("  cd inference/silero_vad && uv run python server.py --port 50051")


def run_server_command(args) -> int:
    """Run xiaozhi-server from package"""
    from vixio.xiaozhi.presets import get_preset_path, AVAILABLE_PRESETS
    
    # Get preset
    preset_name = args.preset
    
    try:
        preset_path = get_preset_path(preset_name)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    
    # Check API Key
    api_key = args.dashscope_key or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("Error: DashScope API Key not provided!")
        print("")
        print("Please provide API key via:")
        print("  1. Command line: --dashscope-key sk-xxx")
        print("  2. Environment variable: export DASHSCOPE_API_KEY=sk-xxx")
        print("")
        print("Get your API key from: https://dashscope.console.aliyun.com/")
        return 1
    
    # Set environment variables
    os.environ["DASHSCOPE_API_KEY"] = api_key
    
    # Disable LiteLLM model cost map download to avoid startup delay
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    
    # Set log mode (must be before importing vixio modules)
    if args.enable_logs:
        os.environ["VIXIO_LOG_MODE"] = "file"
    
    # Set prompt
    if args.prompt:
        os.environ["VIXIO_PROMPT"] = args.prompt
        print(f"   Using custom prompt (from --prompt)")
    elif args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.exists():
            print(f"Error: Prompt file not found: {prompt_path}")
            return 1
        with open(prompt_path, encoding='utf-8') as f:
            custom_prompt = f.read().strip()
            os.environ["VIXIO_PROMPT"] = custom_prompt
            print(f"   Using custom prompt from file: {prompt_path}")
            print(f"   Prompt preview: {custom_prompt[:80]}..." if len(custom_prompt) > 80 else f"   Prompt: {custom_prompt}")
    elif not os.getenv("VIXIO_PROMPT"):
        # Set default prompt
        default_prompt = (
            "你是一个友好的AI语音助手。用简洁自然的语气回答问题，像和朋友聊天一样。"
            "回答要简短，适合语音播放。总是先用一个简短的句子回答核心问题，以句号结束，不要用逗号。"
            "如果需要详细说明，再分成多个简短的句子。"
        )
        os.environ["VIXIO_PROMPT"] = default_prompt
    
    # Determine mode from preset
    if preset_name == "qwen-realtime":
        mode = "realtime"
    else:
        mode = "pipeline"
    
    print(f"Starting Xiaozhi Server...")
    print(f"   Preset: {preset_name}")
    print(f"   Mode:   {mode}")
    print(f"   Host:   {args.host}")
    print(f"   Port:   {args.port}")
    print(f"   Logs:   {'enabled (logs/)' if args.enable_logs else 'disabled (use --enable-logs to enable)'}")
    print("")
    
    # Import and run
    try:
        if mode == "realtime":
            from vixio.xiaozhi.runners import run_realtime_server
            asyncio.run(run_realtime_server(
                config_path=str(preset_path),
                env=None,  # Preset configs don't have env
                host=args.host,
                port=args.port,
                turn_timeout=args.turn_timeout,
                prompt=os.getenv("VIXIO_PROMPT"),
            ))
        else:
            from vixio.xiaozhi.runners import run_pipeline_server
            asyncio.run(run_pipeline_server(
                config_path=str(preset_path),
                env=None,  # Preset configs don't have env
                host=args.host,
                port=args.port,
                turn_timeout=args.turn_timeout,
                prompt=os.getenv("VIXIO_PROMPT"),
            ))
    except KeyboardInterrupt:
        print("\nShutting down...")
        return 0
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


def init_project_command(args) -> int:
    """Initialize project from template"""
    from vixio.xiaozhi.templates import get_template_path, AVAILABLE_TEMPLATES
    from vixio.xiaozhi.presets import get_preset_path, AVAILABLE_PRESETS
    
    template_name = args.template
    preset_name = args.preset
    output_dir = Path(args.output) if args.output else Path(f"./{template_name}")
    
    # Validate template
    try:
        template_path = get_template_path(template_name)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    
    # Validate preset
    try:
        preset_path = get_preset_path(preset_name)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    
    # Check if output directory exists
    if output_dir.exists():
        print(f"Error: Directory already exists: {output_dir}")
        return 1
    
    # Copy template
    print(f"Creating project from template...")
    print(f"   Template: {template_name}")
    print(f"   Preset:   {preset_name}")
    print(f"   Output:   {output_dir}")
    print("")
    
    try:
        shutil.copytree(template_path, output_dir)
        
        # Copy preset config to config.yaml
        shutil.copy(preset_path, output_dir / "config.yaml")
        
        print(f"Project created successfully!")
        print("")
        print("Next steps:")
        print(f"   cd {output_dir}")
        print(f"   cp .env.example .env")
        print(f"   # Edit .env and set DASHSCOPE_API_KEY")
        print(f"   # Edit prompt.txt (optional)")
        print(f"   # Edit config.yaml (optional)")
        print(f"   uv run python run.py")
        print("")
        print(f"Or run directly with:")
        print(f"   cd {output_dir}")
        print(f"   DASHSCOPE_API_KEY=sk-xxx uv run python run.py")
        
        return 0
        
    except Exception as e:
        print(f"Error creating project: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main(args: Optional[list] = None) -> int:
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog="vixio",
        description="Vixio - Voice-Powered Agent Framework"
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # run command - Quick start
    run_parser = subparsers.add_parser(
        "run",
        help="Run xiaozhi-server from package (quick start)"
    )
    run_parser.add_argument(
        "template",
        choices=["xiaozhi-server"],
        help="Template to run"
    )
    run_parser.add_argument(
        "--preset", "-p",
        choices=["qwen", "qwen-realtime"],
        default="qwen",
        help="Preset configuration (default: qwen)"
    )
    run_parser.add_argument(
        "--dashscope-key", "-k",
        help="DashScope API Key (or set DASHSCOPE_API_KEY env var)"
    )
    run_parser.add_argument(
        "--prompt",
        help="Custom system prompt"
    )
    run_parser.add_argument(
        "--prompt-file",
        help="Load prompt from file"
    )
    run_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Server host (default: 0.0.0.0)"
    )
    run_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port (default: 8000)"
    )
    run_parser.add_argument(
        "--turn-timeout",
        type=float,
        default=30.0,
        help="Turn timeout in seconds (default: 30.0, 0 to disable)"
    )
    run_parser.add_argument(
        "--enable-logs",
        action="store_true",
        help="Enable file logging to logs/ directory (disabled by default)"
    )
    
    # init command - Create project from template
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize project from template"
    )
    init_parser.add_argument(
        "template",
        choices=["xiaozhi-server"],
        help="Template name"
    )
    init_parser.add_argument(
        "--preset", "-p",
        choices=["qwen", "qwen-realtime"],
        default="qwen",
        help="Preset configuration (default: qwen)"
    )
    init_parser.add_argument(
        "--output", "-o",
        help="Output directory (default: ./<template>)"
    )
    
    # serve command - Start inference services
    serve_parser = subparsers.add_parser("serve", help="Start inference services")
    serve_parser.add_argument(
        "service",
        nargs="?",
        choices=["vad", "asr", "tts", "all"],
        help="Service to start (vad/asr/tts/all)"
    )
    serve_parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        help="Port number"
    )
    serve_parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available services"
    )
    
    parsed_args = parser.parse_args(args)
    
    # Handle --version
    if parsed_args.version:
        print(f"vixio {get_version()}")
        return 0
    
    # Handle commands
    if parsed_args.command == "run":
        return run_server_command(parsed_args)
    
    elif parsed_args.command == "init":
        return init_project_command(parsed_args)
    
    elif parsed_args.command == "serve":
        if parsed_args.list or not parsed_args.service:
            list_services()
            return 0
        
        service = parsed_args.service
        
        if service == "vad":
            port = parsed_args.port or 50051
            serve_vad(port)
        elif service == "asr":
            port = parsed_args.port or 50052
            serve_asr(port)
        elif service == "tts":
            port = parsed_args.port or 50053
            serve_tts(port)
        elif service == "all":
            serve_all()
        
        return 0
    
    # No command specified
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
