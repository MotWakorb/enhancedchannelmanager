FROM python:3.14-alpine@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc

WORKDIR /app

# Apply Alpine security updates that may have landed after the pinned base
# manifest was published. The digest makes the input reviewable; this upgrade
# step closes fixed OS advisories until the next intentional digest refresh.
RUN apk upgrade --no-cache \
    && apk add --no-cache \
        'libcrypto3>=3.5.8-r0' \
        'libssl3>=3.5.8-r0' \
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
