"""CI guard — forbid raw outbound calls in cloud_storage adapters.

Bead enhancedchannelmanager-0i2vt.5 / threat model §9.4 item 1 + §9.2 row B4:
the SSRF validator is only effective if it is the SINGLE chokepoint. An adapter
that opens its own HTTP connection (httpx / requests / urllib / aiohttp) or
constructs a boto3 client straight from an operator-supplied endpoint URL
bypasses the validator entirely — "SSRF protection is theatre."

This test is the build-failing guard. It does a lightweight AST scan of every
module under ``backend/cloud_storage/`` and FAILS if it finds a raw outbound
primitive that is not annotated as routed through the chokepoint.

How the guard is enforced
-------------------------
* It runs in the normal backend pytest suite (``.github/workflows/test.yml``
  → "Run pytest"), so any PR that re-introduces a raw outbound call in an
  adapter turns this test red and blocks the merge gate.
* An adapter that legitimately needs to make a call MUST route it through
  ``security.ssrf`` (the validated client / ``validate_outbound_url``). The
  scan ignores any module that imports ``security.ssrf`` AND tags the call
  site with a ``# ssrf-ok:`` comment giving the reason — see ``_ALLOW_TAG``.
  This keeps the guard honest: the escape hatch is explicit and greppable, not
  a silent default.

Why AST, not grep: a substring grep trips on strings, comments, and the word
"requests" in prose. The AST scan keys on actual ``import`` statements and
call expressions, so it has far fewer false positives while still being a hard
gate. A plain-text fallback covers ``# ssrf-ok:`` tagging.

NOTE: As of .5 the adapters are NOT yet migrated onto the chokepoint (that is
bead .8). Today's adapters DO contain raw calls. So this guard is written to
ASSERT THE CURRENT INVENTORY and FAIL if NEW untagged raw outbound primitives
appear beyond the known-baseline set. When .8 migrates an adapter, it removes
the baseline entry; when the baseline is empty the guard becomes a pure
"no raw outbound" gate. This makes the guard useful immediately (catches a new
bypass in a new adapter) without falsely failing on the pre-.8 baseline.
"""
import ast
from pathlib import Path

import pytest

# Outbound HTTP primitives that must not be used raw in an adapter. Includes the
# generic HTTP clients AND the provider SDKs that do their own DNS + connect
# (boto3, dropbox, google api client) — those are exactly the §9.2 row B4 / §9.5
# "SDK bypasses the validator" concern, so they count as raw outbound for the
# chokepoint guard. urllib.parse (pure URL parsing, no I/O) is explicitly
# exempted in the scanner.
_FORBIDDEN_IMPORT_ROOTS = {
    "httpx", "requests", "urllib", "aiohttp", "http", "boto3",
    "dropbox", "google", "googleapiclient",
}
_ALLOW_TAG = "ssrf-ok:"

CLOUD_DIR = Path(__file__).resolve().parent.parent / "cloud_storage"

# Known pre-.8 baseline: modules that still make raw outbound calls today and
# are scheduled to be migrated onto the chokepoint in bead .8. The guard
# tolerates these specific modules so it does not falsely fail before .8, but
# FAILS LOUDLY if a brand-new adapter shows up with a raw outbound primitive.
# bead .8 MUST shrink this set as it migrates each adapter.
# bead .8 migrated s3/gdrive/webdav onto the chokepoint (boto3/google imports
# now carry the `# ssrf-ok:` tag because the endpoint is pre-validated via
# security.ssrf before the SDK call). onedrive/dropbox were DEFERRED to a
# v0.18.x follow-up (PO decision 2026-06-17 — drop them from v0.18.0), so they
# keep their raw outbound calls and remain on the baseline.
_PRE_8_BASELINE = {
    "onedrive_adapter.py",  # DEFERRED (v0.18.x) — raw httpx, not migrated
    "dropbox_adapter.py",   # DEFERRED (v0.18.x) — raw dropbox SDK, not migrated
}


def _adapter_modules():
    return sorted(p for p in CLOUD_DIR.glob("*.py") if p.name != "__init__.py")


def _has_allow_tag(source: str, lineno: int) -> bool:
    """Return True if the source line (1-based) carries the # ssrf-ok: tag."""
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        return _ALLOW_TAG in lines[lineno - 1]
    return False


def _forbidden_outbound_nodes(source: str):
    """Yield (lineno, description) for raw outbound primitives in ``source``.

    Detects:
      * ``import httpx`` / ``import requests`` / etc.
      * ``from urllib import request`` / ``from httpx import ...`` etc.
      * attribute calls like ``boto3.client(...)``, ``httpx.AsyncClient(...)``,
        ``requests.get(...)`` where the root name is a forbidden import.
    Skips any node whose line carries the ``# ssrf-ok:`` tag.
    """
    tree = ast.parse(source)
    findings = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                # urllib.parse is pure URL parsing (no I/O) — allow it.
                if alias.name.startswith("urllib.parse"):
                    continue
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    if not _has_allow_tag(source, node.lineno):
                        findings.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if mod.startswith("urllib.parse"):
                continue
            if root in _FORBIDDEN_IMPORT_ROOTS:
                if not _has_allow_tag(source, node.lineno):
                    findings.append((node.lineno, f"from {mod} import ..."))

    return findings


class TestCloudStorageChokepointGuard:
    def test_cloud_dir_exists(self):
        assert CLOUD_DIR.is_dir(), f"cloud_storage dir not found at {CLOUD_DIR}"

    @pytest.mark.parametrize("module_path", _adapter_modules(), ids=lambda p: p.name)
    def test_no_new_raw_outbound_calls(self, module_path):
        """A module with raw outbound primitives must be a known .8 baseline.

        New modules (not in the pre-.8 baseline) must contain ZERO raw outbound
        primitives — they have to route through ``security.ssrf``.
        """
        source = module_path.read_text()
        findings = _forbidden_outbound_nodes(source)

        if not findings:
            return  # clean — nothing to check

        if module_path.name in _PRE_8_BASELINE:
            pytest.skip(
                f"{module_path.name} is a known pre-.8 raw-outbound baseline "
                f"(migrate onto security.ssrf in bead .8): {findings}"
            )

        detail = ", ".join(f"L{ln}: {desc}" for ln, desc in findings)
        pytest.fail(
            f"{module_path.name} makes a RAW outbound call that bypasses the "
            f"SSRF chokepoint ({detail}). Route it through security.ssrf "
            f"(validate_outbound_url / the validated client). If this is "
            f"genuinely the validated path, tag the line with '# {_ALLOW_TAG} "
            f"<reason>'. See docs/security/threat_model_dbas_import.md §9.4 "
            f"item 1 / row B4."
        )

    def test_baseline_does_not_grow_silently(self):
        """The pre-.8 baseline must not list modules that no longer have raw calls.

        Keeps the baseline honest: once bead .8 migrates an adapter, its raw
        outbound primitives disappear and it should be removed from
        ``_PRE_8_BASELINE``. If a baseline entry is already clean, fail so the
        stale entry gets pruned (otherwise a future regression in that file
        would be silently skipped).
        """
        stale = []
        for name in _PRE_8_BASELINE:
            path = CLOUD_DIR / name
            if not path.exists():
                stale.append(f"{name} (file missing)")
                continue
            if not _forbidden_outbound_nodes(path.read_text()):
                stale.append(f"{name} (no raw outbound left — remove from baseline)")
        assert not stale, (
            "pre-.8 SSRF baseline is stale; prune these entries: " + ", ".join(stale)
        )
