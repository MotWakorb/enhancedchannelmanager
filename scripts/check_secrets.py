#!/usr/bin/env python3
"""Scan changed files using detect-secrets authority derived from the base.

The pull request's ``.secrets.baseline`` is never suppression or plugin
authority for that same pull request. The merge-base tree is scanned with the
pinned detect-secrets release and policy. When the base already contains a
baseline, it must match that fresh scan; for the first-baseline bootstrap, the
candidate baseline in HEAD must match instead. The freshly generated base
artifact is then the only baseline passed to ``detect-secrets-hook``.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.metadata
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


PINNED_VERSION = "1.5.0"
BASELINE_PATH = ".secrets.baseline"
INLINE_ALLOWLIST_FILTER = "detect_secrets.filters.allowlist.is_line_allowlisted"
REQUIRED_PLUGINS = frozenset(
    {
        "ArtifactoryDetector",
        "AWSKeyDetector",
        "AzureStorageKeyDetector",
        "Base64HighEntropyString",
        "BasicAuthDetector",
        "CloudantDetector",
        "DiscordBotTokenDetector",
        "GitHubTokenDetector",
        "GitLabTokenDetector",
        "HexHighEntropyString",
        "IbmCloudIamDetector",
        "IbmCosHmacDetector",
        "IPPublicDetector",
        "JwtTokenDetector",
        "KeywordDetector",
        "MailchimpDetector",
        "NpmDetector",
        "OpenAIDetector",
        "PrivateKeyDetector",
        "PypiTokenDetector",
        "SendGridDetector",
        "SlackDetector",
        "SoftlayerDetector",
        "SquareOAuthDetector",
        "StripeDetector",
        "TelegramBotTokenDetector",
        "TwilioKeyDetector",
    }
)
REQUIRED_FILTERS = frozenset(
    {
        "detect_secrets.filters.common.is_baseline_file",
        "detect_secrets.filters.heuristic.is_indirect_reference",
        "detect_secrets.filters.heuristic.is_likely_id_string",
        "detect_secrets.filters.heuristic.is_lock_file",
        "detect_secrets.filters.heuristic.is_not_alphanumeric_string",
        "detect_secrets.filters.heuristic.is_potential_uuid",
        "detect_secrets.filters.heuristic.is_prefixed_with_dollar_sign",
        "detect_secrets.filters.heuristic.is_sequential_string",
        "detect_secrets.filters.heuristic.is_swagger_file",
        "detect_secrets.filters.heuristic.is_templated_secret",
    }
)


class GuardError(Exception):
    """A metadata-only scanner configuration or execution failure."""


def _run(repo: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd or repo,
        capture_output=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> bytes:
    result = _run(repo, "git", "-C", str(repo), *args)
    if result.returncode:
        raise GuardError("git metadata operation failed")
    return result.stdout


def _validate_policy(baseline: dict) -> None:
    if baseline.get("version") != PINNED_VERSION:
        raise GuardError("baseline scanner version differs from pinned policy")
    plugins = {item.get("name") for item in baseline.get("plugins_used", [])}
    filters = {item.get("path") for item in baseline.get("filters_used", [])}
    if plugins != REQUIRED_PLUGINS:
        raise GuardError("baseline plugin policy differs from required policy")
    if filters != REQUIRED_FILTERS:
        raise GuardError("baseline filter policy differs from required policy")
    plugin_by_name = {item["name"]: item for item in baseline["plugins_used"]}
    if plugin_by_name["Base64HighEntropyString"].get("limit") != 4.5:
        raise GuardError("base64 threshold differs from required policy")
    if plugin_by_name["HexHighEntropyString"].get("limit") != 3.0:
        raise GuardError("hex threshold differs from required policy")
    baseline_filters = [
        item
        for item in baseline["filters_used"]
        if item.get("path") == "detect_secrets.filters.common.is_baseline_file"
    ]
    if baseline_filters != [
        {
            "path": "detect_secrets.filters.common.is_baseline_file",
            "filename": BASELINE_PATH,
        }
    ]:
        raise GuardError("baseline self-filter differs from required policy")


def _normalized(baseline: dict) -> dict:
    copy = json.loads(json.dumps(baseline))
    copy.pop("generated_at", None)
    return copy


def _extract_tree(repo: Path, revision: str, destination: Path) -> None:
    archive = _git(repo, "archive", "--format=tar", revision)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def _scan_tree(repo: Path, revision: str, scanner: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="ecm-secrets-base-") as raw_dir:
        tree = Path(raw_dir)
        _extract_tree(repo, revision, tree)
        # A recorded baseline is evidence about the base, never scan input.
        # Removing it before discovery makes the self-filter effective even
        # though that filter is recorded only in the generated metadata.
        (tree / BASELINE_PATH).unlink(missing_ok=True)
        initial = _run(
            repo,
            scanner,
            "scan",
            "--all-files",
            "--no-verify",
            "--disable-filter",
            INLINE_ALLOWLIST_FILTER,
            "--slim",
            ".",
            cwd=tree,
        )
        if initial.returncode:
            raise GuardError("base-tree secret inventory failed")
        try:
            baseline = json.loads(initial.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GuardError("base-tree scanner returned invalid metadata") from error
        baseline["filters_used"].append(
            {
                "path": "detect_secrets.filters.common.is_baseline_file",
                "filename": BASELINE_PATH,
            }
        )
        baseline["filters_used"].sort(key=lambda item: item["path"])
        _validate_policy(baseline)
        return baseline


def _baseline_at(repo: Path, revision: str) -> dict | None:
    tree_entry = _run(
        repo,
        "git",
        "-C",
        str(repo),
        "ls-tree",
        "-z",
        revision,
        "--",
        BASELINE_PATH,
    )
    if tree_entry.returncode:
        raise GuardError("baseline Git metadata operation failed")
    if not tree_entry.stdout:
        return None
    metadata = tree_entry.stdout.split(b"\t", 1)[0].split()
    if len(metadata) < 2 or metadata[0] != b"100644" or metadata[1] != b"blob":
        raise GuardError("baseline must be a regular non-executable Git blob")
    result = _run(repo, "git", "-C", str(repo), "show", f"{revision}:{BASELINE_PATH}")
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardError("base baseline is invalid metadata") from error


def _candidate_baseline(repo: Path) -> dict:
    baseline = _baseline_at(repo, "HEAD")
    if baseline is None:
        raise GuardError("candidate baseline is missing")
    return baseline


def _finding_inventory(baseline: dict) -> Counter[str]:
    return Counter(
        json.dumps(finding, sort_keys=True, separators=(",", ":"))
        for findings in baseline.get("results", {}).values()
        for finding in findings
    )


def _is_strict_finding_removal(before: dict, after: dict) -> bool:
    """Accept only removal of known findings under identical scanner policy."""
    before_metadata = {key: value for key, value in before.items() if key != "results"}
    after_metadata = {key: value for key, value in after.items() if key != "results"}
    # detect-secrets refreshes this informational timestamp whenever it
    # rewrites a baseline; it is not scanner policy or suppression authority.
    before_metadata.pop("generated_at", None)
    after_metadata.pop("generated_at", None)
    if after_metadata != before_metadata:
        return False
    old_findings = _finding_inventory(before)
    new_findings = _finding_inventory(after)
    return new_findings != old_findings and all(
        count <= old_findings[finding] for finding, count in new_findings.items()
    )


def _canonical_runtime_baseline(baseline: dict) -> dict:
    """Convert a scanner-updated temporary baseline to repository form."""
    canonical = json.loads(json.dumps(baseline))
    canonical.pop("generated_at", None)
    self_filter = next(
        (
            item
            for item in canonical.get("filters_used", [])
            if item.get("path")
            == "detect_secrets.filters.common.is_baseline_file"
        ),
        None,
    )
    if self_filter is None:
        raise GuardError("updated baseline omits required policy metadata")
    self_filter["filename"] = BASELINE_PATH
    _validate_policy(canonical)
    return canonical


def _report_changed_path_failure(paths: list[str]) -> int:
    print("FAIL: possible secret finding or ambiguous baseline mutation.", file=sys.stderr)
    for path in paths:
        print(f"Location: {path}", file=sys.stderr)
    return 1


def check(repo: Path, base_ref: str, scanner: str, refresh_bootstrap: bool) -> int:
    if importlib.metadata.version("detect-secrets") != PINNED_VERSION:
        raise GuardError("installed detect-secrets version differs from pin")
    base = _git(repo, "merge-base", base_ref, "HEAD").decode().strip()
    recorded = _baseline_at(repo, base)
    authority = _scan_tree(repo, base, scanner)
    if recorded is None:
        if refresh_bootstrap:
            (repo / BASELINE_PATH).write_text(
                json.dumps(authority, indent=2) + "\n", encoding="utf-8"
            )
            print("Bootstrap baseline refreshed from the merge-base tree.")
            return 0
        recorded = _candidate_baseline(repo)
    _validate_policy(recorded)
    if _normalized(recorded) != _normalized(authority):
        raise GuardError("recorded baseline does not match a fresh base-tree scan")
    try:
        candidate = _candidate_baseline(repo)
    except GuardError:
        return _report_changed_path_failure([BASELINE_PATH])

    changed = _git(
        repo,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        "--diff-filter=ACMR",
        base,
        "HEAD",
    ).split(b"\0")
    paths = [item.decode("utf-8") for item in changed if item]
    paths = [path for path in paths if path != BASELINE_PATH]
    deleted = _git(
        repo,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        "--diff-filter=D",
        base,
        "HEAD",
    ).split(b"\0")
    deleted_paths = [item.decode("utf-8") for item in deleted if item]
    if not paths and not deleted_paths:
        if candidate != authority:
            return _report_changed_path_failure([BASELINE_PATH])
        print("PASS: no changed files for generic secret scanning.")
        return 0
    report_paths = paths + [path for path in deleted_paths if path not in paths]
    with tempfile.TemporaryDirectory(prefix="ecm-secrets-authority-") as raw_dir:
        baseline_file = Path(raw_dir) / BASELINE_PATH
        runtime_authority = json.loads(json.dumps(authority))
        self_filter = next(
            item
            for item in runtime_authority["filters_used"]
            if item["path"] == "detect_secrets.filters.common.is_baseline_file"
        )
        # The scanner rewrites this filter to the path it was actually passed.
        # Adapt it before execution so all policy metadata remains identical.
        self_filter["filename"] = str(baseline_file)
        pre_change_authority = json.loads(json.dumps(runtime_authority))
        for path in deleted_paths:
            runtime_authority["results"].pop(path, None)
        baseline_file.write_text(
            json.dumps(runtime_authority, indent=2) + "\n", encoding="utf-8"
        )
        if paths:
            result = subprocess.run(
                [
                    scanner.replace("detect-secrets", "detect-secrets-hook"),
                    "--no-verify",
                    "--disable-filter",
                    INLINE_ALLOWLIST_FILTER,
                    "--baseline",
                    str(baseline_file),
                    "--",
                    *paths,
                ],
                cwd=repo,
                capture_output=True,
                check=False,
            )
        else:
            result = subprocess.CompletedProcess([], 0)
        try:
            updated_authority = json.loads(baseline_file.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GuardError("changed-file scanner returned invalid baseline metadata") from error
    strict_shrink = _is_strict_finding_removal(
        pre_change_authority, updated_authority
    )
    if result.returncode == 0 and not strict_shrink and updated_authority == runtime_authority:
        if candidate == authority:
            return 0
        return _report_changed_path_failure([BASELINE_PATH, *report_paths])
    if result.returncode in (0, 3) and strict_shrink:
        canonical_shrink = _canonical_runtime_baseline(updated_authority)
        if candidate != canonical_shrink:
            return _report_changed_path_failure([BASELINE_PATH, *report_paths])
        print("PASS: changed files remove tolerated base findings only.")
        return 0
    return _report_changed_path_failure(report_paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", default="origin/dev")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--scanner", default="detect-secrets")
    parser.add_argument("--refresh-bootstrap", action="store_true")
    args = parser.parse_args(argv)
    scanner = args.scanner
    if "/" in scanner and not Path(scanner).is_absolute():
        scanner = str((args.repo_root.resolve() / scanner).resolve())
    try:
        return check(args.repo_root.resolve(), args.base_ref, scanner, args.refresh_bootstrap)
    except (GuardError, importlib.metadata.PackageNotFoundError, OSError) as error:
        print(f"FAIL: generic secret guard configuration: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
