#!/bin/bash
# Setup development environment for microservices
# Installs dependencies and starts each service

set -e

cd "$(dirname "$0")/../.."
PROJECT_ROOT=$(pwd)

echo "🔧 Setting up microservices development environment..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Please install: pip install uv"
    exit 1
fi

echo ""
echo "📦 Installing Silero VAD service dependencies..."
cd "$PROJECT_ROOT/inference/silero_vad"
uv sync
echo "✅ Silero VAD dependencies installed"
echo "🚀 Starting Silero VAD server..."
uv run vixio-vad-server &
VAD_PID=$!
echo "   VAD server started (PID: $VAD_PID)"

echo ""
echo "📦 Installing Sherpa ONNX ASR service dependencies..."
cd "$PROJECT_ROOT/inference/sherpa_onnx_local"
uv sync
echo "✅ Sherpa ONNX ASR dependencies installed"
echo "🚀 Starting Sherpa ONNX ASR server..."
uv run vixio-asr-server &
ASR_PID=$!
echo "   ASR server started (PID: $ASR_PID)"

echo ""
echo "📦 Installing Kokoro TTS service dependencies..."
cd "$PROJECT_ROOT/inference/kokoro_cn_tts_local"
uv sync
echo "✅ Kokoro TTS dependencies installed"
echo "🚀 Starting Kokoro TTS server..."
uv run vixio-tts-server &
TTS_PID=$!
echo "   TTS server started (PID: $TTS_PID)"

echo ""
echo "============================================================"
echo "✅ All microservices installed and started!"
echo "============================================================"
echo ""
echo "Running services:"
echo "   VAD server PID: $VAD_PID"
echo "   ASR server PID: $ASR_PID"
echo "   TTS server PID: $TTS_PID"
echo ""
echo "🎯 Next steps:"
echo "   uv run python examples/agent_chat.py --env dev"
echo ""
echo "To stop all services:"
echo "   kill $VAD_PID $ASR_PID $TTS_PID"
echo ""

# Wait for all background processes
wait
