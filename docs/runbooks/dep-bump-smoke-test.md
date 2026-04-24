# Runbook: Dep-Bump Fresh-Image Smoke Test (ADR-001)

> Local fresh-image smoke check for dependency-upgrade PRs. Run this before opening any `bd-6rrl5` child PR (or any future major dep bump) to confirm the image still builds and the FastAPI app still serves the wired endpoints.
>
> **For backend ASGI-triplet bumps** (`fastapi`, `starlette`, `uvicorn`) this
> runbook is necessary but **not sufficient**. Additionally run the pre-merge
> dry-run gate in
> [Dep-Bump Backend ASGI Regression](./dep-bump-backend-asgi-regression.md#pre-merge-dry-run-gate-pr-170-and-any-future-asgi-triplet-bump),
> which exercises the rollback path before merge and verifies all four
> `ecm_*` SLI families emit under synthetic traffic — the silent-SLI-
> degradation class of regression the smoke matrix does not cover.

- **Severity**: Pre-merge gate (no paging) — workflow tool, not an incident runbook
- **Owner**: Project Engineer (run during dep-bump work); SRE (owns the underlying contract via ADR-001)
- **Last reviewed**: 2026-04-23
- **Related beads**: `enhancedchannelmanager-6rrl5.5` (this script), `enhancedchannelmanager-6rrl5` (epic), `enhancedchannelmanager-xnqgo` (ADR-001)
- **Related ADR**: [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md)

## Alert / Trigger

Manual trigger. Run before opening or refreshing a PR that bumps any of:

- `backend/requirements*.txt`
- `frontend/package.json` / `frontend/package-lock.json`
- `Dockerfile` / `Dockerfile.dev`
- `mcp-server/requirements.txt`

This complements the CI pre-merge gate (the `build-amd64` / `dast-scan` / `trivy-scan` jobs that ADR-001 extended to dep-bump PRs targeting `dev`). The local script catches the same class of failures *before* you push, saving a CI cycle.

## Symptoms (what failure looks like)

The script's exit code and per-check PASS/FAIL lines tell you the failure mode at a glance:

| Failure | What it usually means |
|-|-|
| `docker build` fails | A dep bump broke the build (missing system lib, Rust toolchain mismatch, transitive resolver conflict). Read the `RUN uv pip install` step output. |
| Container never reaches `/api/health` within 90s | The app crashed at import time. Run with `--keep-container` and `docker logs <name>` to see the traceback. |
| `[FAIL] schema_up_to_date` (or `up_to_date != true`) | Alembic head moved but migrations didn't run, or the schema endpoint regressed. |
| `[FAIL] auth_setup_required` | Auth router registration broken or response shape changed. |
| `[FAIL] auto_creation_rules_list` / `journal_list` | Local-DB router broken (import error, schema drift, ORM model break). |
| `[FAIL] channels_list` / `epg_sources_list` with non-500 code | These checks accept 200 OR 500 (Dispatcharr-proxy 500 is expected on a fresh container). A 404 means the route isn't registered; a 502/connect-refused means the app didn't start. |
| `[FAIL] journal_batch_id_filter` | The `bd-s4sph` `batch_id` query parameter regressed to 422. |

## Diagnosis

Run the script:

```bash
./scripts/smoke_test_dev_container.sh
```

Decision tree:

1. **Did `docker build` complete?**
   - If no: read the failing layer. For Python dep bumps, the `python-builder` stage's `uv pip install` is where transitive resolution failures show up. For frontend dep bumps, the `frontend-builder` stage's `npm install` / `npm run build` is the suspect.
2. **Did the container reach `/api/health` within 90s?**
   - If no: re-run with `--keep-container`, then `docker logs <container-name>`. Look for tracebacks in the first 50 lines (import errors typically surface immediately). If the container exited, `docker logs` still shows the final stdout/stderr.
3. **Did all 7 checks PASS?**
   - If yes: you're cleared to push. Open the PR.
   - If no: the failing check name maps directly to the broken router or contract. Fix locally, then re-run with `--no-build` (skips the docker build, reuses the existing image) for a fast retest.

To inspect manually after a failed run, use `--keep-container`:

```bash
./scripts/smoke_test_dev_container.sh --keep-container
# Output prints the URL (e.g. http://127.0.0.1:49689) and container name.
docker logs <container-name>
curl http://127.0.0.1:<port>/api/health/ready | jq
docker rm -f <container-name>   # When you're done
```

## Resolution

The script doesn't restore service — it gates a PR. If the smoke fails:

1. **Identify the broken contract** from the FAIL line.
2. **Fix locally** (edit code, adjust requirements pin, update migration).
3. **Re-run with `--no-build`** to validate quickly:
   ```bash
   ./scripts/smoke_test_dev_container.sh --no-build
   ```
   `--no-build` reuses the existing `ecm-smoke:<sha>` image. If you changed `requirements.txt` or `package.json`, drop `--no-build` so a fresh image is built.
4. **Repeat until green**, then open the PR. The CI gate will re-run the same checks against the GHCR-built image.

If the smoke shows real fresh-image-only breakage (passes locally with `docker exec uv pip install` but fails fresh), that is exactly the silent-skew case ADR-001 is designed to catch — fix it before merging, do not work around it.

## Escalation

This is a developer workflow check, not a paging condition. Escalation paths:

- **Script itself broken** (false positives, won't run on the host): file a bead and ping the SRE/Project Engineer who owns it. The script lives at `scripts/smoke_test_dev_container.sh`.
- **CI gate disagrees with local result** (script passes locally, CI fails on the same SHA, or vice versa): that's a real ADR-001 finding — capture both logs, file a bead with the divergence, and post in the dep-bump epic (`bd-6rrl5`).
- **Script reports systemic regression across multiple dep bumps**: hold the affected dep PRs, open a bead under the epic, and propose either a code fix or a targeted update to the smoke matrix.

## Post-incident

Not applicable — this is a pre-merge gate, not an incident response. After every dep-bump merge:

- [ ] Confirm the corresponding CI `build-amd64` / `dast-scan` / `trivy-scan` jobs were green on the PR (ADR-001 acceptance criterion 2).
- [ ] If the local script passed but CI failed: file a bead under `bd-6rrl5` capturing the divergence — that's an ADR-001 gap to close.
- [ ] If you discovered a check that should be in the matrix but isn't, edit `scripts/smoke_test_dev_container.sh`, add a row to the matrix above, and update this runbook in the same PR.

## Reference

### Endpoint matrix (current)

| Check | Path | Expected | What it validates |
|-|-|-|-|
| `schema_up_to_date` | `GET /api/health/schema` | 200 + `up_to_date=true` | Alembic head matches applied revision |
| `auth_setup_required` | `GET /api/auth/setup-required` | 200 + `required=true` | Auth stack alive on a fresh (no-users) container |
| `channels_list` | `GET /api/channels?limit=1` | 200 OR 500 | Channels CRUD route registered + handler runs (500 = Dispatcharr unreachable, expected) |
| `epg_sources_list` | `GET /api/epg/sources?limit=1` | 200 OR 500 | EPG sources route registered + handler runs |
| `auto_creation_rules_list` | `GET /api/auto-creation/rules?limit=1` | 200 | Auto-creation router + local DB |
| `journal_list` | `GET /api/journal?page_size=1` | 200 | Journal router + local DB |
| `journal_batch_id_filter` | `GET /api/journal?page_size=1&batch_id=00000000` | 200 | `bd-s4sph` batch_id filter still accepts the documented shape |

### Script flags

```bash
./scripts/smoke_test_dev_container.sh                # build + run + check + tear down (default)
./scripts/smoke_test_dev_container.sh --no-build     # reuse existing ecm-smoke:<sha> image (fast retest)
./scripts/smoke_test_dev_container.sh --keep-container  # leave container running for manual debug
./scripts/smoke_test_dev_container.sh --json         # emit a machine-readable JSON report on stdout
./scripts/smoke_test_dev_container.sh --image TAG    # override the image tag (default: ecm-smoke:<short-sha>)
```

### Isolation guarantees

- The script never touches the running `ecm-ecm-1` container or its image — it tags its image as `ecm-smoke:<short-sha>` and names the container `ecm-smoke-<short-sha>-<pid>`.
- The smoke container binds to `127.0.0.1:<random-ephemeral-port>` only — never reachable from the network, never collides with the developer's running ECM port.
- The smoke container uses `docker run --rm` and a cleanup trap so it tears itself down on success, failure, Ctrl-C, or terminal hangup (unless `--keep-container` is set).

### Related

- [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md) — the contract this script implements
- `.github/workflows/build.yml` — the CI counterpart (`build-amd64`, `dast-scan`, `trivy-scan` jobs)
- Epic: `enhancedchannelmanager-6rrl5` — dep-bump children that consume this script
