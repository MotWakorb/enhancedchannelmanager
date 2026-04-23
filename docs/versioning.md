# Versioning Scheme

> How to read an ECM version string, map a dev-build number to a commit, and check whether a specific fix is in the build you are running.

This page exists primarily for **external reporters** who want to verify that a fix they are tracking (a bead ID, a GitHub issue, a PR number) is included in the build they have deployed. If that is your situation, skip straight to [Checking whether a fix is in your build](#checking-whether-a-fix-is-in-your-build).

## Format

ECM versions follow this shape:

```
MAJOR.MINOR.PATCH-BUILD
```

- `MAJOR.MINOR.PATCH` — the target release. Until the target release is actually cut, this value is the **next planned release**, not a release that has already shipped. Example: `0.16.0-0051` means "dev tip aiming at the 0.16.0 cut, CI build #0051."
- `BUILD` — a zero-padded, monotonically increasing CI build number. Four digits today (`0040`, `0051`, ...). Used on dev builds only. Release cuts drop the `-BUILD` suffix entirely (see [Cut Mechanics](shipping.md#release-workflow-merging-to-main)).

The canonical version string lives in [`frontend/package.json`](../frontend/package.json) and is baked into the Docker image at build time via the `ECM_VERSION` build-arg. Every image tagged with a `-BUILD` suffix is a dev build; every image tagged `X.Y.Z` with no suffix is a promoted release.

## Yanked release note — 0.16.0

Version `0.16.0` was tagged and pushed to GHCR on 2026-04-20 and then **hard-rolled-back the same day** — the tag, GitHub Release, and GHCR image were all deleted before any external consumer pulled them. See [`docs/runbooks/v0.16.0-rollback.md`](runbooks/v0.16.0-rollback.md) for the full incident and [ADR-004](adr/ADR-004-release-cut-promotion-discipline.md) for the pre-cut gate that now blocks a repeat.

Because of the rollback, the current dev stream is still on `0.16.0-NNNN`. Per PO decision (grooming 2026-04-22, bd-eio04.10), there is no `0.16.1` release cut planned — dev continues to increment `BUILD` until a full `0.17.0` cut. External users running `0.16.0-NNNN` images are on dev builds, not a promoted release; the `[Unreleased]` section of [`CHANGELOG.md`](../CHANGELOG.md) is the canonical list of fixes awaiting a cut.

## Where to read the version

Four places all show the same string:

- **UI** — the footer (and About dialog) render `frontend/package.json` at build time.
- **Docker image label** — `docker inspect ecm-ecm-1 --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'`, or the GHCR tag itself.
- **Build-arg inside the container** — `docker exec ecm-ecm-1 sh -c 'echo $ECM_VERSION'`.
- **`package.json`** in the repo at the SHA the build was cut from.

All four are populated from the same source; if they disagree, something has been hand-edited post-build and the image should be treated as suspect.

## Checking whether a fix is in your build

You have a bead ID or PR number, you have a running ECM container, and you want to know: is the fix in?

### 1. Read the version you are running

```bash
docker exec ecm-ecm-1 sh -c 'echo $ECM_VERSION'
# Example output: 0.16.0-0051
```

### 2. Map the build number to a commit

Every dev build comes from exactly one commit on `dev`. The CI build workflow stamps the version onto the image, so the mapping is one-to-one, but it is not currently encoded in the image itself — you recover it from git by matching the build number against the version bump commit.

The version bump lands in `frontend/package.json` at the time of the build, so:

```bash
# Clone or update a local copy of the repo, then:
git fetch origin
git log --all --oneline --follow -S '"version": "0.16.0-0051"' -- frontend/package.json
# Expected: one commit, the one that set this version.
```

Alternative: if you know roughly when the build was cut, jump to the GitHub Actions run log. Each `build-amd64` run prints the resolved version in step "Extract version and set release channel" — the workflow run URL is the canonical audit trail.

The commit SHA that sets `frontend/package.json` to your `BUILD` number is the tip of the tree your image was built from.

### 3. Confirm the fix SHA is an ancestor

Once you have the tip SHA (from step 2) and the fix SHA (from the bead, the merged PR, or the CHANGELOG entry):

```bash
git merge-base --is-ancestor <fix-sha> <tip-sha> && echo "FIX PRESENT" || echo "FIX ABSENT"
```

This is a pure `git` check — no need to rebuild or rerun anything. Exit code 0 means the fix is in the build; exit code 1 means it is not.

### 4. (Cross-check) compare against CHANGELOG

If the bead ID or PR number appears in the `[Unreleased]` section of [`CHANGELOG.md`](../CHANGELOG.md) at the tip SHA, the fix is in. If it appears in a versioned section (`## [0.X.Y]`), the fix shipped in that release and every subsequent build. The CHANGELOG is the intended-audience view; `git merge-base` is the authoritative check.

## Worked example

> "Does build `0.16.0-0040` include the fix for bd-eio04.1 (unified NormalizationPolicy, closes GH #104)?"

1. **Build number → tip SHA.** `git log --all --oneline -S '"version": "0.16.0-0040"' -- frontend/package.json` returns one commit; call its SHA `abc1234`.
2. **Fix SHA.** bd-eio04.1 landed in PR #114; the merge-commit SHA is listed in the bead's close comment (or `git log --grep='bd-eio04.1' --oneline`).
3. **Ancestor check.** `git merge-base --is-ancestor <fix-sha> abc1234`. If the fix SHA was merged *before* the `0.16.0-0040` version bump, exit code 0 — fix present. If after, exit code 1 — fix absent.
4. **Sanity check.** Does `CHANGELOG.md` at `abc1234` mention `bd-eio04.1` under `[Unreleased]`? If yes, consistent with "fix present." If no, either the fix post-dates the build or the CHANGELOG entry was missed at merge time (file a bead).

## What this scheme does not guarantee

- **Monotone feature presence across releases.** A feature visible in `0.16.0-0051` can be absent from a later promoted release if the PO explicitly decides to revert or defer. Always check against the target release's CHANGELOG, not the build stream.
- **Reproducible binaries.** The `BUILD` number and commit SHA map is one-to-one, but the image bytes also depend on base-image digests and dependency resolver state at build time. For byte-identical reproducibility use the image digest (`docker inspect ... --format '{{ .Id }}'`), not the version string.
- **External identification of a release.** `0.16.0-0051` is an internal dev-build identifier; only a tagged release like `0.17.0` is a stable external reference. Do not cite dev builds in external bug reports without also providing the commit SHA.

## Related

- [`CHANGELOG.md`](../CHANGELOG.md) — Keep-a-Changelog log of user-facing changes. `[Unreleased]` lists the fixes awaiting the next cut.
- [`docs/shipping.md`](shipping.md) — mechanics of incrementing the build number (step 3) and cutting a release.
- [`docs/adr/ADR-004-release-cut-promotion-discipline.md`](adr/ADR-004-release-cut-promotion-discipline.md) — why a release cut is a deliberate, gated act and what G1–G7 enforce.
- [`docs/runbooks/v0.16.0-rollback.md`](runbooks/v0.16.0-rollback.md) — procedure that yanked 0.16.0.
