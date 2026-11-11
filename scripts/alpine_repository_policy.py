"""Validate the stable-only feed selection for the Alpine migration prototype.

Selection is not APK signature verification, retained-input completeness or a
vulnerability assessment. The caller must perform those separate checks too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BRANCH = "v3.24"
REPOSITORIES = (
    "https://dl-cdn.alpinelinux.org/alpine/" + BRANCH + "/main",
    "https://dl-cdn.alpinelinux.org/alpine/" + BRANCH + "/community",
)


class AlpinePolicyError(ValueError):
    """The image release or active repository set differs from the reviewed set."""


def validate_repositories(release_text: str, repositories_text: str) -> dict:
    """Require the reviewed stable release and complete untagged official feeds."""
    release = release_text.strip()
    if re.fullmatch(r"3\.24\.\d+", release) is None:
        raise AlpinePolicyError("Alpine release must be a stable 3.24 patch release")
    repositories = [
        line.strip()
        for line in repositories_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(repositories) != len(REPOSITORIES) or set(repositories) != set(REPOSITORIES):
        raise AlpinePolicyError(
            "Alpine repositories must be exactly official v3.24 main/community; "
            "edge/testing, mixed releases, tags and alternate endpoints are forbidden"
        )
    return {
        "release": release,
        "policy_branch": BRANCH,
        "repositories": repositories,
        "scope": "Stable feed selection only; repositories are not immutable and this is not security clearance.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-file", type=Path, default=Path("/etc/alpine-release")
    )
    parser.add_argument(
        "--repositories", type=Path, default=Path("/etc/apk/repositories")
    )
    args = parser.parse_args(argv)
    try:
        result = validate_repositories(
            args.release_file.read_text(), args.repositories.read_text()
        )
    except (OSError, UnicodeError, AlpinePolicyError) as exc:
        print("ALPINE POLICY DENIED: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
