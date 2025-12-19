#!/bin/bash
# Type check script with exclusions

echo "Running type check on core code..."
echo ""

uvx ty check \
    --exclude "**/*pb2*.py" \
    --exclude "inference/**" \
    --exclude "**/*client.py" \
    --exclude ".venv/**" \
    "$@"
