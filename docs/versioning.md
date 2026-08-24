# Versioning Scheme

> How to read an ECM version string, map a dev-build number to a commit, and check whether a specific fix is in the build you are running.

This page exists primarily for **external reporters** who want to verify that a fix they are tracking (a bead ID, a GitHub issue, a PR number) is included in the build they have deployed. If that is your situation, skip straight to [Checking whether a fix is in your build](#checking-whether-a-fix-is-in-your-build).

## Format

ECM versions follow this shape:

```
MAJOR.MINOR.PATCH-BUILD
```

- `MAJOR.MINOR.PATCH`: the target release. Until the target release is actually cut, this value is the **next planned release**, not a release that has already shipped. Example: `0.16.0-0051` means "dev tip aiming at the 0.16.0 cut, CI build #0051."
- `BUILD`: a zero-padded, monotonically increasing CI build number. Four digits today (`0040`, `0051`, ...). Used on dev builds only. Release cuts drop the `-BUILD` suffix entirely (see [Cut Mechanics](shipping.md#release-workflow-merging-to-main)).

The canonical version string lives in [`frontend/package.json`](../frontend/package.json) and is baked into the Docker image at build time via the `ECM_VERSION` build-arg. Every image tagged with a `-BUILD` suffix is a dev build; every image tagged `X.Y.Z` with no suffix is a promoted release.

## Touchpoints

The version literal is hand-edited in **three** files. All three must move in lockstep on every bump.

**This is enforced** by [`backend/tests/unit/test_version_touchpoint_consistency.py`](../backend/tests/unit/test_version_touchpoint_consistency.py), which runs in the backend pytest suite (a required PR check). The invariant it pins is *every declaration of the ECM application version agrees with the canonical source* — stated as a property, not as a list of three paths, because the guard it replaces was a hardcoded list and a fourth declaration added elsewhere was outside it by construction. The test discovers declarations by scanning the product tree, and separately requires the discovered set and the table below to be **the same set**, so a fourth touchpoint cannot be added silently in either direction: undocumented code fails, and undiscoverable documentation fails.

It lives in the pytest suite rather than a dedicated CI job on purpose. Its predecessor — the `version-consistency` job and `scripts/check_version_consistency.py` — was deleted wholesale in the CI gate reduction, and the lockstep then went unguarded for months. A test inside the suite cannot be removed by trimming workflow files.

**What it does not check: advancement.** It asserts the touchpoints *agree*, not that a code change *bumped* them. `scripts/check_version_advances.py` covered that and is also gone; deciding whether a change earns a bump remains a convention followed by hand ([`docs/shipping.md`](shipping.md#3a-first-decide-whether-this-change-gets-a-version-bump-at-all) steps 3a/3b).

**Lockstep governs how you bump, not whether you bump.** A change containing only approved root machine-generated `.beads` state gets **no** bump: it carries no build to advance, and taking a build number a concurrent branch already holds is pure conflict. Every other path, including documentation, images, workflow support files, and beads configuration, is a code-gate input. [`scripts/classify_changed_paths.py`](../scripts/classify_changed_paths.py) is the arbiter. Decide with [`docs/shipping.md`](shipping.md#3a-first-decide-whether-this-change-gets-a-version-bump-at-all) → step 3a, and confirm it mechanically at [step 6a](shipping.md#6a-confirm-the-bump-decision-before-opening-the-pr), after the branch is committed. Do not run the classifier before the commit: with no branch diff to read it reports `code_paths_changed=true` for every change, which is an empty-input default rather than a verdict. Leaving all three touchpoints untouched keeps them agreeing, which is what the rule wants on an exempt machine-state PR.

| File | Line shape | Read by | Why it exists |
| --- | --- | --- | --- |
| [`frontend/package.json`](../frontend/package.json) | `"version": "X.Y.Z-NNNN"` | `build.yml` (`jq -r .version` → `ECM_VERSION` build-arg → `/api/version` env, UI header status pill, Docker label) | Canonical source. Baked into the image. |
| [`backend/routers/backup.py`](../backend/routers/backup.py) | `APP_VERSION = "X.Y.Z-NNNN"` | Backup-export manifest (`version` field); also re-imported by `routers/channel_pipeline.py` for the rule-export `ecm_version` field | Stamps backups with the version that produced them so DBAS restore can gate on the source version. |
| [`backend/main.py`](../backend/main.py) | `version="X.Y.Z-NNNN"` (kwarg to `FastAPI(...)`) | OpenAPI schema (`/api/openapi.json` → `info.version`) | Surfaces in the auto-generated docs at `/api/docs`. Picked up by API-contract tests that diff the schema. |

When you add a fourth touchpoint:

1. Edit the file with the new literal in lockstep with the other three.
2. **Add a row to the table above.** This is not documentation hygiene — the table *is* the registry. `test_documented_touchpoints_and_discovered_touchpoints_are_the_same_set` parses it, and a declaration the table does not list fails the backend suite with the offending path named.
3. Confirm the guard can actually read your new file. It dispatches on file type (`.py` via AST, `.json` via `json.loads`, anything else via a text scan for a `version`-shaped assignment). If your touchpoint declares the version in a shape none of those recognise, the same test fails — extend `declared_versions_in_file()` rather than exempting the file.
4. Update any local tooling that reads the version.

### Not a touchpoint: `frontend/package-lock.json`

[`frontend/package-lock.json`](../frontend/package-lock.json) declares `"version"` twice — at the root and at the `packages[""]` self-entry — and is **deliberately not a touchpoint**. It is allowed to lag, and the guard excludes it by name.

The reasoning, so this is a decision on the record rather than an oversight:

- It has never tracked the `-BUILD` suffix. Today it reads `0.18.0` while the canonical version is on the `0.18.1` line; that is its normal condition, not drift that appeared.
- Nothing reads it for the application version. `build.yml` takes `jq -r .version frontend/package.json`; the lockfile's copy is npm's record of the package's own identity, consumed by npm alone.
- npm rewrites it only on install. Making it track would mean regenerating the lockfile on every build bump — turning a one-line edit into a dependency-resolution event, and producing a guaranteed conflict on every pair of concurrent PRs, since each would rewrite the same generated file.

It resyncs to whatever `MAJOR.MINOR.PATCH` is current the next time someone runs `npm install`. That is expected and needs no action. `test_package_lock_is_documented_as_a_non_touchpoint` pins this section in place, so the exclusion cannot quietly lose its rationale.

History of why this guard exists:

- **PR #277** (cherry-pick of bd-lkyg5 from `release/v0.16.1` to `dev`, 2026-05-13): the cherry-pick agent noticed `backend/routers/backup.py` was at `"0.16.0"` while `frontend/package.json` had been bumped 27 times to `"0.17.0-0027"`. The skew had been latent for months. It was only caught because the cherry-picked commit happened to touch `backup.py`. Fixed inline; bd-9rtlc filed.
- **bd-9rtlc audit** (2026-05-14): grep across the codebase surfaced a second long-standing skew: `backend/main.py` was at `"0.16.0-0003"` (FastAPI kwarg) while `frontend/package.json` was at `"0.17.0-0033"`. The FastAPI version only shows in the OpenAPI schema, which no external consumer cited, so nobody noticed for ~30 builds. Both touchpoints were re-synced at `"0.17.0-0034"`.
- **Guard removed, then restored** (bead `enhancedchannelmanager-ipcqx`): the `version-consistency` CI job that caught neither of the above — it postdated them — was itself deleted in the CI gate reduction at commit `3404d2d5`, along with `scripts/check_version_consistency.py`. The lockstep then ran unguarded. Until 2026-08-23 this section still ended by crediting that job with blocking all future divergence: a claim that was true when written, never revisited when the job was deleted, and directly contradicted by the Touchpoints section immediately above it. A reader who stopped at the history came away believing they were protected. Enforcement now lives in the backend pytest suite — see [Touchpoints](#touchpoints) — and `test_versioning_doc_does_not_claim_a_ci_guard_that_was_removed` pins the retired sentence out of this file so it cannot be reinstated.

## 0.16.0: yanked first attempt, then re-cut

An initial `0.16.0` build was tagged and pushed to GHCR on 2026-04-20 and then **hard-rolled-back the same day**. The tag, GitHub Release, and GHCR image were all deleted before any external consumer pulled them. See [`docs/runbooks/v0.16.0-rollback.md`](runbooks/v0.16.0-rollback.md) for the full incident and [ADR-004](adr/ADR-004-release-cut-promotion-discipline.md) for the pre-cut gate that now blocks a repeat.

**0.16.0 was successfully re-cut and shipped on 2026-05-12.** The shipping release incorporates everything that was intended for the first attempt plus the blocking bug fixes; see the `## [0.16.0]` entry in [`CHANGELOG.md`](../CHANGELOG.md). The `v0.16.0` tag and GHCR image from that date are the canonical promoted release. The 2026-04-20 rollback was the first attempt only, not a permanent yank of the 0.16.x line.

Three further releases have since been promoted: **0.17.0** (2026-05-16), **0.17.1** (2026-05-22), and **0.17.2** (2026-05-23). `dev` now increments `BUILD` toward the next planned release as `0.17.3-NNNN` (tip is `0.17.3-0000`): a 0.17.x patch line carrying Channel Pipeline stability fixes ahead of the larger 0.18.0 work. External users running `0.17.3-NNNN` images are on dev builds, not a promoted release; the `[Unreleased]` section of [`CHANGELOG.md`](../CHANGELOG.md) is the canonical list of fixes awaiting the next cut.

## Where to read the version

Four places all show the same string:

- **UI**: the header status pill (and About dialog) render `frontend/package.json` at build time.
- **Docker image label**: `docker inspect ecm-ecm-1 --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'`, or the GHCR tag itself.
- **Build-arg inside the container**: `docker exec ecm-ecm-1 sh -c 'echo $ECM_VERSION'`.
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

Every dev build comes from exactly one commit on `dev`. The CI build workflow stamps the version onto the image, so the mapping is one-to-one, but it is not currently encoded in the image itself. You recover it from git by matching the build number against the version bump commit.

The version bump lands in `frontend/package.json` at the time of the build, so:

```bash
# Clone or update a local copy of the repo, then:
git fetch origin
git log --all --oneline --follow -S '"version": "0.16.0-0051"' -- frontend/package.json
# Expected: one commit, the one that set this version.
```

Alternative: if you know roughly when the build was cut, jump to the GitHub Actions run log. Each `build-amd64` run prints the resolved version in step "Extract version and set release channel". The workflow run URL is the canonical audit trail.

The commit SHA that sets `frontend/package.json` to your `BUILD` number is the tip of the tree your image was built from.

### 3. Confirm the fix SHA is an ancestor

Once you have the tip SHA (from step 2) and the fix SHA (from the bead, the merged PR, or the CHANGELOG entry):

```bash
git merge-base --is-ancestor <fix-sha> <tip-sha> && echo "FIX PRESENT" || echo "FIX ABSENT"
```

This is a pure `git` check. No need to rebuild or rerun anything. Exit code 0 means the fix is in the build; exit code 1 means it is not.

### 4. (Cross-check) compare against CHANGELOG

If the bead ID or PR number appears in the `[Unreleased]` section of [`CHANGELOG.md`](../CHANGELOG.md) at the tip SHA, the fix is in. If it appears in a versioned section (`## [0.X.Y]`), the fix shipped in that release and every subsequent build. The CHANGELOG is the intended-audience view; `git merge-base` is the authoritative check.

## Worked example

> "Does build `0.16.0-0040` include the fix for bd-eio04.1 (unified NormalizationPolicy, closes GH #104)?"

1. **Build number → tip SHA.** `git log --all --oneline -S '"version": "0.16.0-0040"' -- frontend/package.json` returns one commit; call its SHA `abc1234`.
2. **Fix SHA.** bd-eio04.1 landed in PR #114; the merge-commit SHA is listed in the bead's close comment (or `git log --grep='bd-eio04.1' --oneline`).
3. **Ancestor check.** `git merge-base --is-ancestor <fix-sha> abc1234`. If the fix SHA was merged *before* the `0.16.0-0040` version bump, exit code 0: fix present. If after, exit code 1: fix absent.
4. **Sanity check.** Does `CHANGELOG.md` at `abc1234` mention `bd-eio04.1` under `[Unreleased]`? If yes, consistent with "fix present." If no, either the fix post-dates the build or the CHANGELOG entry was missed at merge time (file a bead).

## What this scheme does not guarantee

- **Monotone feature presence across releases.** A feature visible in `0.16.0-0051` can be absent from a later promoted release if the PO explicitly decides to revert or defer. Always check against the target release's CHANGELOG, not the build stream.
- **Reproducible binaries.** The `BUILD` number and commit SHA map is one-to-one, but the image bytes also depend on base-image digests and dependency resolver state at build time. For byte-identical reproducibility use the image digest (`docker inspect ... --format '{{ .Id }}'`), not the version string.
- **External identification of a release.** `0.16.0-0051` is an internal dev-build identifier; only a tagged release like `0.17.0` is a stable external reference. Do not cite dev builds in external bug reports without also providing the commit SHA.

## Related

- [`CHANGELOG.md`](../CHANGELOG.md): Keep-a-Changelog log of user-facing changes. `[Unreleased]` lists the fixes awaiting the next cut.
- [`docs/shipping.md`](shipping.md): mechanics of incrementing the build number (step 3) and cutting a release.
- [`docs/adr/ADR-004-release-cut-promotion-discipline.md`](adr/ADR-004-release-cut-promotion-discipline.md): why a release cut is a deliberate, gated act and what G1–G7 enforce.
- [`docs/runbooks/v0.16.0-rollback.md`](runbooks/v0.16.0-rollback.md): procedure that yanked 0.16.0.
