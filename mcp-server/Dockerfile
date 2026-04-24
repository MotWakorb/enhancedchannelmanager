FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Environment
ENV CONFIG_DIR=/config
ENV ECM_URL=http://ecm:6100
ENV MCP_PORT=6101

EXPOSE 6101

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6101/health')" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "6101"]
