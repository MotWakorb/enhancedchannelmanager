FROM python:3.12-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

WORKDIR /app

# Apply Alpine security updates that may have landed after the pinned base
# manifest was published. The digest makes the input reviewable; this upgrade
# step closes fixed OS advisories until the next intentional digest refresh.
# Alpine v3.24 secdb requires libuuid 2.42.3-r1 for CVE-2026-78408.
# Its vendor backport fixes CVE-2026-107891 in both musl packages at 1.2.6-r3.
RUN apk upgrade --no-cache \
    && apk add --no-cache \
        'libcrypto3>=3.5.8-r0' \
        'libssl3>=3.5.8-r0' \
        'libuuid>=2.42.3-r1' \
        'musl>=1.2.6-r3' \
        'musl-utils>=1.2.6-r3' \
    && addgroup -g 1000 appgroup \
    && adduser -D -u 1000 -G appgroup appuser

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .
RUN chown -R appuser:appgroup /app

# Environment
ENV MCP_SECRETS_DIR=/run/secrets/ecm-mcp
ENV ECM_URL=http://ecm:6100
ENV MCP_PORT=6101
ENV MCP_BIND_ADDRESS=127.0.0.1
ENV HOME=/tmp

EXPOSE 6101

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6101/health')" || exit 1

USER appuser:appgroup

CMD ["python", "server.py"]
