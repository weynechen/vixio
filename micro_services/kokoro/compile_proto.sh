#!/bin/bash
# Compile Kokoro TTS proto file

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Compiling Kokoro TTS proto..."

# Compile TTS proto
uv run python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    tts.proto

echo "✅ Proto file compiled"

# Fix imports to use relative imports
echo "Fixing imports..."
sed -i 's/^import tts_pb2 as tts__pb2$/from . import tts_pb2 as tts__pb2/' tts_pb2_grpc.py

echo "Generated files:"
echo "  - tts_pb2.py"
echo "  - tts_pb2_grpc.py (fixed imports)"

