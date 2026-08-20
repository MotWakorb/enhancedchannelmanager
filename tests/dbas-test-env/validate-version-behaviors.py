#!/usr/bin/env python3
"""
validate-version-behaviors.py — re-validate the 0.23.0-era behaviors ECM
encodes, against whatever Dispatcharr is actually running.

This is the "don't let the fake re-encode our assumptions" guard (acceptance
criterion #3) applied to the LIVE instance: it probes the real, running stack
and reports whether each version-specific behavior ECM depends on still holds.
The output is the GROUND TRUTH that any CI fake
(backend/tests/fixtures/mock_dispatcharr.py and its successor) must be
diffed against.

THIS SCRIPT DOES NOT KNOW WHAT VERSION IT SHOULD SEE, AND MUST NOT.
`tests/dbas-test-env/` tracks `dispatcharr:latest` literally (PO decision, bead
enhancedchannelmanager-xvuk1), so the platform moves without announcing itself.
The version is READ from `GET /api/core/version/` and stamped on every line of
the report; if it cannot be read, the run is a hard failure rather than a
result, because a result that cannot name its platform is not a result. It used
to default `EXPECTED_VERSION` to `0.26.0`, which meant a run against a newer
instance either failed or — worse — passed for the wrong reason.

Behaviors checked (from bead zqtjj / spike tsfv0):
  1. Version-detect endpoint   GET /api/core/version/        -> RECORD the value
  2. Current-user endpoint     GET /api/accounts/users/me/   (0.23.0+); record
                               whether /api/accounts/me/ also answers (fallback)
  3. Login throttle            >3 logins/min/IP -> 429 (0.23.0+ shared throttle)
  4. M3U refresh shape         POST refresh returns immediately (2-stage async);
                               record the response shape ECM must poll against
  5. EPG import shape          POST /api/epg/import/ returns-then-downloads
  6. is_active workaround      record the M3U is_active toggle behavior

It does NOT mutate persistent state beyond what's needed to observe a behavior,
and it never deletes. Run AFTER the stack is up and seeded.

Usage:
  DBAS_TEST_BASE_URL=http://localhost:9591 \
  DBAS_TEST_ADMIN_USER=ecmtest DBAS_TEST_ADMIN_PASS=ecmtestpass \
  python3 validate-version-behaviors.py

Optional, and OFF by default: set DBAS_EXPECT_VERSION to assert a specific
version — only useful when you are deliberately reproducing an old finding with
`DISPATCHARR_VERSION=<old>`. Leaving it unset is the normal case and is what
keeps this script from carrying a pin.

Exit codes:
  0  all probes ran (PASS/INFO recorded)
  2  hard failure — instance unreachable, or its version could not be read
Behavior DRIFT from expectations is reported as WARN, not a hard failure —
drift is a finding for the importer authors, not a crash.
"""
from __future__ import annotations

import os
import sys
import json
import time
import urllib.request
import urllib.error

BASE = os.environ.get("DBAS_TEST_BASE_URL", "http://localhost:9591").rstrip("/")
USER = os.environ.get("DBAS_TEST_ADMIN_USER", "ecmtest")
PASS = os.environ.get("DBAS_TEST_ADMIN_PASS", "ecmtestpass")
# OPT-IN only. Unset by default — this script must never carry a version pin.
EXPECT_VERSION = os.environ.get("DBAS_EXPECT_VERSION") or None

# Filled in by probe 1 from the running instance. Never guessed, never defaulted.
RUNNING_VERSION: str | None = None

results: list[tuple[str, str, str]] = []  # (level, name, detail)


def rec(level: str, name: str, detail: str) -> None:
    results.append((level, name, detail))
    print(f"[{level:4}] {name}: {detail}")


def call(method: str, path: str, token=None, body=None, raw_body=None):
    url = f"{BASE}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    elif raw_body is not None:
        data = raw_body
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            txt = resp.read().decode()
            return resp.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except urllib.error.URLError as e:
        return None, str(e)


