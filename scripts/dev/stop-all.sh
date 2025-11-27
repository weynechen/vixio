#!/bin/bash
# Stop all development services

cd "$(dirname "$0")/../.."

echo "🛑 Stopping Vixio development services..."

# Stop VAD service
if [ -f .dev_vad.pid ]; then
    VAD_PID=$(cat .dev_vad.pid)
    if ps -p $VAD_PID > /dev/null 2>&1; then
        kill $VAD_PID 2>/dev/null && echo "✅ Stopped VAD service (PID: $VAD_PID)"
        sleep 1
        # Force kill if still running
        if ps -p $VAD_PID > /dev/null 2>&1; then
            kill -9 $VAD_PID 2>/dev/null && echo "   (force killed)"
        fi
    else
        echo "⚠️  VAD service not running (PID: $VAD_PID)"
    fi
    rm .dev_vad.pid
else
    echo "⚠️  No VAD service PID file found"
fi

echo ""
echo "✅ All development services stopped"

