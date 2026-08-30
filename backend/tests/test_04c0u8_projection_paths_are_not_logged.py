"""No log line may carry a value derived from the ``MCP_SECRETS_DIR`` read.

Bead enhancedchannelmanager-04c0u.8. CodeQL's
``py/clear-text-logging-sensitive-data`` treats
``os.environ.get("MCP_SECRETS_DIR", ...)`` as a sensitive source — the variable
name matches its sensitive-identifier heuristic — so every path derived from it
(``MCP_SECRETS_DIR``, ``MCP_KEY_FILE``, ``MCP_SERVICE_FILE``, and the ``path``
parameter those are passed to) taints any logger it reaches.

This class has now recurred once: alert 1894 was remediated in
``mcp-server/config.py`` by logging the constant filename and the environment
variable name instead of the resolved path, and the next round re-introduced it
through *new* log lines (the traceback latch, the 503 handler, the startup
warnings) — seven fresh alerts. Pinning the seven instances would not have
caught that. These tests pin the property.

**The invariant.** No ``logger.*`` call in the backend modules that touch the
MCP projection may interpolate a value derived from the ``MCP_SECRETS_DIR``
environment read. What the operator can act on — the constant filename and the
name of the variable they configured — is logged instead.

**What this does NOT claim.** ``logger.exception`` emits a traceback, and an
``OSError``'s own text ends with the filename it failed on. The latched
projection-health traceback is a structured diagnostic rather than a composed
message, CodeQL does not model exception-attribute propagation, and it is
emitted at most once per unhealthy episode by design (see
``TestDegradedModeDoesNotLogPerRequest``), so it is out of scope here. Explicit
settings-save failures are not latched, so the behavioural assertion below
requires that sink to omit both the exception object and traceback.
"""

import ast
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import config
from auth.mcp_service import (
    load_mcp_service_credentials,
    reset_mcp_projection_failure_log_latch,
)

BACKEND_DIR = Path(config.__file__).resolve().parent

# The module-level constants CodeQL's taint reaches from the environment read.
TAINTED_ROOTS = frozenset({"MCP_SECRETS_DIR", "MCP_KEY_FILE", "MCP_SERVICE_FILE"})

# ``auth/mcp_service.py`` never imports those constants: the projection path
# arrives as the ``path`` parameter of every public function in the module, so
# ``path`` is the same tainted value under a local name.
EXTRA_TAINTED_NAMES = {"auth/mcp_service.py": frozenset({"path"})}

# Every backend module that resolves, writes, or reports on the projection.
PROJECTION_MODULES = (
    "config.py",
    "main.py",
    "auth/mcp_service.py",
    "auth/dependencies.py",
    "routers/settings.py",
)

_LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)


def _referenced_names(expression: ast.AST) -> set[str]:
    """Identifiers an expression reads.

    Names only — a format string that happens to contain the word ``path`` is a
    ``Constant`` and contributes nothing, so this cannot fire on prose.
    """
    return {node.id for node in ast.walk(expression) if isinstance(node, ast.Name)}


