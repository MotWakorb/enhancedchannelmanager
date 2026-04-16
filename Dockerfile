# Build frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

# Cache busting - invalidate cache when git commit changes
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

COPY frontend/ ./
RUN npm run build

# Build Python dependencies in a separate stage to reduce peak memory
# ARM64 needs build tools + Rust for packages like cryptography
FROM python:3.12-slim AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install build tools in their own layer (cached separately from pip install)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libffi-dev \
        cargo \
        rustc \
    && rm -rf /var/lib/apt/lists/*

# Compile Python packages into a virtual env we can copy to the final image
COPY backend/requirements.txt /tmp/requirements.txt
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache -r /tmp/requirements.txt

# Production image
FROM python:3.12-slim

# Build args - MUST be declared early in the stage to receive build arg
ARG GIT_COMMIT=unknown
ARG ECM_VERSION=unknown
ARG RELEASE_CHANNEL=latest
ENV GIT_COMMIT=$GIT_COMMIT
ENV ECM_VERSION=$ECM_VERSION
ENV RELEASE_CHANNEL=$RELEASE_CHANNEL

WORKDIR /app

# Install gosu for proper user switching, ffmpeg for stream probing, and create non-root user.
# apt-get upgrade pulls in Debian security updates (e.g. openssl CVE fixes) that the base image lags behind on.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends gosu ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

# Copy pre-built Python packages from builder stage (no build tools needed)
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy backend code
COPY backend/ ./

# Copy built frontend to static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Create config and TLS directories with proper permissions
# Convert entrypoint line endings (handles Windows CRLF -> Unix LF)
RUN mkdir -p /config /config/tls /config/uploads/logos \
    && chown -R appuser:appuser /config /app \
    && chmod 700 /config/tls \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

# Environment
ENV PUID=1000
ENV PGID=1000
ENV CONFIG_DIR=/config
ENV ECM_PORT=6100
ENV ECM_HTTPS_PORT=6143

# Expose default ports (HTTP: 6100, HTTPS: 6143)
# Note: Actual ports are configurable at runtime via ECM_PORT and ECM_HTTPS_PORT.
EXPOSE 6100 6143

# Add healthcheck (respects runtime ECM_PORT)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request, os; port = os.environ.get('ECM_PORT', '6100'); urllib.request.urlopen(f'http://localhost:{port}/api/health')" || exit 1

# Entrypoint sets UID/GID from PUID/PGID, fixes permissions, then drops to non-root via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
