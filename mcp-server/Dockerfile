FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

WORKDIR /app

# Apply Alpine security updates that may have landed after the pinned base
# manifest was published. The digest makes the input reviewable; this upgrade
# step closes fixed OS advisories until the next intentional digest refresh.
RUN apk upgrade --no-cache \
    && addgroup -g 1000 appgroup \
    && adduser -D -u 1000 -G appgroup appuser

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .
RUN chown -R appuser:appgroup /app

# Environment
ENV CONFIG_DIR=/config
ENV ECM_URL=http://ecm:6100
ENV MCP_PORT=6101
ENV MCP_BIND_ADDRESS=127.0.0.1
ENV HOME=/tmp

EXPOSE 6101

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6101/health')" || exit 1

USER appuser:appgroup

CMD ["python", "server.py"]
