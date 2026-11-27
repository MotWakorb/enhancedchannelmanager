"""Diagnostic URL redaction must stay independent of application routers."""

import subprocess
import sys
from pathlib import Path


def test_backup_and_obfuscation_share_leaf_credential_rules():
    import credential_sentinel
    import obfuscate
    from routers import backup

    assert (
        backup._URL_CREDENTIAL_QUERY_KEYS
        is credential_sentinel.URL_CREDENTIAL_QUERY_KEYS
        is obfuscate.URL_CREDENTIAL_QUERY_KEYS
    )
    assert (
        backup._rewrite_known_credential_segments
        is credential_sentinel.rewrite_known_credential_segments
        is obfuscate._rewrite_known_credential_values
    )


def test_url_scrubbing_does_not_import_application_routers():
    script = """
import importlib.abc
import sys

attempted = []

class RejectRouters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "routers" or fullname.startswith("routers."):
            attempted.append(fullname)
            raise AssertionError("URL scrubbing must not load application routers")

sys.meta_path.insert(0, RejectRouters())
from obfuscate import obfuscate_url

result = obfuscate_url(
    "https://provider.invalid/custom/operator/secret-value-hd/guide?api_key=unknown",
    frozenset({"secret-value"}), frozenset({"operator"}),
)
assert not attempted, attempted
assert "secret-value" not in result and "operator" not in result
assert "unknown" not in result
assert "***REDACTED***" in result
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
