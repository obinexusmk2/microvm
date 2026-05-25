# MicroVM CLI - Python 3.10+ containerized
# Multi-stage build for minimal production image

# Stage 1: Builder
FROM python:3.10-slim AS builder

WORKDIR /build

# Install build dependencies (minimal for setuptools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY microvm/ ./microvm/

# Build the package
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install --user --no-warn-script-location .

# Stage 2: Runtime
FROM python:3.10-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r microvm && useradd -r -g microvm microvm

# Copy only runtime dependencies from builder
COPY --from=builder --chown=microvm:microvm /root/.local /home/microvm/.local

# Set PATH to include user-installed packages
ENV PATH=/home/microvm/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER microvm

# Default entrypoint
ENTRYPOINT ["microvm"]
CMD ["--help"]
