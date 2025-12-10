# Kokoro TTS Service Dockerfile
# Uses independent pyproject.toml for dependency isolation

FROM python:3.12-slim

WORKDIR /app/kokoro_cn_tts_local

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Copy service files (including pyproject.toml)
COPY inference/kokoro_cn_tts_local /app/kokoro_cn_tts_local

# Install dependencies using service's own pyproject.toml
RUN uv sync --no-dev

# Compile proto files
RUN uv run python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    tts.proto && \
    sed -i 's/^import tts_pb2 as tts__pb2$/from . import tts_pb2 as tts__pb2/' tts_pb2_grpc.py

# Expose gRPC port
EXPOSE 50053

# Run service with uv
CMD ["uv", "run", "python", "server.py", "--port", "50053", "--log-level", "INFO"]
