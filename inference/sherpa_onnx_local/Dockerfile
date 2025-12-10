# Sherpa ONNX ASR Service Dockerfile
# Uses independent pyproject.toml for dependency isolation

FROM python:3.12-slim

WORKDIR /app/sherpa_onnx_local

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Copy service files (including pyproject.toml)
COPY inference/sherpa_onnx_local /app/sherpa_onnx_local

# Install dependencies using service's own pyproject.toml
RUN uv sync --no-dev

# Compile proto files
RUN uv run python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    asr.proto && \
    sed -i 's/^import asr_pb2 as asr__pb2$/from . import asr_pb2 as asr__pb2/' asr_pb2_grpc.py

# Copy models directory (if building with models included)
# COPY models /app/models

# Expose gRPC port
EXPOSE 50052

# Run service with uv
CMD ["uv", "run", "python", "server.py", "--port", "50052", "--log-level", "INFO"]

