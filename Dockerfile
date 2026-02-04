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

# Install gosu for proper user switching, ffmpeg for stream probing, and create non-root user
RUN apt-get update && apt-get install -y --no-install-recommends gosu ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies
# Note: Build tools needed for ARM64 where some packages lack pre-built wheels
COPY backend/requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libffi-dev \
        cargo \
        rustc \
    && uv pip install --system --no-cache -r requirements.txt \
    && apt-get purge -y build-essential python3-dev libffi-dev cargo rustc \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.cargo

# Copy backend code
COPY backend/ ./

# Copy built frontend to static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Create config and TLS directories with proper permissions
# Convert entrypoint line endings (handles Windows CRLF -> Unix LF)
RUN mkdir -p /config /config/tls \
    && chown -R appuser:appuser /config /app \
    && chmod 700 /config/tls \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

# Environment
ENV CONFIG_DIR=/config

# Expose ports (HTTP on 6100, HTTPS on 6143 when TLS enabled)
EXPOSE 6100 6143

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6100/api/health')" || exit 1

# Entrypoint fixes volume permissions then drops to non-root user via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
