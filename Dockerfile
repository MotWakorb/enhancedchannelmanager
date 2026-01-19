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

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./

# Copy built frontend to static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Create config directory and set ownership
# Convert entrypoint line endings (handles Windows CRLF -> Unix LF)
RUN mkdir -p /config && chown -R appuser:appuser /config /app \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

# Environment
ENV CONFIG_DIR=/config

# Expose port
EXPOSE 6100

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6100/api/health')" || exit 1

# Entrypoint fixes volume permissions then drops to non-root user via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
