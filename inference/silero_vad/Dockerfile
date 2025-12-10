# Silero VAD Service Dockerfile
# Uses independent pyproject.toml for dependency isolation

FROM python:3.12-slim

WORKDIR /app/silero_vad

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Copy service files (including pyproject.toml)
COPY inference/silero_vad /app/silero_vad

# Install dependencies using service's own pyproject.toml
RUN uv sync --no-dev

# Compile proto files
RUN uv run python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    vad.proto && \
    sed -i 's/^import vad_pb2 as vad__pb2$/from . import vad_pb2 as vad__pb2/' vad_pb2_grpc.py

# Expose gRPC port
EXPOSE 50051

# Run service with uv
CMD ["uv", "run", "python", "server.py", "--port", "50051", "--log-level", "INFO"]
