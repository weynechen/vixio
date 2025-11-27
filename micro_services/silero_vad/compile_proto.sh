#!/bin/bash
# Compile Silero VAD proto file

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Compiling Silero VAD proto..."

# Compile VAD proto
uv run python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    vad.proto

echo "✅ Proto file compiled"

# Fix imports to use relative imports
echo "Fixing imports..."
sed -i 's/^import vad_pb2 as vad__pb2$/from . import vad_pb2 as vad__pb2/' vad_pb2_grpc.py

echo "Generated files:"
echo "  - vad_pb2.py"
echo "  - vad_pb2_grpc.py (fixed imports)"

