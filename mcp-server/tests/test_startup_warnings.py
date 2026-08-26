"""Regression tests for warnings emitted while constructing the MCP server."""

import os
import subprocess
import sys
from pathlib import Path

MCP_SERVER_DIR = Path(__file__).resolve().parents[1]


def test_import_does_not_emit_incomplete_field_definition_warning(tmp_path) -> None:
    completed = subprocess.run(
        [sys.executable, "-W", "always", "-c", "import server"],
        cwd=MCP_SERVER_DIR,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "MCP_SECRETS_DIR": str(tmp_path),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert "IncompleteFieldDefinitionWarning" not in completed.stderr
