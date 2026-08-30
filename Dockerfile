# Build frontend
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

# Cache busting - invalidate cache when git commit changes
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

COPY frontend/ ./
RUN npm run build

# Build Python dependencies in a separate stage to reduce peak memory
# ARM64 needs build tools + Rust for packages like cryptography
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /usr/local/bin/uv

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
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

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

# Add healthcheck (respects runtime ECM_PORT).
# The interpreter is the absolute venv path, not a bare `python` resolved
# through PATH (bead enhancedchannelmanager-0oi96): a runtime PATH override
# would otherwise silently move the healthcheck onto the system interpreter.
# urllib is stdlib, so this happens to survive that today — pinning it keeps
# the healthcheck honest if it ever grows a dependency, and keeps every
# interpreter invocation in the image consistent.
# Long-running installs may hit slow first-run migrations against bloated
# SQLite WAL files. WAL checkpoint at startup (bd-ej995) addresses the
# common case; this start-period absorbs the edge case where a particularly
# large migration still runs longer than the default. Operators on installs
# with consistent fast startups can lower this; the default favors safety
# over startup time.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD /opt/venv/bin/python -c "import urllib.request, os; port = os.environ.get('ECM_PORT', '6100'); urllib.request.urlopen(f'http://localhost:{port}/api/health')" || exit 1

# Entrypoint sets UID/GID from PUID/PGID, fixes permissions, then drops to non-root via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
