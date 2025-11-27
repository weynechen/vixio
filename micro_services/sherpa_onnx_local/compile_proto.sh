#!/bin/bash
# Compile Sherpa ONNX ASR proto file

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Compiling Sherpa ONNX ASR proto..."

# Compile ASR proto
uv run python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    asr.proto

echo "✅ Proto file compiled"

# Fix imports to use relative imports
echo "Fixing imports..."
sed -i 's/^import asr_pb2 as asr__pb2$/from . import asr_pb2 as asr__pb2/' asr_pb2_grpc.py

echo "Generated files:"
echo "  - asr_pb2.py"
echo "  - asr_pb2_grpc.py (fixed imports)"