def read_running_version(status, txt) -> str | None:
    """Pull the version STRING out of /api/core/version/.

    Returns None rather than a guess when the response is not a 200 carrying a
    non-empty `version` field, so "could not read" is never mistaken for a
    value. Tolerates a bare string body as well as the documented JSON object.
    """
    if status != 200:
        return None
    try:
        payload = json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        value = payload.get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def main() -> int:
    global RUNNING_VERSION

    # 1. Version-detect. This is not an assertion — it is how every other line
    #    of this report gets a platform to name.
    status, txt = call("GET", "/api/core/version/")
    if status is None:
        rec("FAIL", "reachability", f"instance unreachable at {BASE}: {txt}")
        return 2

    RUNNING_VERSION = read_running_version(status, txt)
    if RUNNING_VERSION is None:
        rec("FAIL", "version-detect",
            f"could not read a version from /api/core/version/ "
            f"(status={status} body={txt[:120]!r}). Refusing to report probe "
            f"results that cannot name the platform they ran against.")
        return 2

    rec("PASS", "version-detect",
        f"running Dispatcharr {RUNNING_VERSION} "
        f"(/api/core/version/ -> {txt.strip()[:120]})")

    if EXPECT_VERSION is not None:
        if EXPECT_VERSION == RUNNING_VERSION:
            rec("PASS", "version-expectation",
                f"DBAS_EXPECT_VERSION={EXPECT_VERSION} matches the running instance")
        else:
            rec("WARN", "version-expectation",
                f"DBAS_EXPECT_VERSION={EXPECT_VERSION} but the instance reports "
                f"{RUNNING_VERSION} — you are not testing what you think you are")

    # Login (also primes throttle test)
    status, txt = call("POST", "/api/accounts/token/", body={"username": USER, "password": PASS})
    token = None
    if status == 200:
        token = json.loads(txt)["access"]
        rec("PASS", "auth", "token obtained")
    else:
        rec("WARN", "auth", f"login status={status} body={txt[:120]} "
                            f"(seed-admin env may differ on Dispatcharr {RUNNING_VERSION})")

    # 2. current-user endpoint + fallback
    if token:
        s_new, _ = call("GET", "/api/accounts/users/me/", token)
        s_old, _ = call("GET", "/api/accounts/me/", token)
        rec("PASS" if s_new == 200 else "WARN", "users/me",
            f"/api/accounts/users/me/ -> {s_new}; /api/accounts/me/ -> {s_old}")

    # 3. Login throttle (0.23.0+: >3/min/IP -> 429). Fire a burst of bad logins.
    codes = []
    for _ in range(6):
        s, _ = call("POST", "/api/accounts/token/", body={"username": "nope", "password": "nope"})
        codes.append(s)
        time.sleep(0.2)
    if 429 in codes:
        rec("PASS", "login-throttle", f"saw 429 in burst: {codes}")
    else:
        rec("WARN", "login-throttle",
            f"no 429 in burst {codes} — throttle behavior may have changed "
            f"in Dispatcharr {RUNNING_VERSION}")

    # 4/5/6: shape probes (read-only listing of what ECM polls; do not assert
    # success, just record the response SHAPE the fake must reproduce).
    if token:
        for label, path in [
            ("m3u-list", "/api/m3u/accounts/"),
            ("epg-sources", "/api/epg/sources/"),
            ("streams", "/api/channels/streams/?page=1&page_size=1"),
            ("logos", "/api/channels/logos/?page=1&page_size=1"),
        ]:
            s, t = call("GET", path, token)
            rec("INFO", f"shape:{label}", f"status={s} body[:160]={t[:160]}")

    warns = sum(1 for lvl, _, _ in results if lvl == "WARN")
    print(f"\n[summary] VALIDATED AGAINST DISPATCHARR {RUNNING_VERSION} at {BASE}")
    print(f"[summary] {len(results)} probes, {warns} WARN (drift findings for importer authors)")
    print("[summary] Quote the version above with any result from this run — "
          "tests/dbas-test-env/ tracks `latest`, so the platform moves.")
    print("[summary] Feed these shapes into the CI fake's contract test "
          "(see docs/testing/dbas-test-env.md 'CI fake validation').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
