#!/bin/sh
# Ensure config directory has correct permissions for the app user
chown -R appuser:appuser /config 2>/dev/null || true

# Switch to non-root user and run the application
exec gosu appuser uvicorn main:app --host 0.0.0.0 --port 6100
