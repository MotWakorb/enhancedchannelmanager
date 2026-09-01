#!/usr/bin/env python3
"""Check workflow evidence and current registry-tag markers after a dev merge.

This is a POST-MERGE check (bead enhancedchannelmanager-t8fqg). It is not
a CI gate and must never become one: a check that runs after the merge
cannot gate the merge it follows, and wiring it into the PR flow would
add a permanently-failing context to every open PR.

## Why it exists

The published `:dev` tag has silently lagged the `dev` branch four times,
from four unrelated causes:

  1. A Buildx flake that skipped the multi-arch manifest.
  2. A GitHub Actions outage that orphaned queued runs.
  3. A frontend test flake that correctly gated the publish.
  4. PR #793 (2026-08-07): the `dev` Tests workflow failed on one
     order-dependent frontend flake, so the publish gate correctly
     refused to ship from a failed suite.

Every one of those was the gate behaving correctly. The defect is that
nothing surfaced the resulting drift. On the most recent occurrence `dev`
carried the fix and the registry did not, for about five hours, until an
unrelated merge republished it by accident.

## What it checks

  1. The "Tests" push run for the commit under test concluded `success`,
     and its reusable publish workflow completed the final multi-arch
     manifest job successfully on that exact run attempt.
  2. The expected linux/amd64 and linux/arm64 configs carry matching
     `ECM_VERSION` and full
     `GIT_COMMIT` markers. They must equal `frontend/package.json` AT THAT
     COMMIT and the exact resolved commit SHA.

Both must hold. The checks intentionally report workflow evidence and current
tag-marker evidence separately; neither cryptographically binds image bytes to
the workflow job. The mutable tag check also trusts actors allowed to write it.

The image is always read through both expected platforms' registry configs
(`docker buildx imagetools inspect`), which does not download layers.
Pass `--pull` to add a host-level marker cross-check after that inspection.
A pull cannot rescue failed manifest inspection. See `docs/shipping.md`
section 6, "Check publish evidence and current tag markers".

## Refs it needs

`--commit` defaults to HEAD, and HEAD is the right default precisely
because this runs after `git checkout dev && git pull`: the local branch
IS the merged state, so the check never requires a remote-tracking ref to
do its job. `origin/dev` is only consulted as the preferred (not
required) input to the "is this commit already on dev?" orientation note,
which degrades to "unknown" rather than failing. Passing an `origin/*`
ref explicitly in a checkout that has none reports what to fetch instead
of surfacing git's raw `ambiguous argument` error.

## Usage

    # Normal use: after `gh pr merge`, `git checkout dev && git pull`
    python scripts/check_publish.py

    # A specific commit
    python scripts/check_publish.py --commit <sha>

    # Heavier image gate, matching the restore drill
    python scripts/check_publish.py --pull

Exits 0 when the registry matches `dev`, 1 when it does not or when a
check could not be completed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_IMAGE = "ghcr.io/motwakorb/enhancedchannelmanager"
DEFAULT_TAG = "dev"
DEFAULT_BRANCH = "dev"
WORKFLOW_NAME = "Tests"
PUBLISH_JOB_NAME = "Publish Verified Dev Images / Publish Verified Multi-Arch Manifests"
MARKER_ENV = "ECM_VERSION"
COMMIT_ENV = "GIT_COMMIT"
PLATFORMS_KEY = "_ECM_MANIFEST_PLATFORMS"
EXPECTED_PLATFORMS = ("linux/amd64", "linux/arm64")

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class CheckError(RuntimeError):
    """A check could not be completed (as opposed to completing and failing)."""


# --- Process helpers --------------------------------------------------------


def _run(cmd: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as error:
        raise CheckError(
            f"command timed out after {timeout}s: {' '.join(cmd)}"
        ) from error
    except OSError as error:
        raise CheckError(f"could not launch {cmd[0]!r}: {error}") from error


def _git(*args: str) -> str:
    result = _run(["git", "-C", str(REPO_ROOT), *args], timeout=60)
    if result.returncode != 0:
        raise CheckError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


# --- Repo-side facts --------------------------------------------------------


def _ref_exists(ref: str) -> bool:
    """True when `ref` names a commit that exists in THIS checkout."""
    probe = _run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{ref}^{{commit}}",
        ],
        timeout=60,
    )
    return probe.returncode == 0


def _display_ref(ref: str) -> str:
    """Shorten a raw SHA for reporting, leave symbolic refs readable."""
    return ref[:12] if _SHA_RE.match(ref) else ref


def _unresolvable_ref_message(ref: str) -> str:
    """Say what to DO about a missing ref instead of echoing git's error.

    `origin/<branch>` is the case worth spelling out: remote-tracking refs
    are simply absent from a shallow or single-branch clone (any default
    `actions/checkout`, `git clone --depth 1`), so `fatal: ambiguous
    argument` there means "this checkout was never told about the remote
    branch", not "the branch is gone".
    """
    if ref.startswith("origin/"):
        branch = ref.split("/", 1)[1]
        return (
            f"{ref!r} does not exist in this checkout ({REPO_ROOT}). "
            f"Remote-tracking refs are absent from shallow and single-branch "
            f"clones. Run `git fetch --no-tags origin {branch}` first, or pass "
            f"a ref this checkout already has (e.g. --commit {branch}, or a SHA)."
        )
    return (
        f"{ref!r} does not resolve to a commit in this checkout ({REPO_ROOT}). "
        f"Pass a commit SHA or a ref that exists locally."
    )


def resolve_commit(ref: str) -> str:
    result = _run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{ref}^{{commit}}",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise CheckError(_unresolvable_ref_message(ref))
    return result.stdout.strip()


def commit_subject(sha: str) -> str:
    return _git("log", "-1", "--format=%s", sha).strip()


def expected_version_at(ref: str) -> str:
    """Read `frontend/package.json`'s version AS OF the given commit.

    Reading the working tree instead would compare the registry against a
    bump that has not merged, which is the single most confusing way this
    check can be misread.
    """
    shown = _run(
        ["git", "-C", str(REPO_ROOT), "show", f"{ref}:frontend/package.json"],
        timeout=60,
    )
    if shown.returncode != 0:
        if not _ref_exists(ref):
            raise CheckError(_unresolvable_ref_message(ref))
        raise CheckError(
            f"frontend/package.json could not be read at {_display_ref(ref)}: "
            f"{shown.stderr.strip()}"
        )
    try:
        data = json.loads(shown.stdout)
    except json.JSONDecodeError as error:
        raise CheckError(
            f"frontend/package.json at {_display_ref(ref)} is not valid JSON: {error}"
        ) from error
    version = data.get("version")
    if not isinstance(version, str):
        raise CheckError(
            f"no string 'version' field in package.json at {_display_ref(ref)}"
        )
    return version


def commit_is_on_branch(sha: str, branch: str) -> bool | None:
    """True when `sha` is an ancestor of `branch`, None when unknown.

    The remote-tracking ref is preferred because it is what the registry
    actually built from, but a checkout that has no `origin/<branch>` (a
    shallow or single-branch clone) falls back to the local branch. When
    neither ref exists the answer is unknown, not False: this is only
    orientation for the operator, so a missing ref must never masquerade
    as "the commit is not on the branch".
    """
    for ref in (f"origin/{branch}", branch):
        probe_cmd = [
            "git",
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            "--verify",
            "--quiet",
            ref,
        ]
        probe = _run(probe_cmd, timeout=60)
        if probe.returncode == 1:
            continue
        if probe.returncode != 0:
            raise CheckError(
                f"{' '.join(probe_cmd)} failed ({probe.returncode}): "
                f"{probe.stderr.strip() or 'no stderr'}"
            )
        ancestor_cmd = [
            "git",
            "-C",
            str(REPO_ROOT),
            "merge-base",
            "--is-ancestor",
            sha,
            ref,
        ]
        result = _run(ancestor_cmd, timeout=60)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise CheckError(
            f"{' '.join(ancestor_cmd)} failed ({result.returncode}): "
            f"{result.stderr.strip() or 'no stderr'}"
        )
    return None


def repo_slug() -> str:
    url = _git("remote", "get-url", "origin").strip()
    scp_match = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+)", url)
    if scp_match:
        owner, repo = scp_match.groups()
    else:
        url_match = re.fullmatch(
            r"(?:https://github\.com/|ssh://git@github\.com/)([^/]+)/([^/?#]+)",
            url,
        )
        if not url_match:
            raise CheckError(
                f"cannot derive owner/repo from GitHub origin remote {url!r}"
            )
        owner, repo = url_match.groups()

    repo = repo.removesuffix(".git")
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner
    ) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise CheckError(f"GitHub origin does not carry a valid owner/repo: {url!r}")
    return f"{owner}/{repo}"


# --- Check 1: the workflow run ----------------------------------------------


def fetch_workflow_runs(slug: str, sha: str) -> list[dict]:
    if shutil.which("gh") is None:
        raise CheckError(
            "the GitHub CLI (gh) is not installed, so the workflow-run check "
            "cannot run. Install gh, or pass --skip-workflow to check only "
            "the published image."
        )
    result = _run(
        [
            "gh",
            "api",
            "--paginate",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{slug}/actions/runs?head_sha={sha}&per_page=100",
        ]
    )
    if result.returncode != 0:
        raise CheckError(
            f"gh api call for workflow runs failed: {result.stderr.strip()}"
        )
    try:
        pages = _decode_json_pages(result.stdout)
    except json.JSONDecodeError as error:
        raise CheckError(f"unparseable gh api response: {error}") from error
    return _combine_paginated_items(pages, "workflow_runs")


def fetch_workflow_jobs(slug: str, run_id: int, run_attempt: int) -> list[dict]:
    result = _run(
        [
            "gh",
            "api",
            "--paginate",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{slug}/actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100",
        ]
    )
    if result.returncode != 0:
        raise CheckError(
            f"gh api call for workflow jobs failed: {result.stderr.strip()}"
        )
    try:
        pages = _decode_json_pages(result.stdout)
    except json.JSONDecodeError as error:
        raise CheckError(
            f"unparseable gh api response for workflow jobs: {error}"
        ) from error
    return _combine_paginated_items(pages, "jobs")


def _decode_json_pages(payload: str) -> list[object]:
    """Decode consecutive JSON documents emitted by ``gh api --paginate``."""
    decoder = json.JSONDecoder()
    pages: list[object] = []
    offset = 0
    while offset < len(payload):
        while offset < len(payload) and payload[offset].isspace():
            offset += 1
        if offset == len(payload):
            break
        page, offset = decoder.raw_decode(payload, offset)
        pages.append(page)
    if not pages:
        raise CheckError("paginated gh api response was empty")
    return pages


def _combine_paginated_items(pages: list[object], key: str) -> list[dict]:
    combined: list[dict] = []
    for page_number, page in enumerate(pages, start=1):
        if not isinstance(page, dict) or not isinstance(page.get(key), list):
            raise CheckError(
                f"paginated gh api response page {page_number} carried invalid {key}"
            )
        for item_number, item in enumerate(page[key], start=1):
            if not isinstance(item, dict):
                raise CheckError(
                    f"paginated gh api response page {page_number} {key} item "
                    f"{item_number} is not an object"
                )
            combined.append(item)
    return combined


def select_build_run(
    runs: list[dict], workflow_name: str, branch: str, sha: str
) -> dict | None:
    """Pick the newest `workflow_name` run for `branch`, re-runs included.

    GitHub returns the same workflow once per attempt and once per event
    (a `push` run and a `pull_request` run share a head SHA). The publish
    only happens on the branch push, so pull_request runs are discarded.
    """
    candidates = [
        run
        for run in runs
        if run.get("name") == workflow_name
        and run.get("head_branch") == branch
        and run.get("head_sha") == sha
        and run.get("event") == "push"
    ]
    if not candidates:
        return None
    for run in candidates:
        for field in ("id", "run_number", "run_attempt"):
            value = run.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise CheckError(
                    f"candidate {workflow_name!r} run field {field!r} must be a "
                    f"positive integer, got {value!r}"
                )
    return max(
        candidates,
        key=lambda run: (run["run_number"], run["run_attempt"]),
    )


def select_publish_job(jobs: list[dict]) -> dict | None:
    matches = [job for job in jobs if job.get("name") == PUBLISH_JOB_NAME]
    if len(matches) > 1:
        raise CheckError(
            f"the Tests attempt carried {len(matches)} jobs named {PUBLISH_JOB_NAME!r}; "
            "exactly one is required"
        )
    return matches[0] if matches else None


# --- Check 2: the published build marker ------------------------------------


def _env_list_to_mapping(
    env: list[str], *, context: str = "image config"
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in env:
        name, separator, value = entry.partition("=")
        if separator:
            if name in (MARKER_ENV, COMMIT_ENV) and name in mapping:
                raise CheckError(f"{context} carries duplicate {name} entries")
            mapping[name] = value
    return mapping


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CheckError(
                f"imagetools output carries duplicate JSON object key {key!r}"
            )
        value[key] = item
    return value


def parse_imagetools_config(payload: str) -> dict[str, str]:
    """Pull the image's env mapping out of `imagetools inspect` JSON.

    The payload must contain exactly the supported ECM platform configs. Both
    must carry one copy of each marker and agree before one mapping can
    represent the current tag.
    """
    try:
        data = json.loads(payload, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as error:
        raise CheckError(f"unparseable imagetools output: {error}") from error

    if not isinstance(data, dict):
        raise CheckError("imagetools output carried no image configs")
    actual_platforms = set(data)
    if actual_platforms != set(EXPECTED_PLATFORMS):
        raise CheckError(
            f"imagetools platform set mismatch: expected {', '.join(EXPECTED_PLATFORMS)}; "
            f"found {', '.join(sorted(actual_platforms)) or 'none'}"
        )

    configs: list[tuple[str, dict]] = []
    for platform in EXPECTED_PLATFORMS:
        value = data[platform]
        if not isinstance(value, dict) or "config" not in value:
            raise CheckError(
                f"imagetools output carried no image config for {platform}"
            )
        configs.append((platform, value))

    mappings: list[dict[str, str]] = []
    for platform, entry in configs:
        env = entry.get("config", {}).get("Env")
        if not isinstance(env, list) or not all(isinstance(item, str) for item in env):
            raise CheckError(
                f"imagetools output carried no image config env block for {platform}"
            )
        mapping = _env_list_to_mapping(env, context=f"image config for {platform}")
        for marker in (MARKER_ENV, COMMIT_ENV):
            if marker not in mapping:
                raise CheckError(f"image config for {platform} carries no {marker}")
        mappings.append(mapping)

    for marker in (MARKER_ENV, COMMIT_ENV):
        values = {mapping[marker] for mapping in mappings}
        if len(values) != 1:
            raise CheckError(
                f"image platforms disagree on {marker}: {sorted(values)!r}"
            )
    return mappings[0] | {PLATFORMS_KEY: ", ".join(platform for platform, _ in configs)}


def read_marker_via_imagetools(ref: str) -> dict[str, str]:
    result = _run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            ref,
            "--format",
            "{{json .Image}}",
        ]
    )
    if result.returncode != 0:
        raise CheckError(
            f"docker buildx imagetools inspect {ref} failed: {result.stderr.strip()}"
        )
    return parse_imagetools_config(result.stdout)


def read_marker_via_pull(ref: str) -> dict[str, str]:
    """The restore drill's image gate: drop the local tag, pull, read marker."""
    _run(["docker", "rmi", ref], timeout=120)  # Best effort; in-use tags stay.
    pull = _run(["docker", "pull", ref], timeout=1800)
    if pull.returncode != 0:
        raise CheckError(f"docker pull {ref} failed: {pull.stderr.strip()}")
    inspect = _run(["docker", "inspect", ref, "--format", "{{json .Config.Env}}"])
    if inspect.returncode != 0:
        raise CheckError(f"docker inspect {ref} failed: {inspect.stderr.strip()}")
    try:
        env = json.loads(inspect.stdout)
    except json.JSONDecodeError as error:
        raise CheckError(f"unparseable docker inspect output: {error}") from error
    if not isinstance(env, list):
        raise CheckError("docker inspect returned no env list")
    return _env_list_to_mapping(env)


