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

# Keep Python, native builders and runtime on the same reviewed glibc package
# graph. Wolfi supplies the FFmpeg/libxml2 fixes unavailable in Debian stable.
FROM cgr.dev/chainguard/wolfi-base@sha256:f15fea2f49cb14dceaea491fcbde0d35e0c190126a744ac05a9838440d47d994 AS runtime-base
RUN apk upgrade --no-cache \
    && apk add --no-cache python-3.12 'ffmpeg-9.0>=9.0.2-r5' 'libxml2-16>=2.15.4-r1' \
        gosu shadow coreutils bash tzdata ca-certificates-bundle posix-libc-utils

# Install the locked Python dependencies without carrying build tools at runtime.
FROM runtime-base AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /usr/local/bin/uv

# Compile Python packages into a virtual env we can copy to the final image
COPY backend/requirements.txt /tmp/requirements.txt
RUN uv venv --python /usr/bin/python3.12 /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache -r /tmp/requirements.txt

# Build resdet from a pinned source commit for both supported image architectures.
FROM runtime-base AS resdet-builder
COPY scripts/generate_sbom.py scripts/build_resdet.py sbom/native-dependencies.json /tmp/
RUN apk add --no-cache build-base pkgconf gawk \
    && python3.12 /tmp/build_resdet.py --manifest /tmp/native-dependencies.json --work-dir /tmp/resdet-work --output-dir /tmp/resdet-output

# Production image
FROM runtime-base

# Build args - MUST be declared early in the stage to receive build arg
ARG GIT_COMMIT=unknown
ARG ECM_VERSION=unknown
ARG RELEASE_CHANNEL=latest
ENV GIT_COMMIT=$GIT_COMMIT
ENV ECM_VERSION=$ECM_VERSION
ENV RELEASE_CHANNEL=$RELEASE_CHANNEL

WORKDIR /app

# Explicit identity avoids distribution-specific useradd defaults. Entrypoint
# still remaps this account to the configured PUID/PGID before dropping root.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /bin/bash appuser

# Copy pre-built Python packages from builder stage (no build tools needed)
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy backend code
COPY backend/ ./

# Copy built frontend to static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Create config and TLS directories with proper permissions
# Convert entrypoint line endings (handles Windows CRLF -> Unix LF)
RUN mkdir -p /config /config/tls /config/uploads/logos /run/ecm \
    && chown -R appuser:appuser /config /app \
    && chown appuser:appuser /run/ecm \
    && chmod 700 /run/ecm \
    && chmod 700 /config/tls \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

# Copy immutable native outputs after every other filesystem-mutating final-stage step.
COPY --from=resdet-builder /tmp/resdet-output/resdet /usr/local/bin/resdet
COPY --from=resdet-builder /tmp/resdet-output/licenses/ /usr/share/doc/resdet/
RUN test "$(resdet -V)" = "$(printf 'resdet version 2.4.3\nlibresdet version 3.2.0\nBuilt with image readers: Y4M')"

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
