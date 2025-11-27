#!/bin/bash
# Setup development environment for microservices
# Installs dependencies for each service independently

set -e

cd "$(dirname "$0")/../.."

echo "🔧 Setting up microservices development environment..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Please install: pip install uv"
    exit 1
fi

echo ""
echo "📦 Installing Silero VAD service dependencies..."
cd micro_services/silero_vad
uv sync
cd ../..
echo "✅ Silero VAD dependencies installed"

echo ""
echo "📦 Installing Sherpa ONNX ASR service dependencies..."
cd micro_services/sherpa_onnx_local
uv sync
cd ../..
echo "✅ Sherpa ONNX ASR dependencies installed"

echo ""
echo "📦 Installing Kokoro TTS service dependencies..."
cd micro_services/kokoro
uv sync
cd ../..
echo "✅ Kokoro TTS dependencies installed"

echo ""
echo "="*60
echo "✅ All microservice dependencies installed!"
echo "="*60
echo ""
echo "🎯 Next steps:"
echo "   ./scripts/dev/start-all.sh"
echo ""

