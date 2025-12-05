"""
Vixio CLI - Command Line Interface

Usage:
    vixio serve vad --port 50051
    vixio serve asr --port 50052
    vixio serve tts --port 50053
    vixio serve all
    vixio --version

Note:
    Services are located in the inference/ directory (workspace members).
    Run them with: uv run --package <service-name> python server.py
    Or: cd inference/<service> && uv run python server.py
"""

import argparse
import os
import subprocess
import sys
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
    
    # serve command
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
    if parsed_args.command == "serve":
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
