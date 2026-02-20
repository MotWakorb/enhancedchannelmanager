"""
Dynamic HTTPS server management.

Manages the HTTPS server as a subprocess, allowing hot enable/disable
of TLS without requiring a container restart.
"""
import asyncio
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .settings import get_tls_settings, TLS_DIR
from .storage import CertificateStorage

logger = logging.getLogger(__name__)


class HTTPSServerManager:
    """
    Manages the HTTPS server subprocess.

    The HTTP server (port 6100) is always the main process.
    When TLS is enabled, this manager spawns a subprocess running
    uvicorn with SSL on the configured HTTPS port.
    """

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._port: int = 6143
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Check if HTTPS server is running."""
        if self._process is None:
            return False
        # Check if process is still alive
        return self._process.poll() is None

    @property
    def port(self) -> int:
        """Get the HTTPS port."""
        return self._port

    def _get_uvicorn_command(self, port: int, cert_path: Path, key_path: Path) -> list[str]:
        """Build the uvicorn command for HTTPS server.

        All inputs are validated before use:
        - port: must be int in 1-65535
        - cert/key paths: must resolve within TLS_DIR
        """
        # Validate port — reject non-int and out-of-range
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError(f"Invalid port: {port}")
        # Convert validated port to string (breaks CodeQL taint flow)
        safe_port = str(int(port))

        cert_resolved = cert_path.resolve()
        key_resolved = key_path.resolve()
        tls_dir_resolved = TLS_DIR.resolve()
        if not str(cert_resolved).startswith(str(tls_dir_resolved)):
            raise ValueError("Certificate path outside TLS directory")
        if not str(key_resolved).startswith(str(tls_dir_resolved)):
            raise ValueError("Key path outside TLS directory")

        return [
            sys.executable, "-m", "uvicorn",
            "main:app",
            "--host", "0.0.0.0",
            "--port", safe_port,
            "--ssl-keyfile", str(key_resolved),
            "--ssl-certfile", str(cert_resolved),
        ]

    def _get_subprocess_env(self) -> dict:
        """Get environment variables for the subprocess."""
        env = os.environ.copy()
        # Mark this as the HTTPS subprocess to prevent recursive spawning
        env["ECM_HTTPS_SUBPROCESS"] = "1"
        return env

    async def start(self) -> tuple[bool, Optional[str]]:
        """
        Start the HTTPS server if TLS is properly configured.

        Returns:
            Tuple of (success, error_message)
        """
        async with self._lock:
            if self.is_running:
                logger.debug("[TLS-SERVER] HTTPS server already running")
                return True, None

            settings = get_tls_settings()

            # Check if TLS is enabled
            if not settings.enabled:
                return False, "TLS is not enabled"

            # Check if certificates exist
            storage = CertificateStorage(TLS_DIR)
            if not storage.has_certificate():
                return False, "No valid certificate found"

            cert_path = TLS_DIR / "cert.pem"
            key_path = TLS_DIR / "key.pem"

            if not cert_path.exists() or not key_path.exists():
                return False, "Certificate files not found"

            self._port = settings.https_port

            try:
                # Build command
                cmd = self._get_uvicorn_command(self._port, cert_path, key_path)
                env = self._get_subprocess_env()

                logger.info("[TLS-SERVER] Starting HTTPS server on port %s", self._port)

                # Start subprocess
                # Set working directory to /app where main.py is located
                work_dir = Path(__file__).parent.parent  # backend directory

                # Use the env we already prepared
                self._process = subprocess.Popen(
                    cmd,
                    cwd=str(work_dir),
                    # Don't capture output - let it go to container logs
                    env=env,
                    # Don't let the subprocess inherit our signal handlers
                    start_new_session=True,
                )

                # Give it a moment to start
                await asyncio.sleep(1)

                # Check if it's still running
                if self._process.poll() is not None:
                    # Process died
                    exit_code = self._process.returncode
                    logger.error("[TLS-SERVER] HTTPS server failed to start (exit code: %s)", exit_code)
                    self._process = None
                    return False, f"Server failed to start (exit code: {exit_code})"

                logger.info("[TLS-SERVER] HTTPS server started successfully on port %s (PID: %s)", self._port, self._process.pid)
                return True, None

            except Exception as e:
                logger.error("[TLS-SERVER] Failed to start HTTPS server: %s", e)
                self._process = None
                return False, str(e)

    async def stop(self) -> bool:
        """
        Stop the HTTPS server.

        Returns:
            True if stopped successfully, False if wasn't running
        """
        async with self._lock:
            if not self.is_running:
                logger.debug("[TLS-SERVER] HTTPS server not running")
                return False

            try:
                logger.info("[TLS-SERVER] Stopping HTTPS server (PID: %s)", self._process.pid)

                # Send SIGTERM for graceful shutdown
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)

                # Wait for it to terminate
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't respond
                    logger.warning("[TLS-SERVER] HTTPS server didn't stop gracefully, forcing kill")
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                    self._process.wait(timeout=5)

                logger.info("[TLS-SERVER] HTTPS server stopped")
                self._process = None
                return True

            except Exception as e:
                logger.error("[TLS-SERVER] Error stopping HTTPS server: %s", e)
                self._process = None
                return False

    async def restart(self) -> tuple[bool, Optional[str]]:
        """
        Restart the HTTPS server (e.g., after certificate renewal).

        Returns:
            Tuple of (success, error_message)
        """
        await self.stop()
        await asyncio.sleep(1)  # Brief pause between stop and start
        return await self.start()

    def get_status(self) -> dict:
        """Get current HTTPS server status."""
        return {
            "running": self.is_running,
            "port": self._port,
            "pid": self._process.pid if self._process else None,
        }


# Global instance
https_server_manager = HTTPSServerManager()


def is_https_subprocess() -> bool:
    """Check if we're running as the HTTPS subprocess."""
    return os.environ.get("ECM_HTTPS_SUBPROCESS") == "1"


async def start_https_if_configured() -> None:
    """
    Start HTTPS server if TLS is configured and enabled.

    Called during application startup.
    """
    # Don't start HTTPS subprocess from within the HTTPS subprocess
    # (prevents recursive spawning)
    if is_https_subprocess():
        logger.debug("[TLS-SERVER] Running as HTTPS subprocess, skipping HTTPS server spawn")
        return

    settings = get_tls_settings()

    if not settings.enabled:
        logger.debug("[TLS-SERVER] TLS not enabled, HTTPS server not started")
        return

    storage = CertificateStorage(TLS_DIR)
    if not storage.has_certificate():
        logger.warning("[TLS-SERVER] TLS enabled but no certificate found, HTTPS server not started")
        return

    success, error = await https_server_manager.start()
    if not success:
        logger.warning("[TLS-SERVER] Failed to start HTTPS server: %s", error)


async def stop_https_server() -> None:
    """
    Stop HTTPS server.

    Called during application shutdown.
    """
    await https_server_manager.stop()
