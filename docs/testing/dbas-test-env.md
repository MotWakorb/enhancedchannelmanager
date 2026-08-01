# DBAS Round-Trip Test Environment

Test-environment strategy for the ECM ↔ Dispatcharr **DBAS** (Dispatcharr
Backup-And-Sync) round-trip, owned by QA. Implements bead
`enhancedchannelmanager-zqtjj`.

Tooling lives in [`tests/dbas-test-env/`](../../tests/dbas-test-env/): see its
`README.md` for the exact bring-up / seed / teardown commands. This doc covers
the *why* and the *strategy*.

## The problem this solves

The epic success signal, a clean full 13-category round-trip on a clean
Dispatcharr, is **untestable with the current suite**. There is no Dispatcharr
in `docker-compose.yml` or `docker-compose.mcp.yml`, and every
Dispatcharr-touching ECM test mocks the client
(`backend/tests/fixtures/mock_dispatcharr.py`). Those mocks **encode our
assumptions** of Dispatcharr's async behavior (the M3U 2-stage refresh polling,
the EPG download wait, the `is_active` workaround, `/api/accounts/users/me/`),
which are exactly the behaviors Phase 2 has to *validate*. A mock that re-states
our assumptions can never falsify them.

This environment provides a **live, pinned** Dispatcharr to test against, so the
round-trip can be exercised for real before the importers
(`.10`/`.11`/`.14`/`.15`/`.18`/`l1p4p`) ship.

## Version pin

| What | Value | Source |
|------|-------|--------|
| Pinned image | `dispatcharr/dispatcharr:0.26.0` | operator's live instance |
| Behavioral floor in ECM code | `0.23.0+` | `config.py` throttle; `auth/providers/dispatcharr.py` users/me |
| Version-detect endpoint | `GET /api/core/version/` | spike `tsfv0` |
| 0.26.0 digest (2026-06-07) | `sha256:9275b0c1f5319a84b412af6839a1364ef25b61d8d6ff0d2130a6c2b2504a8b07` | Docker Hub |

ECM encodes a 0.23.0+ **floor**; 0.26.0 is the **target**. The
`validate-version-behaviors.py` probe re-checks the 0.23.0-era behaviors against
0.26.0. They may have shifted across 0.24–0.26. **Bump procedure:** when the
operator upgrades, change `DISPATCHARR_VERSION` in
`tests/dbas-test-env/.env`, re-run the validator, refresh the digest comment.

## Topology: why modular, not all-in-one

The stack runs Dispatcharr in **modular** mode: `web` + `celery` + `postgres:17`
+ `redis:7`. The **celery worker is load-bearing for this test's purpose**:
Dispatcharr's M3U refresh and EPG import are *asynchronous*: the API returns
"initiated" immediately and celery does the work in the background, which is
precisely the 2-stage-poll / download-wait behavior ECM must validate. An
all-in-one image would hide that seam. Postgres on host port **5436** exists
only for snapshot capture/load; the app talks to it over the internal network.

## Seed-data strategy: production-shaped, not hand-crafted

Per QA test-data policy, **integration/E2E seed comes from production snapshots,
not synthetic fixtures.** Hand-crafted minimal fixtures would defeat the point:

| Importer risk | Why production shape matters |
|---------------|------------------------------|
| `.14` 4-tier stream matcher | needs **real stream cardinality + duplication**: a toy set never forces the matcher to disambiguate |
| `.15` logos importer (streaming/OOM) | needs **real logo volume**: the streaming-upload / OOM path only triggers at scale |
| `.10` M3U group matching | needs real group/stream distributions |

### Capture / load

1. **Capture** (`capture-snapshot.sh`): `pg_dump` from the operator's live
   Dispatcharr DB (preferred, real shape) **or** from the throwaway stack after
   populating it from real upstreams. No PII in this data, so it's captured
   directly: no anonymization step.
2. **Load** (`load-snapshot.sh`): restore into the throwaway Postgres, then
   restart web+celery, then apply the edge supplement.

The `.sql.gz` dump is **git-ignored** (large, machine-local). The committed,
reviewable artifacts are `seed/SNAPSHOT_MANIFEST.txt` (row counts per category)
and the tooling. Regenerate on demand. Seed is versioned alongside the schema:
re-capture after a Dispatcharr version bump.

### Categories: 12, not 13

DBAS scope **excludes plugins** (decision **D10**). So the seed reproduces
**12** categories, not 13. The categories are the ones the round-trip must
faithfully recreate (M3U accounts, EPG sources, channels, channel groups,
streams, logos, users, profiles, etc.; see the manifest for the live set).

### Edge supplement: the cases snapshots miss

Production snapshots cover the *common* case. The edge supplement
(`seed/edge_supplement.py`, applied via the stable API contract, not raw
SQL) adds the adversarial cases each importer must survive:

