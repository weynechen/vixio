#!/bin/bash
# Development mode: Start all services without Docker
# Uses uv run to launch services as local processes

set -e

cd "$(dirname "$0")/../.."

echo "🚀 Starting Vixio services in development mode..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Please install: pip install uv"
    exit 1
fi

# Create logs directory
mkdir -p logs

# Start Silero VAD service (with its own dependencies)
echo "📡 Starting Silero VAD service..."
cd micro_services/silero_vad
uv run --project . python server.py > ../../logs/silero_vad.log 2>&1 &
VAD_PID=$!
cd ../..
echo "   VAD service PID: $VAD_PID"

# Wait a bit for VAD to start
sleep 2

# Check if VAD service is running
if ! ps -p $VAD_PID > /dev/null; then
    echo "❌ VAD service failed to start. Check logs/vad.log"
    exit 1
fi

echo "✅ Services started:"
echo "   Silero VAD: localhost:50051 (PID: $VAD_PID)"
echo ""
echo "📝 Logs:"
echo "   tail -f logs/silero_vad.log"
echo ""
echo "🧪 Test VAD service:"
echo "   uv run python tests/test_vad_simple.py"
echo ""
echo "🎯 Start main service:"
echo "   uv run python examples/agent_chat.py"
echo ""
echo "🛑 Stop services:"
echo "   ./scripts/dev/stop-all.sh"

# Save PIDs
echo $VAD_PID > .dev_vad.pid

echo ""
echo "✨ Development environment ready!"