def read_published_marker(ref: str, *, use_pull: bool) -> dict[str, str]:
    if shutil.which("docker") is None:
        raise CheckError(
            "docker is not installed, so the published image cannot be read. "
            "Pass --skip-image to check only the workflow run."
        )
    manifest = read_marker_via_imagetools(ref)
    if use_pull:
        host = read_marker_via_pull(ref)
        for marker in (MARKER_ENV, COMMIT_ENV):
            if host.get(marker) != manifest.get(marker):
                raise CheckError(
                    f"fresh-pull host {marker} mismatch: manifest carries "
                    f"{manifest.get(marker)!r}, host image carries {host.get(marker)!r}"
                )
        print("  OK: fresh-pull host markers match the registry manifest markers.")
    return manifest


# --- Reporting --------------------------------------------------------------


def _banner(text: str) -> str:
    return f"\n{text}\n{'=' * len(text)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "POST-MERGE check: inspect the exact workflow attempt and current "
            "mutable-tag markers. Run this AFTER `gh pr merge`, not before."
        )
    )
    parser.add_argument(
        "--commit",
        default="HEAD",
        help="Commit to verify (default: HEAD, i.e. the branch you just pulled).",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Registry image name.")
    parser.add_argument("--tag", default=DEFAULT_TAG, help="Published tag to read.")
    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help="Branch whose push triggers the publish (default: dev).",
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="After mandatory manifest inspection, remove and re-pull the tag "
        "and require the host image markers to match.",
    )
    parser.add_argument(
        "--skip-workflow", action="store_true", help="Skip the workflow-run check."
    )
    parser.add_argument(
        "--skip-image", action="store_true", help="Skip the published-image check."
    )
    args = parser.parse_args(argv)

    if args.skip_workflow and args.skip_image:
        print(
            "FATAL: --skip-workflow and --skip-image cannot be used together; "
            "at least one check is required.",
            file=sys.stderr,
        )
        return 2

    try:
        sha = resolve_commit(args.commit)
        subject = commit_subject(sha)
        expected = expected_version_at(sha)
    except CheckError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1

    ref = f"{args.image}:{args.tag}"
    failures: list[str] = []
    errors: list[str] = []
    orientation_error: str | None = None
    try:
        on_branch = commit_is_on_branch(sha, args.branch)
    except CheckError as error:
        on_branch = None
        orientation_error = f"branch orientation could not be determined: {error}"
        errors.append(orientation_error)

    print(_banner("Post-merge publish check"))
    print(f"  commit under test : {sha[:12]}  {subject}")
    print(f"  expected version  : {expected}   (frontend/package.json at that commit)")
    print(f"  published tag     : {ref}")

    if orientation_error is not None:
        print(f"\n  COULD NOT CHECK branch orientation: {orientation_error}")
    elif on_branch is False:
        print(
            f"\n  PRE-MERGE RUN. {sha[:12]} is not an ancestor of "
            f"'{args.branch}'. This check verifies what the registry carries "
            f"for a commit that is already ON {args.branch}; the registry "
            f"cannot publish a version {args.branch} does not have yet. "
            f"A mismatch below is EXPECTED here and is not a defect. "
            f"Re-run after `gh pr merge` and `git checkout {args.branch} "
            f"&& git pull`."
        )
    elif on_branch is None:
        print(
            f"\n  NOTE: this checkout has neither 'origin/{args.branch}' nor "
            f"'{args.branch}', so whether {sha[:12]} is already on "
            f"'{args.branch}' could not be determined. The two checks below "
            f"still run. Run `git fetch --no-tags origin {args.branch}` if you "
            f"want that context in the verdict."
        )

    # --- Check 1 ---
    print("\n[1/2] Tests run and reusable publish job")
    if args.skip_workflow:
        print("  SKIPPED (--skip-workflow)")
    else:
        try:
            slug = repo_slug()
            runs = fetch_workflow_runs(slug, sha)
            run = select_build_run(runs, WORKFLOW_NAME, args.branch, sha)
            if run is None:
                failures.append(
                    f"no {WORKFLOW_NAME!r} push run exists for {sha[:12]} on "
                    f"'{args.branch}'. The run has probably not been created "
                    f"yet; wait and re-run. (A documentation-only merge does "
                    f"create a run as of bead enhancedchannelmanager-5rwzy, "
                    f"but its image-build jobs skip, so it publishes nothing "
                    f"and the marker below legitimately does not move.)"
                )
                print(f"  no run found for {sha[:12]} on '{args.branch}'")
            else:
                status = run.get("status")
                conclusion = run.get("conclusion")
                print(
                    f"  run       : #{run.get('run_number')} attempt {run.get('run_attempt')}"
                )
                print(f"  status    : {status}")
                print(f"  conclusion: {conclusion}")
                print(f"  url       : {run.get('html_url')}")
                if status != "completed":
                    failures.append(
                        f"the build run is still {status!r}. Nothing has "
                        f"published yet; re-run this check when it finishes."
                    )
                elif conclusion != "success":
                    failures.append(
                        f"the build run concluded {conclusion!r}, so the "
                        f"publish gate refused to ship. The registry is "
                        f"serving an older build. Re-run the failed workflow "
                        f"from the URL above once the cause is understood."
                    )
                else:
                    run_id = run.get("id")
                    run_attempt = run.get("run_attempt")
                    if not isinstance(run_id, int) or not isinstance(run_attempt, int):
                        raise CheckError(
                            "the selected Tests run has no integer id/run_attempt; "
                            "attempt-specific job inspection is impossible"
                        )
                    jobs = fetch_workflow_jobs(slug, run_id, run_attempt)
                    publish_job = select_publish_job(jobs)
                    if publish_job is None:
                        failures.append(
                            f"the Tests attempt carried no {PUBLISH_JOB_NAME!r} job. "
                            "A green test rollup alone is not publish-job evidence."
                        )
                        print("  publish job: not found")
                    else:
                        publish_status = publish_job.get("status")
                        publish_conclusion = publish_job.get("conclusion")
                        print(f"  publish job status    : {publish_status}")
                        print(f"  publish job conclusion: {publish_conclusion}")
                        print(
                            f"  publish job url       : {publish_job.get('html_url')}"
                        )
                        if publish_status != "completed":
                            failures.append(
                                f"the reusable publish job is still {publish_status!r}. "
                                "Nothing has published yet; re-run this check when it finishes."
                            )
                        elif publish_conclusion != "success":
                            failures.append(
                                f"the reusable publish job concluded {publish_conclusion!r}, "
                                "so publish-job evidence is not successful. Re-run the failed Tests "
                                "workflow from the URL above once the cause is understood."
                            )
                        else:
                            print(
                                "  OK: the exact attempt's reusable publish job succeeded."
                            )
        except CheckError as error:
            errors.append(str(error))
            print(f"  COULD NOT CHECK: {error}")

    # --- Check 2 ---
    print(f"\n[2/2] Published build marker on {ref}")
    if args.skip_image:
        print("  SKIPPED (--skip-image)")
    else:
        try:
            env = read_published_marker(ref, use_pull=args.pull)
            actual = env.get(MARKER_ENV)
            built_from = env.get(COMMIT_ENV)
            platforms = env.get(PLATFORMS_KEY, "unknown")
            print(f"  manifest platforms: {platforms}")
            print(f"  {MARKER_ENV}   : {actual}")
            print(f"  {COMMIT_ENV}    : {built_from[:12] if built_from else 'missing'}")
            if actual is None:
                failures.append(
                    f"the published image carries no {MARKER_ENV}. The image "
                    f"predates the build-arg, or was not built by this repo's "
                    f"Dockerfile."
                )
            elif actual != expected:
                failures.append(
                    f"{MARKER_ENV} mismatch: expected {expected!r}, published "
                    f"tag {ref} carries {actual!r} ({COMMIT_ENV} marker: "
                    f"{built_from[:12] if built_from else 'missing'}). "
                    f"The registry is lagging the commit "
                    f"under test."
                )
            else:
                print(f"  OK: {MARKER_ENV} matches {expected}.")

            if built_from is None:
                failures.append(
                    f"the published image carries no {COMMIT_ENV}. The current "
                    "registry tag cannot be matched to the target SHA."
                )
            elif not re.fullmatch(r"[0-9a-f]{40}", built_from):
                failures.append(
                    f"the published {COMMIT_ENV} marker {built_from!r} is not a "
                    "full 40-character lowercase commit SHA."
                )
            elif built_from != sha:
                failures.append(
                    f"{COMMIT_ENV} mismatch: expected the exact SHA {sha}, but "
                    f"published tag {ref} carries {built_from}."
                )
            else:
                print(f"  OK: {COMMIT_ENV} matches the exact resolved SHA.")
        except CheckError as error:
            errors.append(str(error))
            print(f"  COULD NOT CHECK: {error}")

    print()
    # Keep the two streams in order when the caller pipes or redirects them.
    # Without the flush, stdout is block-buffered and the verdict below lands
    # ahead of the evidence it refers to.
    sys.stdout.flush()
    if failures:
        print(
            "FAIL: required workflow and current-tag evidence did not all pass.",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        if on_branch is False:
            print(
                f"\nReminder: {sha[:12]} is NOT on '{args.branch}'. This is a "
                f"POST-MERGE check; run it again after the merge lands.",
                file=sys.stderr,
            )
        else:
            print(
                "\nSee docs/shipping.md section 6, step 'Check publish evidence "
                "and current tag markers'. Do not leave dev and the registry diverged: "
                "re-run the failed workflow rather than waiting for the next "
                "merge to republish by accident.",
                file=sys.stderr,
            )
        if errors:
            print("\nINCOMPLETE: one or more checks could not run.", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
        return 1

    if errors:
        print(
            "INCOMPLETE: no mismatch found, but a check could not run.", file=sys.stderr
        )
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    # Claim only what actually ran. A PASS line that asserts the registry
    # carries the right marker after `--skip-image` is a lie the operator
    # has no way to see through.
    if args.skip_image:
        print(
            f"PASS: exact Tests attempt and reusable publish job succeeded for "
            f"{sha} (mutable-tag marker check skipped)."
        )
    elif args.skip_workflow:
        print(
            f"PASS: current mutable tag {ref} exposes {MARKER_ENV}={expected} and "
            f"{COMMIT_ENV}={sha} (workflow evidence skipped; point-in-time marker "
            "check only)."
        )
    else:
        print(
            f"PASS: exact Tests attempt and reusable publish job succeeded, and "
            f"current mutable tag {ref} exposes {MARKER_ENV}={expected} and "
            f"{COMMIT_ENV}={sha}. This point-in-time check trusts registry writers; "
            "it does not bind image bytes to the workflow job."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
