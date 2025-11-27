#!/bin/bash
# Cleanup orphaned microservice processes
# Use this if agent_chat.py crashed and left services running

set -e

echo "🧹 Cleaning up orphaned microservices..."
echo ""

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Function to kill processes by pattern
kill_by_pattern() {
    local pattern=$1
    local name=$2
    
    pids=$(ps aux | grep "$pattern" | grep -v grep | awk '{print $2}')
    
    if [ -n "$pids" ]; then
        echo "  Stopping $name (PIDs: $pids)..."
        echo "$pids" | xargs kill -TERM 2>/dev/null || true
        sleep 0.5
        
        # Force kill if still running
        pids=$(ps aux | grep "$pattern" | grep -v grep | awk '{print $2}')
        if [ -n "$pids" ]; then
            echo "  Force stopping $name..."
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
        
        echo "  ✓ $name stopped"
    else
        echo "  ✓ $name not running"
    fi
}

# Kill microservices (match by directory and server.py separately)
kill_by_pattern "silero_vad.*server.py" "Silero VAD"
kill_by_pattern "sherpa_onnx_local.*server.py" "Sherpa ONNX ASR"
kill_by_pattern "kokoro.*server.py" "Kokoro TTS"

# Kill agent_chat.py if running
kill_by_pattern "agent_chat.py" "Agent Chat Server"

echo ""
echo "✅ Cleanup complete!"
echo ""
echo "You can now restart the services with:"
echo "  uv run examples/agent_chat.py --env dev"