def _logger_call_arguments(module_path: Path):
    """Yield ``(line, source)`` for every argument of every logger call."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOG_METHODS:
            continue
        receiver = node.func.value
        receiver_name = (
            receiver.id if isinstance(receiver, ast.Name) else getattr(receiver, "attr", "")
        )
        if "log" not in receiver_name.lower():
            continue
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        for argument in arguments:
            yield node.lineno, argument


@pytest.mark.parametrize("relative", PROJECTION_MODULES)
def test_no_logger_call_interpolates_a_projection_path(relative: str):
    """The structural half: catches a re-introduction at any new log site."""
    tainted = TAINTED_ROOTS | EXTRA_TAINTED_NAMES.get(relative, frozenset())
    module_path = BACKEND_DIR / relative

    offences = [
        f"{relative}:{line} logs {sorted(_referenced_names(argument) & tainted)}"
        f" — {ast.unparse(argument)}"
        for line, argument in _logger_call_arguments(module_path)
        if _referenced_names(argument) & tainted
    ]

    assert not offences, (
        "MCP_SECRETS_DIR-derived values reach a logger "
        "(py/clear-text-logging-sensitive-data):\n" + "\n".join(offences)
    )


def test_the_superseded_projection_is_not_derived_from_the_tainted_paths():
    """``main.startup_event`` logs this value, so it must not be tainted.

    The superseded pre-…-04c0u.8 projection lives under ``CONFIG_DIR``, which is
    not a sensitive source, and its filename is a constant. Building it as
    ``CONFIG_DIR / MCP_SERVICE_FILE.name`` made it tainted anyway and put the
    startup warning on the alert list, for a value that never depended on
    ``MCP_SECRETS_DIR`` in the first place. Deriving it from the filename
    constant keeps the warning able to name the exact file to delete.
    """
    tree = ast.parse((BACKEND_DIR / "config.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "superseded_mcp_service_projection"
    )

    offences = [
        f"line {node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(function)
        if isinstance(node, ast.Assign) and _referenced_names(node.value) & TAINTED_ROOTS
    ]

    assert not offences, (
        "superseded_mcp_service_projection() builds its result from an "
        "MCP_SECRETS_DIR-derived value, which taints the startup warning that "
        "logs it:\n" + "\n".join(offences)
    )


class TestDegradedProjectionLogsNameTheVariableNotThePath:
    """The behavioural half: what an operator actually reads in the log."""

    def test_a_degraded_projection_never_names_its_directory(self, tmp_path, caplog):
        projection_dir = tmp_path / "sentinel-secrets-04c0u8-degraded"
        projection_dir.mkdir()
        projection = projection_dir / "mcp-service.json"
        reset_mcp_projection_failure_log_latch()
        try:
            with caplog.at_level(logging.DEBUG, logger="auth.mcp_service"):
                projection_dir.chmod(0o500)
                # First failure of the episode — the latched traceback.
                assert load_mcp_service_credentials(projection) is None
                # Repeat failure — the DEBUG one-liner.
                assert load_mcp_service_credentials(projection) is None
                projection_dir.chmod(0o700)
                # Recovery — the INFO line.
                assert load_mcp_service_credentials(projection) is not None
        finally:
            projection_dir.chmod(0o700)
            reset_mcp_projection_failure_log_latch()

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "auth.mcp_service"
        ]
        assert len(messages) == 3, messages

        leaked = [message for message in messages if str(projection_dir) in message]
        assert not leaked, f"projection directory disclosed in a log message: {leaked}"

        emitted = "\n".join(messages)
        assert "MCP_SECRETS_DIR" in emitted
        assert "mcp-service.json" in emitted

    def test_an_unwritable_rotation_names_neither_directory_in_log_nor_503(
        self, tmp_path, caplog
    ):
        """The 503 stays loud and repairable — it just stops quoting the path."""
        import routers.settings as settings_router

        projection_dir = tmp_path / "sentinel-secrets-04c0u8-rotate"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        try:
            with caplog.at_level(logging.DEBUG, logger="routers.settings"):
                with patch.object(
                    settings_router,
                    "MCP_SERVICE_FILE",
                    projection_dir / "mcp-service.json",
                ):
                    with pytest.raises(HTTPException) as raised:
                        settings_router._rotate_private_projection_or_503()
        finally:
            projection_dir.chmod(0o700)

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "routers.settings"
        ]
        leaked = [message for message in messages if str(projection_dir) in message]
        assert not leaked, f"projection directory disclosed in a log message: {leaked}"
        assert any("MCP_SECRETS_DIR" in message for message in messages), messages

        assert raised.value.status_code == 503
        assert str(projection_dir) not in raised.value.detail
        assert "MCP_SECRETS_DIR" in raised.value.detail
        assert "not rotated" in raised.value.detail


def test_settings_save_failure_logs_only_the_exception_type(
    tmp_path, caplog, monkeypatch
):
    """An MCP filesystem exception must not disclose its derived path."""
    secret_path = tmp_path / "sentinel-secrets-04c0u8-save" / "api-key"

    def fail_sweep():
        raise OSError(f"failed to inspect {secret_path}")

    monkeypatch.setattr(config, "_sweep_orphaned_mcp_temporaries_locked", fail_sweep)

    with caplog.at_level(logging.ERROR, logger="config"):
        with pytest.raises(config.MCPApiKeyStorageError):
            config.save_settings(
                config.DispatcharrSettings(),
                settings_file=tmp_path / "settings.json",
            )

    records = [record for record in caplog.records if record.name == "config"]
    assert records
    assert all(str(secret_path) not in record.getMessage() for record in records)
    assert all(record.exc_info is None for record in records)
    assert any("MCPApiKeyStorageError" in record.getMessage() for record in records)
