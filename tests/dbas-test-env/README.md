# DBAS round-trip test environment

A pinned, **throwaway** Dispatcharr instance + production-shaped seed tooling so
the ECM ↔ Dispatcharr DBAS round-trip success signal (epic
`enhancedchannelmanager-0i2vt`) can be tested against a **live** Dispatcharr
instead of mocks.

> Why this exists: every Dispatcharr-touching ECM test currently mocks the
> client. The mocks **encode our assumptions** of Dispatcharr's async behavior
> (M3U 2-stage refresh polling, EPG download wait, the `is_active` quirk,
> `/api/accounts/users/me/`) — which is exactly what Phase 2 must *validate*.
> This stack lets us validate against reality. Prereq for meaningful coverage of
> beads `.10` (M3U importer), `.11` (EPG importer), `al6e3`/`.14` (4-tier stream
> matcher), `.15` (logos importer), `.18` (pre-flight + rollback), and `l1p4p`
> (users importer).

Full rationale and the CI-fast-path strategy live in
`docs/testing/dbas-test-env.md`.

---

## Isolation contract — READ FIRST

This stack **must never touch** the production ECM stack (`ecm-ecm-1`,
docker-compose.yml/.mcp.yml) or the operator's live Dispatcharr.

- Always use the project name `-p dbas-testenv` (the compose file also sets
  `name: dbas-testenv`, but pass `-p` explicitly so it's unmistakable).
- Host ports are deliberately off the ECM/live ranges: **9591** (web, vs live
  9191), **5436** (postgres, vs 5432). ECM uses 6100/6143/6101.
- All volumes/network are project-scoped (`dbas-testenv_*`).
- **Tear down with `-v`** when done — it's throwaway.

---

## Bring it up

```bash
cd tests/dbas-test-env
cp .env.example .env            # optional: customize version/ports/creds

docker compose -p dbas-testenv -f docker-compose.dbas-test.yml up -d
# wait for the web healthcheck (GET /api/core/version/) to go healthy:
docker compose -p dbas-testenv -f docker-compose.dbas-test.yml ps
```

## Validate the pinned version's behaviors

This is the ground-truth probe — confirms the 0.23.0-era behaviors ECM encodes
still hold at the pinned version, and records the response shapes the CI fake
must reproduce:

```bash
DBAS_TEST_BASE_URL=http://localhost:9591 \
DBAS_TEST_ADMIN_USER=ecmtest DBAS_TEST_ADMIN_PASS=ecmtestpass \
python3 validate-version-behaviors.py
```

## Seed production-shaped data + edge cases

```bash
# 1. Capture a snapshot (PREFERRED: from the operator's live Dispatcharr DB):
DBAS_SNAPSHOT_PGHOST=<operator-db-host> DBAS_SNAPSHOT_PGPASSWORD=<pw> \
  ./capture-snapshot.sh live
#    ...or from the throwaway stack after you've populated it:
./capture-snapshot.sh testenv

# 2. Load it into the throwaway stack (also applies the edge supplement):
./load-snapshot.sh
```

The snapshot (`seed/dispatcharr-seed.sql.gz`) is **git-ignored** — large and
machine-local. The committed, reviewable artifacts are the **manifest**
(`seed/SNAPSHOT_MANIFEST.txt`, row counts) and the **tooling**. Regenerate the
dump on demand.

## Tear down

```bash
docker compose -p dbas-testenv -f docker-compose.dbas-test.yml down -v
```

---

## First-bring-up validation checklist (do this once per version bump)

The compose env vars and the edge-supplement endpoint/field names are
**best-effort against the pinned 0.26.0 schema** and must be confirmed on first
real bring-up. None of this could be live-verified at authoring time (no
Dispatcharr reachable from the authoring sandbox).

- [ ] **Seed-admin env names.** Confirm `DISPATCHARR_SUPERUSER_USERNAME` /
      `DISPATCHARR_SUPERUSER_PASSWORD` actually create the first admin on the
      pinned image. If not, do the first-run wizard once, then capture a
      snapshot so the admin is baked into the seed.
- [ ] **`entrypoint.celery.sh` path** exists in the pinned image
      (`/app/docker/entrypoint.celery.sh`).
- [ ] **`validate-version-behaviors.py` runs clean** — any WARN is a drift
      finding to route to the importer authors.
- [ ] **Edge-supplement endpoints** (`/api/channels/groups/`,
      `/api/channels/streams/`, `/api/accounts/users/`) and the privilege
      fields (`is_superuser`/`is_staff`/`user_level`) match the pinned schema.
- [ ] **Snapshot table names** in `capture-snapshot.sh` manifest query reflect
      the real schema (the row-count query is schema-introspecting, so it's
      tolerant, but eyeball the manifest).
