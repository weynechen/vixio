#!/bin/bash
# Compile Silero VAD proto file
#
# IMPORTANT: After running this script, you must manually copy the generated
# files to the vixio providers directory to keep client/server in sync:
#
#   cp vad_pb2.py vad_pb2_grpc.py ../../src/vixio/providers/silero_vad/
#
# The client.py in providers/ may also need updates if the proto interface changed.

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

# Fix imports to support both package and script mode
echo "Fixing imports..."
# Replace absolute import with try-except pattern
python3 << 'EOF'
with open('vad_pb2_grpc.py', 'r') as f:
    content = f.read()

# Replace import line with try-except pattern
content = content.replace(
    'import vad_pb2 as vad__pb2',
    '''try:
    from . import vad_pb2 as vad__pb2
except ImportError:
    import vad_pb2 as vad__pb2'''
)

with open('vad_pb2_grpc.py', 'w') as f:
    f.write(content)
EOF

echo "Generated files:"
echo "  - vad_pb2.py"
echo "  - vad_pb2_grpc.py"