| Edge case | Importer it stresses |
|-----------|----------------------|
| **Empty category** (zero-member group) | `.10` group matching (no divide-by-zero / skip) |
| **Unicode names** (CJK, RTL, emoji, combining marks) | normalization + matching unicode-safety |
| **Max-length names** (≈255 chars) | round-trip must not silently truncate |
| **Known duplicate stream set** | `.14` 4-tier matcher: deterministic ambiguity to assert against |
| **Foreign-admin-lockout (D11)** | users importer must never lock out / clobber a non-ECM superuser; per spike `tsfv0` the privilege flags (`is_superuser`/`is_staff`/`user_level`) are the real escalation surface: restore conservatively, never escalate |

## CI-fast-path strategy: the fake must be validated, not assumed

Standing up postgres+redis+celery per PR is too slow for the per-PR gate. The
fast path is a **faithful Dispatcharr fake** for unit/integration runs. The
hard rule (acceptance criterion #3):

> A CI fake **must be validated against the live/pinned instance**. A fake that
> just re-encodes our assumptions is worth nothing. It's the same trap as the
> current mocks.

### Validation approach (specified; full fake to be built in a follow-up)

1. **Ground truth = the live probe.** `validate-version-behaviors.py` runs
   against the pinned 0.26.0 stack and records, for each behavior ECM depends
   on, the **real response shape**: version-detect payload, `users/me` (+ the
   `/api/accounts/me/` fallback), login-throttle 429, and the M3U-refresh /
   EPG-import / streams / logos list shapes.
2. **Contract test pins the fake to ground truth.** The CI fake (successor to
   `mock_dispatcharr.py`) gets a **contract test** that asserts its responses
   match the recorded live shapes for those same endpoints. The contract
   fixtures are *captured from the live instance*, not authored by hand.
3. **Two-tier gate:**
   - **Per-PR (fast):** importer tests run against the validated fake.
   - **Nightly / pre-merge-to-`dev` (slow, full):** the same importer round-trip
     runs against the **live pinned stack** from this directory. Drift between
     the two tiers fails the slow gate and flags the fake as stale.
4. **Re-validate on version bump.** The contract fixtures are re-captured and
   the fake updated whenever `DISPATCHARR_VERSION` changes: the fake is never
   allowed to drift silently from the pinned reality.

This keeps the per-PR loop fast **and** keeps the fake honest: the fake's
correctness is *derived from* the live instance, never *assumed*.

## Cross-instance sync test strategy (epic `i39wu`)

Cross-instance live sync (`docs/adr/ADR-013-cross-instance-live-sync.md`) is
**"restore over HTTP"**: ECM gathers the config of **Dispatcharr-A** and PUSHES
it to **Dispatcharr-B** through B's WRITE API. The least-validated surface sync
depends on is exactly that write contract: `create` (returns the created object
with a NEW id), a **409 conflict** on a duplicate create, `update` mutation,
async write completion, which the *read*-oriented restore mocks never covered
(QA panel: the #1 sync test risk). Bead `enhancedchannelmanager-46pkq` builds the
test substrate for it. **Two-tier gate**, mirroring the restore gate above:

| Tier | What it proves | Where | Status |
|------|----------------|-------|--------|
| **Fast (per-PR)** | The sync ENGINE's convergence / idempotency / partial-failure logic against a FAITHFUL fake of B's write contract | `backend/tests/fixtures/sync_harness.py` + `backend/tests/tasks/test_sync_roundtrip.py` | **BUILT**: runs in the normal pytest suite |
| **Live (nightly / pre-merge)** | That the fake's write-contract fidelity holds against REAL Dispatcharr (the actual 409 shape, async write completion, real id assignment) | `tests/dbas-test-env/docker-compose.dbas-sync-test.yml` (two non-colliding A+B instances) | **DEFERRED**: scaffold only, **not run in CI** |

### Fast tier (BUILT): the stateful two-instance mock harness

The pre-existing engine tests (`test_dbas_sync_engine.py`) mock dest-B as
independent `AsyncMock` methods and assert on **call counts**: proving the
engine *calls* B, but not that B *converged* (a stateless mock's `get_*` ignores
prior `create_*`, so "idempotency" can only be checked against a *separate*
hand-built "already-converged" mock).

The harness instead models **B as a real instance**
(`StatefulDispatcharrFake`), where every write is APPLIED: `create_*` stores the row
and returns it with a NEW server-assigned id; a **duplicate `create_*`** (same
natural key) raises a real `httpx` **409**; `update_*` mutates; `delete_*` (the
rollback compensator) removes it, 404 when already gone. That statefulness makes
the keystone assertions REAL rather than call-count proxies:

- **Convergence**: `B.state_by_key() == A.state_by_key()` after an apply (every
  source entity present on B under its natural key; ids differ: B owns its own).
- **Idempotency**: a GENUINE second `run_sync` against the now-populated B is a
  no-op (importers match B's own state → `ALREADY_EXISTS_IDENTICAL`, zero creates).
- **Partial failure**: a real injected mid-sync write error on B drives the
  orchestrator's compensating rollback against the state B actually stored; the
  suite asserts B is left consistent and that a clean re-run **heals** (converges).
- **Never-sync-users (D3)** and **redact-by-default (D2)** end-to-end through the
  whole gather → redact → plan → importer → B-write pipeline.

The harness is the sync analogue of `tests/dbas/test_restore_roundtrip.py`: a
shareable keystone the downstream sync beads (`tjaey`, `kcxie`) extend.

> **A real orchestrator property the harness surfaces** (documented, not a bug):
> the rollback dispatch (`restore_orchestrator._delete_dispatch`) registers
> compensators only for M3U / group / profile / channel / stream / user, **not**
> `epg_source` or `stream_profile`. So a *late*-step failure can only
> `FAILED_ROLLBACK_INCOMPLETE` (those earlier-created rows are residue on B), vs
> an *early* failure that rolls back cleanly. Both are exercised; a clean re-run
> heals from either. If completeness of cross-instance rollback is later deemed
> required, registering those two compensators is the fix, tracked as a follow-up.

### Live tier (DEFERRED): what still needs a reachable Dispatcharr-B

The live two-stack tier is **scaffolded but unbuilt**: no second (or first)
Dispatcharr is reachable in this environment, so the compose file +
write-contract fidelity against reality were authored, not run. The fast mock
tier is the substrate that ships today; the live tier is the honest gap.

**DEFERRED follow-up checklist** (a reachable Dispatcharr-B is the hard
prerequisite; none of this could be live-validated at authoring time):

- [ ] **Single-instance `zqtjj` first-bring-up checklist closed.** The two-stack
      compose inherits every assumption in the single-instance stack (seed-admin
      env names, `entrypoint.celery.sh` path, modular env vars, snapshot tables).
      Those are still **authored-not-live-validated**: close
      `tests/dbas-test-env/README.md` → "First-bring-up validation checklist"
      against a live 0.26.0 BEFORE trusting the two-stack.
- [ ] **Bring up `docker-compose.dbas-sync-test.yml`** (`-p dbas-sync-testenv`)
      and confirm A (9601) + B (9602) both reach `healthy`, with B reachable from
      A at `http://dispatcharr-b-web:9191` (the `SyncTarget.base_url` analogue).
- [ ] **Capture the WRITE-API contract fixtures** from live B: the shapes the
      `StatefulDispatcharrFake` reproduces are `create_*` success body (does B echo
      the payload + a new `id`?), the **409 conflict** body on a duplicate
      create, `update_*` response, and **async write completion** semantics (does
      a create return synchronously, or is there a celery-backed settle the
      importers must poll for?). Pin the fake to these with a contract test, just
      like the restore fake (above).
- [ ] **Run the keystone round-trip against live A→B**: seed A, `run_sync`
      apply, assert B converged; re-run for idempotency; inject a failure
      (e.g. revoke B mid-run) for the partial-failure path. This is the live
      analogue of `test_sync_roundtrip.py`.
- [ ] **Wire the nightly/pre-merge job** that runs the live tier and **fails on
      drift** between the fake and live B (mirrors the restore two-tier gate).
- [ ] **SSRF chokepoint, live**: confirm `security/ssrf.py` validates B's
      `base_url` at execute time against the real in-cluster DNS name, and that
      the insecure-TLS escape hatch still emits its per-cycle audit row.

Until that checklist is closed, the per-PR mock tier is the **only** validated
sync gate, honest about what it does and does not cover: it proves the engine's
logic against a faithful *model* of B's write contract, not against B itself.

## Status / follow-ups

Authored as infra + tooling. **Not yet live-validated**: no Dispatcharr was
reachable from the authoring sandbox, and bringing up the throwaway stack was
out of scope to avoid contending with the live `ecm-ecm-1` work in flight. The
one-time first-bring-up checklist (seed-admin env names, edge-supplement
endpoint/field names, celery entrypoint path, snapshot table names) is in
`tests/dbas-test-env/README.md`. Building the validated CI fake itself is a
follow-up (specified above, gated on a first live bring-up to capture the
contract fixtures).

For cross-instance **sync** specifically: the fast mock tier (the stateful
two-instance harness) is **BUILT and running per-PR**; the live two-stack tier is
**DEFERRED** with the explicit checklist above: it needs a reachable
Dispatcharr-B, which this environment does not have.
