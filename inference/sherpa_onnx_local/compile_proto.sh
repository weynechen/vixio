#!/bin/bash
# Compile Sherpa ONNX ASR proto file
#
# IMPORTANT: After running this script, you must manually copy the generated
# files to the vixio providers directory to keep client/server in sync:
#
#   cp asr_pb2.py asr_pb2_grpc.py ../../src/vixio/providers/sherpa_onnx_local/
#
# The client.py in providers/ may also need updates if the proto interface changed.

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

# Fix imports to support both package and script mode
echo "Fixing imports..."
# Replace absolute import with try-except pattern
python3 << 'EOF'
with open('asr_pb2_grpc.py', 'r') as f:
    content = f.read()

# Replace import line with try-except pattern
content = content.replace(
    'import asr_pb2 as asr__pb2',
    '''try:
    from . import asr_pb2 as asr__pb2
except ImportError:
    import asr_pb2 as asr__pb2'''
)

with open('asr_pb2_grpc.py', 'w') as f:
    f.write(content)
EOF

echo "Generated files:"
echo "  - asr_pb2.py"
echo "  - asr_pb2_grpc.py"

