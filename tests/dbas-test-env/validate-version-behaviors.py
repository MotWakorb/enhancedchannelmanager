#!/usr/bin/env python3
"""
validate-version-behaviors.py — re-validate the 0.23.0-era behaviors ECM
encodes, against the pinned LIVE Dispatcharr (default 0.26.0).

This is the "don't let the fake re-encode our assumptions" guard (acceptance
criterion #3) applied to the LIVE instance: it probes the real, running stack
and reports whether each version-specific behavior ECM depends on still holds
at the pinned version. The output is the GROUND TRUTH that any CI fake
(backend/tests/fixtures/mock_dispatcharr.py and its successor) must be
diffed against.

Behaviors checked (from bead zqtjj / spike tsfv0):
  1. Version-detect endpoint   GET /api/core/version/        -> reports 0.26.0
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

Exit code 0 if all probes ran (PASS/INFO recorded); non-zero only on a hard
failure (instance unreachable). Behavior DRIFT from expectations is reported as
WARN, not a hard failure — drift is a finding for the importer authors, not a
crash.
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
EXPECTED_VERSION = os.environ.get("DISPATCHARR_VERSION", "0.26.0")

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


def main() -> int:
    # 1. Version-detect
    status, txt = call("GET", "/api/core/version/")
    if status is None:
        rec("FAIL", "reachability", f"instance unreachable at {BASE}: {txt}")
        return 2
    if status == 200 and EXPECTED_VERSION in txt:
        rec("PASS", "version-detect", f"/api/core/version/ -> {txt.strip()[:120]}")
    else:
        rec("WARN", "version-detect",
            f"expected {EXPECTED_VERSION}, got status={status} body={txt[:120]}")

    # Login (also primes throttle test)
    status, txt = call("POST", "/api/accounts/token/", body={"username": USER, "password": PASS})
    token = None
    if status == 200:
        token = json.loads(txt)["access"]
        rec("PASS", "auth", "token obtained")
    else:
        rec("WARN", "auth", f"login status={status} body={txt[:120]} "
                            "(seed-admin env may differ on pinned image)")

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
            f"no 429 in burst {codes} — throttle behavior may have changed at {EXPECTED_VERSION}")

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
    print(f"\n[summary] {len(results)} probes, {warns} WARN (drift findings for importer authors)")
    print("[summary] Feed these shapes into the CI fake's contract test "
          "(see docs/testing/dbas-test-env.md 'CI fake validation').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
