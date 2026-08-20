#!/usr/bin/env python3
"""
edge_supplement.py — add DBAS round-trip EDGE cases on top of the
production-shaped snapshot.

Production snapshots cover the COMMON case (real cardinality, real duplication,
real logo volume). They do NOT reliably contain the adversarial edges the DBAS
importers must survive. This script supplements the snapshot with explicit edge
data, via the Dispatcharr REST API (the stable contract — the internal DB schema
is not a contract we should write to directly).

Edge cases added (each maps to a DBAS importer risk):

  * EMPTY CATEGORY      — a channel group / M3U group with zero members.
                          (.10 group-matching must not divide-by-zero or skip.)
  * UNICODE NAMES       — channel/group names with CJK, RTL, emoji, combining
                          marks. (normalization + matching must be unicode-safe.)
  * MAX-LENGTH NAMES    — names at/around the field length limit.
                          (round-trip must not silently truncate.)
  * DUPLICATE STREAMS   — same stream name across multiple M3U accounts/groups,
                          so the .14 4-tier matcher has real ambiguity to resolve
                          (the snapshot supplies bulk duplication; this guarantees
                          a KNOWN, asserted duplicate set for deterministic tests).
  * FOREIGN-ADMIN-LOCKOUT (D11) — a superuser/admin account NOT owned by ECM,
                          to prove the importer never locks out or clobbers a
                          foreign admin. Privilege flags (is_superuser/is_staff/
                          user_level) are the real escalation surface per spike
                          tsfv0 — restore conservatively, never escalate.

NOT covered here (by design): plugins (D10 — excluded from DBAS scope), so the
seed is 12 categories, not 13.

Usage:
  DBAS_TEST_BASE_URL=http://localhost:9591 \
  DBAS_TEST_ADMIN_USER=ecmtest DBAS_TEST_ADMIN_PASS=ecmtestpass \
  python3 edge_supplement.py

This script is idempotent-ish: it tags everything it creates with an
"[ECM-EDGE]" marker so a re-run can find/skip existing edge objects. It is
written defensively — endpoints/fields are best-effort and MUST be validated
against the RUNNING schema on every platform move (see README). There is no
version to check them against here: `tests/dbas-test-env/` tracks
`dispatcharr:latest` (PO decision, bead enhancedchannelmanager-xvuk1), so read
the version from `GET /api/core/version/` and record it with whatever you
find. These fields were last shaped against the 0.26.0 schema.
"""
from __future__ import annotations

import os
import sys
import json
import urllib.request
import urllib.error

BASE = os.environ.get("DBAS_TEST_BASE_URL", "http://localhost:9591").rstrip("/")
USER = os.environ.get("DBAS_TEST_ADMIN_USER", "ecmtest")
PASS = os.environ.get("DBAS_TEST_ADMIN_PASS", "ecmtestpass")
MARKER = "[ECM-EDGE]"

# Field-length probe for MAX-LENGTH case. Dispatcharr names are typically
# varchar(255); we go right to the edge. Adjust if the running schema differs.
MAX_NAME = (MARKER + " ") + ("X" * (255 - len(MARKER) - 1))

UNICODE_NAMES = [
    f"{MARKER} 体育频道 スポーツ",      # CJK
    f"{MARKER} قناة الرياضة",            # RTL (Arabic)
    f"{MARKER} Спорт 📺🏆",              # Cyrillic + emoji
    f"{MARKER} Café Combining",   # combining acute accent
]


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def login() -> str:
    status, payload = _req("POST", "/api/accounts/token/", body={"username": USER, "password": PASS})
    if status != 200 or not isinstance(payload, dict):
        sys.exit(f"[edge] login failed ({status}): {payload}")
    return payload["access"]


def create_group(token: str, name: str) -> int | None:
    status, payload = _req("POST", "/api/channels/groups/", token, {"name": name})
    if status in (200, 201) and isinstance(payload, dict):
        print(f"[edge] +group {name!r} -> id {payload.get('id')}")
        return payload.get("id")
    print(f"[edge] ! group {name!r} not created ({status}): {payload}")
    return None


def create_foreign_admin(token: str) -> None:
    """D11: a foreign superuser the importer must NOT lock out / clobber."""
    body = {
        "username": f"foreign_admin_edge",
        "password": "Sup3rSecret!notECM",
        "is_superuser": True,
        "is_staff": True,
        # user_level if the schema uses it; harmless extra fields are ignored
        # by DRF serializers in most configs but verify on first run.
        "user_level": 10,
    }
    status, payload = _req("POST", "/api/accounts/users/", token, body)
    if status in (200, 201):
        print("[edge] +foreign admin (D11 lockout guard) created")
    else:
        print(f"[edge] ! foreign admin not created ({status}): {payload} "
              "-- verify /api/accounts/users/ path + privilege fields on the running schema")


def main() -> int:
    token = login()

    # 1. Empty category
    create_group(token, f"{MARKER} EMPTY CATEGORY")

    # 2. Unicode names
    for n in UNICODE_NAMES:
        create_group(token, n)

    # 3. Max-length name
    create_group(token, MAX_NAME)

    # 4. Known duplicate set (deterministic; supplements the snapshot's bulk dupes)
    dup_group = create_group(token, f"{MARKER} DUP-MATCH GROUP")
    if dup_group:
        # Two streams with the SAME name in the same group => the .14 4-tier
        # matcher must disambiguate. (Stream-create path/fields vary by version;
        # validate against the RUNNING instance, not a pin — see README.)
        for i in (1, 2):
            status, payload = _req("POST", "/api/channels/streams/", token, {
                "name": f"{MARKER} Duplicate Stream",
                "url": f"http://stream.test/dup{i}.m3u8",
                "channel_group": dup_group,
            })
            print(f"[edge] dup stream #{i} -> {status}")

    # 5. Foreign-admin lockout guard (D11)
    create_foreign_admin(token)

    print("[edge] supplement complete (validate field/endpoint names vs the "
          "RUNNING schema — read GET /api/core/version/ and record it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
