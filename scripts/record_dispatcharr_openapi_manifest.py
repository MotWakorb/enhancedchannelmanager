#!/usr/bin/env python3
"""Record the Dispatcharr OpenAPI *paths manifest* fixture from a raw schema doc.

The manifest is the recorded contract that
``backend/tests/unit/test_dispatcharr_client_contract_sweep.py`` sweeps every
Dispatcharr URL template in ``backend/dispatcharr_client.py`` against. It
answers exactly three questions per template and nothing else:

1. does this path exist upstream,
2. is this HTTP method allowed on it,
3. what type does each *path* parameter declare.

Everything else in the OpenAPI document — request/response bodies, component
schemas, descriptions, ``operationId``, tags, security, and every non-path
parameter — is **stripped**. That is deliberate (ADR-014): body-shape drift is
the job of the deep recorded fixtures
(``backend/tests/fixtures/dispatcharr_openapi_recorded.json`` and friends),
which pin a small number of high-risk paths literally. Keeping the two fixture
kinds separate means an upstream body-schema edit cannot spuriously fail the
breadth sweep, and the manifest stays reviewable at ~224 paths.

**No live dependency is baked in.** This script never performs a network
request; it reads a raw ``GET /api/schema/?format=json`` response you captured
yourself. CI therefore stays hermetic — the sweep test reads the recorded
fixture and never contacts a Dispatcharr instance.

## Capturing the input

From a host that can reach the instance (read-only GET):

    curl -s 'http://<dispatcharr-host>:9191/api/schema/?format=json' \
        -H 'X-API-Key: <key>' > /tmp/disp_schema.json

or, from inside an ECM container already configured against it::

    docker exec <ecm-container> python - <<'PY'
    import asyncio, json, sys
    sys.path.insert(0, "/app")
    from dispatcharr_client import get_client

    async def main():
        response = await get_client()._request(
            "GET", "/api/schema/", params={"format": "json"}
        )
        response.raise_for_status()
        print(json.dumps(response.json()))

    asyncio.run(main())
    PY

The bare ``/api/schema/`` route renders YAML — ``?format=json`` is required
(bead ``enhancedchannelmanager-q6xjl``; see ``docs/dispatcharr_api.md``).

Read the running Dispatcharr version from ``GET /api/core/version/`` and pass
it as ``--dispatcharr-version``; it is recorded in the manifest's provenance
block and quoted in the sweep's failure message.

## Usage

    python scripts/record_dispatcharr_openapi_manifest.py \
        --schema /tmp/disp_schema.json \
        --dispatcharr-version 0.28.2

    cat /tmp/disp_schema.json | python scripts/record_dispatcharr_openapi_manifest.py \
        --schema - --dispatcharr-version 0.28.2 --output /tmp/manifest.json

Re-record deliberately, not on a schedule (ADR-014): when adopting a new
Dispatcharr version, or when a PR adds client methods the current manifest
predates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = (
    REPO_ROOT / "backend" / "tests" / "fixtures" / "dispatcharr_openapi_paths_manifest.json"
)

# Methods the manifest records. Anything else an OpenAPI path item can carry
# (``parameters``, ``servers``, ``head``, ``options``, ``trace``) is dropped —
# ECM's client issues none of them.
RECORDED_METHODS = ("get", "post", "put", "patch", "delete")

# ``{id}``, ``{account_id}``, ``{channel_id}`` -> ``{}``. The manifest is keyed
# by this normalized template because ECM's client interpolates its own
# parameter NAMES into paths (``f"/api/m3u/accounts/{account_id}/"``) which do
# not have to match upstream's (``/api/m3u/accounts/{id}/``). Only the
# *position* of a path parameter is contractual; its upstream name is not.
_PATH_PARAM_RE = re.compile(r"\{[^{}]*\}")

FIXTURE_KIND = "derived manifest (paths + methods + path-parameter types only)"


def normalize_path(path: str) -> str:
    """Return ``path`` with every ``{param}`` replaced by the ``{}`` wildcard."""
    return _PATH_PARAM_RE.sub("{}", path)


def _path_parameters(path_item: dict, operation: dict) -> list[dict]:
    """Return the ordered path parameters for one operation.

    OpenAPI allows path-item-level parameters shared by every operation as well
    as operation-level ones; both are merged here, shared first, so the recorded
    order matches the order they appear in the URL for a well-formed document.
    """
    merged = []
    for source in (path_item.get("parameters") or [], operation.get("parameters") or []):
        for parameter in source:
            if isinstance(parameter, dict) and parameter.get("in") == "path":
                merged.append(parameter)
    return merged


def build_manifest_paths(schema: dict) -> dict:
    """Strip a raw OpenAPI document down to the recorded paths manifest body.

    Raises:
        ValueError: the document has no ``paths`` object, or two upstream paths
            normalize to the same wildcard template (which would make the
            manifest ambiguous — a real upstream change worth failing on rather
            than silently collapsing).
    """
    paths = schema.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError(
            "Schema document has no non-empty 'paths' object — is this really a "
            "GET /api/schema/?format=json response? (a bare GET renders YAML)"
        )

    manifest_paths: dict = {}
    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        template = normalize_path(path)
        existing = manifest_paths.get(template)
        if existing is not None and existing["openapi_path"] != path:
            raise ValueError(
                f"Two upstream paths normalize to the same template {template!r}: "
                f"{existing['openapi_path']!r} and {path!r}. The manifest cannot "
                "represent both; the sweep's wildcard matching would be ambiguous."
            )

        methods: dict = {}
        for method in RECORDED_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            methods[method.upper()] = {
                "path_parameters": [
                    {
                        "name": parameter.get("name"),
                        "type": (parameter.get("schema") or {}).get("type"),
                        "format": (parameter.get("schema") or {}).get("format"),
                    }
                    for parameter in _path_parameters(path_item, operation)
                ]
            }
        if not methods:
            continue
        manifest_paths[template] = {"openapi_path": path, "methods": methods}

    return manifest_paths


def build_manifest(schema: dict, *, dispatcharr_version: str, captured_at: str) -> dict:
    """Return the full manifest fixture — provenance block plus stripped paths."""
    manifest_paths = build_manifest_paths(schema)
    return {
        "capture": {
            "source": (
                "Live GET /api/schema/?format=json from Dispatcharr "
                f"{dispatcharr_version}, stripped by "
                "scripts/record_dispatcharr_openapi_manifest.py"
            ),
            "fixture_kind": FIXTURE_KIND,
            "literal_raw_capture": False,
            "captured_at": captured_at,
            "dispatcharr_version": dispatcharr_version,
            "openapi_info_version": (schema.get("info") or {}).get("version"),
            "path_count": len(manifest_paths),
            "recorded_by": "scripts/record_dispatcharr_openapi_manifest.py",
            "stripped": (
                "Request/response bodies, component schemas, descriptions, "
                "operationIds, tags, security and all non-path parameters are "
                "dropped. Only path existence, allowed methods and path-parameter "
                "name/type/format are retained."
            ),
            "why": (
                "Breadth contract for the ADR-014 sweep: every (method, URL "
                "template) DispatcharrClient issues is checked against this "
                "manifest so a guessed or upstream-moved endpoint fails a test "
                "instead of shipping (beads enhancedchannelmanager-q6xjl, "
                "enhancedchannelmanager-lsa0s). Deliberately SEPARATE from "
                "dispatcharr_openapi_recorded.json, which pins body/semantic "
                "shape for a few high-risk paths and churns on a different clock."
            ),
            "keys_are_normalized": (
                "Each key is an upstream path with every {param} replaced by {} — "
                "ECM's client interpolates its own parameter names, so only "
                "position is contractual. 'openapi_path' carries the literal "
                "upstream path for reference."
            ),
        },
        "paths": manifest_paths,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Strip a raw Dispatcharr OpenAPI document into the recorded paths "
            "manifest fixture used by the client contract sweep."
        )
    )
    parser.add_argument(
        "--schema",
        required=True,
        help="Path to a raw GET /api/schema/?format=json response, or '-' for stdin.",
    )
    parser.add_argument(
        "--dispatcharr-version",
        required=True,
        help="Version string from GET /api/core/version/ on the recorded instance.",
    )
    parser.add_argument(
        "--captured-at",
        default=date.today().isoformat(),
        help="Capture date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Where to write the manifest. Defaults to {DEFAULT_OUTPUT}.",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.schema == "-" else Path(args.schema).read_text()
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: input is not JSON ({exc}). The bare /api/schema/ route renders "
            "YAML — capture it with ?format=json.",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = build_manifest(
            schema,
            dispatcharr_version=args.dispatcharr_version,
            captured_at=args.captured_at,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    print(
        f"Wrote {output} — {manifest['capture']['path_count']} paths from "
        f"Dispatcharr {args.dispatcharr_version} (captured {args.captured_at})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
