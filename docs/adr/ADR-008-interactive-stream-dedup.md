# ADR-008: Interactive Stream-to-Channel Deduplication

- **Status**: Accepted
- **Date**: 2026-05-16 (proposed) / 2026-05-16 (accepted)
- **Author**: IT Architect persona (on behalf of PO), encoding the 2026-05-15 team-plan ratification (10 personas) and the 2026-05-16 D4 override (API naming, post-Code-Reviewer)
- **Bead**: `enhancedchannelmanager-2pd5i` (BD-L)
- **Related**:
  - `enhancedchannelmanager-1v4ht` — Epic: Interactive stream-to-channel deduplication (v0.17.1); carries the full PO-ratified design record this ADR encodes
  - `enhancedchannelmanager-7xo8e` — BD-A: Matcher service (RapidFuzz + normalization + tiered confidence); the floor in §D2 is enforced here
  - `enhancedchannelmanager-0b6xj` — BD-B: Settings additions (threshold 60–100 default 80; M3U toast suppressor)
  - `enhancedchannelmanager-6by2n` — BD-C: `pending_merges` table + Alembic migration **0014** (bd-5w6jz idempotency pattern); schema in §D8 is the contract
  - `enhancedchannelmanager-kbqwb` — BD-D: `GET /api/channel-merges/candidates` endpoint
  - `enhancedchannelmanager-acqkb` — BD-E: Pending merges API (list / accept / dismiss)
  - `enhancedchannelmanager-a5lb2` — BD-F: Bulk M3U import hook → populate `pending_merges` queue
  - `enhancedchannelmanager-4vxjj` — BD-G: `StreamDedupModal.tsx` component + bespoke focus trap
  - `enhancedchannelmanager-u6ftw` — BD-H: Drag-drop integration + cancel highlight animation
  - `enhancedchannelmanager-1lznl` — BD-I: 'Add Stream' button integration
  - `enhancedchannelmanager-gfxrz` — BD-J: Pending Merges page + subnav badge + toast
  - `enhancedchannelmanager-ugzn4` — BD-K: Settings UI (threshold + toast toggle)
  - `enhancedchannelmanager-ft3hk` — BD-M: SLO-10 + Prometheus rules + dedup runbook stubs
  - `enhancedchannelmanager-0lsas` — BD-N: User guide + API reference + `architecture.md` update
  - `enhancedchannelmanager-70ylc` — BD-O: MCP dedup tools (`mcp-server/tools/dedup.py`); names in §D7 are the contract
  - `enhancedchannelmanager-7u8ms` — BD-P: Extend `add_stream` MCP tool with `dedup_action`
  - `enhancedchannelmanager-5w6jz` — Alembic 0006-0010 idempotency + smart-bootstrap fast-path pattern (BD-C inherits this)
  - `enhancedchannelmanager-r9mtd` — Auto-creation separate-not-merge collision detection (migration 0002); the **unattended** path, distinct from this ADR
  - `enhancedchannelmanager-qpgsx` (P3, backlog) — Bulk "Resolve All" actions (deferred from v0.17.1)
  - `enhancedchannelmanager-5136e` (P3, backlog) — `pending_merges` retention reaper (deferred; depends on SLO-10 alert firing)
  - `enhancedchannelmanager-bfbk8` (P3, backlog) — Systemic `ModalOverlay` focus trap (BD-G ships bespoke)
  - `enhancedchannelmanager-s7lxd` (P3, backlog) — Per-group threshold override (deferred from v0.17.1)
  - `docs/database_migrations.md` — Alembic authoring guide; migration 0014 lands per that doc
  - `docs/style_guide.md` — Naming conventions (plural-noun resource paths drove the §D1 D4-override)
  - `docs/architecture.md` — System overview (BD-N updates to reference this ADR's pipeline)
  - `docs/api.md` — API reference (BD-N adds the four endpoints in §D1)
  - `docs/adr/ADR-007-session-telemetry-retention.md` — Sibling ADR that pioneered the "bounded retention by construction" framing this ADR adopts for the `pending_merges` audit horizon
  - `docs/adr/ADR-005-code-security-gating-strategy.md` — The new router lands subject to CodeQL delta-zero at PR time
  - Epic body `bd-1v4ht` MCP tool names (`dedup_get_candidates`, `dedup_resolve_merge`) and `dedup_action` enum (`prompt_equivalent_fail | merge_if_exact | force_create`) predate this ADR; the names + enum in §D7 supersede the epic body

## Context

The Channel Manager today has no mechanical defense against an operator (or an AI agent through MCP) creating a duplicate channel from a stream whose name already matches an existing channel in the target group. The three trigger paths where the collision is most likely:

1. **Drag-drop** a stream into a group from the Channel Manager UI.
2. **'Add Stream' button** on a channel-less stream.
3. **Bulk M3U import / refresh** — the highest-volume path, where an operator can produce dozens to hundreds of latent duplicates in a single refresh.

Each path silently creates a new channel today. The operator only notices later, while reconciling the channel list, and the recovery is manual (find the dupes, decide which to keep, merge with the existing `MergeChannelsModal` editing surface). At low channel counts this is annoyance; at the bulk-M3U scale it is a quality-of-data problem that compounds over time and degrades the EPG-matching and auto-creation hot paths downstream.

The companion **auto-creation pipeline** (migration 0002 / bd-r9mtd) already has its own unattended collision detection (`match_scope_target_group` + separate-not-merge option). That path is in scope for the operator's pre-configured rules and is deliberately **not** sharing a matcher with the interactive dedup work this ADR governs — see §D8 boundary note.

### Why this ADR must land before BD-A through BD-P start

The epic decomposes into 16 sub-beads (BD-A through BD-P, including this ADR as BD-L and the SRE/docs cross-cutters BD-M / BD-N) spanning the matcher service, schema migration, four API endpoints, four frontend integrations, settings, observability, documentation, and two MCP tool families. Without a contract-lock the surface area drifts: the API path naming alone went through one PO override (D4) after the Code Reviewer flagged the original `/api/dedup/*` convention violation, and that override has to land in the ADR *before* BD-D / BD-E start coding the routes. Other contract-lock decisions (the **confidence floor**, the **MCP tool names**, the **`pending_merges` schema**, the **audit field set**) are each gates that one or more sub-beads consume.

The 2026-05-15 team-plan ratified the design with all ten personas converging; the 2026-05-16 D4 override was the single change. This ADR encodes the ratified design verbatim and is **not** the place to re-litigate it.

### Threat-model carve-out (D2 confidence floor rationale)

The Security Engineer's position during grooming, adopted here: an operator-configurable confidence threshold without a hard integrity floor is a **bulk-destruction vector**. A misconfigured threshold of, say, 5% — settable today via Settings UI or by hand-editing `settings.json` — would cause the bulk-M3U-import path (BD-F) to mass-enqueue near-meaningless candidate merges, and one operator click on a "Resolve All" button (a deferred backlog item, `qpgsx`) would mass-destroy channels. The floor is a defense-in-depth constraint, not a UX setting. See §D2.

## Decision

### D1 — API surface: `/api/channel-merges/*` with verb sub-resources (D4 override from epic)

Four endpoints land under `backend/routers/channel_merges.py`:

- `GET /api/channel-merges/candidates?stream_name=X&group_id=Y` — synchronous lookup; returns the top-1 candidate (or none) for an incoming stream against the target group. Cacheable, idempotent, JWT-required.
- `GET /api/channel-merges?status=pending` — paginated queue list. Status is a query-param filter, default `pending`; passing `?status=merged` or `?status=dismissed` returns the terminal-state history rows the retention rule in §D3 keeps indefinitely.
- `POST /api/channel-merges/{id}/accept` — operator confirms the merge. Idempotent: a row already in `merged` returns 200 with the prior outcome envelope, not 409, so a double-click or a retry after a flaky network is safe.
- `POST /api/channel-merges/{id}/dismiss` — operator rejects the candidate. Also idempotent on terminal `dismissed`.

Pagination follows the existing list-endpoint pattern (`page`, `page_size` query params with sane defaults — 1/50; response envelope includes `total` and `total_pages`).

**Response envelope follows the existing ECM flat-outcome pattern.** No top-level `data` wrapper — the `POST /api/channels/merge` endpoint (`backend/routers/channels.py:1961`, established in bd-ct9wl) is the precedent: it returns the merged channel object directly. The `/accept` endpoint mirrors that, returning `{merged_into_channel_id, journal_entry_id, source_stream_id, confidence, ...}` flat. The `/dismiss` endpoint returns `{journal_entry_id, dismissed_at, ...}` flat. The `GET` endpoints return list/object payloads directly.

**Why `/api/channel-merges/*`, not the originally-ratified `/api/dedup/*`** (D4 override, 2026-05-16):

| Driver | Original `/api/dedup/*` | Chosen `/api/channel-merges/*` |
|---|---|---|
| Convention alignment with `/api/channels`, `/api/channel-groups`, `/api/m3u/accounts` (style guide naming discipline — plural-noun resource paths) | Violates — `dedup` is a singular implementation term | Matches — plural noun naming the domain entity |
| Domain vs. implementation framing | "Dedup" is the *algorithm category*; the operator never sees the word | "Channel merges" is the *operator-facing concept* — the page they navigate to is "Pending Merges" |
| Sub-resource readability | `/api/dedup/pending-merges/{id}/merge` reads redundantly ("merge a pending-merge into merged") | `/api/channel-merges/{id}/accept` reads cleanly |
| Style-guide alignment | Open question — Code Reviewer would have blocked at PR time | Resolved — no PR-time block expected |
| Operator MCP-call legibility | `dedup_resolve_merge` is the algorithm | `accept_channel_merge` is the action |

**Why `?status=pending` rather than `/api/channel-merges/pending`:** the collection is already `channel-merges`; tacking `/pending` on as a path segment would re-encode the lifecycle state in the URL where a query-param filter does the same job and composes cleanly with the planned `?status=merged` and `?status=dismissed` history views (one route, three states, one set of pagination semantics). It also keeps `/api/channel-merges/{id}/accept` and `/api/channel-merges/{id}/dismiss` unambiguous — `pending` is not at the same path-segment level as a numeric id, so the router never has to disambiguate them.

### D2 — Hard confidence floor (60%), defense-in-depth integrity constraint

The matcher service (BD-A) enforces a **hard floor of 60%** below which it refuses to emit a candidate, regardless of the operator-configured threshold. The operator-facing threshold (default 80%, configurable 60–100 in Settings → Channel Defaults — BD-B / BD-K) can be set as low as the floor but no lower. Below the floor, the matcher returns "no candidate" — silent refusal, **not** an offer-merge prompt with a low-confidence badge.

- **At the matcher API**: BD-A's `find_candidate(stream_name, group_id, threshold) -> Candidate | None` clamps `threshold = max(threshold, HARD_FLOOR)` before any RapidFuzz call. A request that asks for a 30% threshold gets 60% behavior; logged at DEBUG so the operator's intent is visible in postmortem traces but the action is safe.
- **At the settings persistence boundary** (BD-B): the `dedup_threshold` field's Pydantic validator rejects values below the floor with HTTP 422 (`"dedup_threshold must be >= 60"`). The Settings UI (BD-K) constrains the input control to the same range; the validator is the source of truth so an API-direct or `settings.json`-edited bypass also lands at the floor.
- **Hard floor value source of truth**: a module-level constant `CONFIDENCE_FLOOR = 60` in BD-A's matcher module. The Pydantic validator imports it so the two layers cannot drift.

**The matcher's clamp is the load-bearing enforcement; the validator is the early-rejection courtesy.** BD-A's test suite MUST include `test_find_candidate_returns_no_match_below_floor`: assert that `find_candidate(stream_name='X', candidates=[(name='Y', confidence=50%)], threshold=5)` returns no candidate — proves the floor holds even when threshold is set below it.

**Rationale.** Without the floor, an accidental or malicious threshold of 5% in the bulk-M3U-import path (BD-F) mass-enqueues low-quality pending rows. A subsequent "Resolve All" action (deferred backlog `qpgsx`, but plausible in v0.17.x) would then mass-destroy channels. The floor is the architectural backstop against this class of misconfiguration; it costs nothing at the happy path (the default 80% is well above it) and bounds the worst-case blast radius. The Security Engineer's veto-class concern, accepted.

**The floor is a single number, not a per-call override or a UX setting.** Changing it is an ADR addendum, not a runtime config change. This is intentional — the value of a defense-in-depth constant comes from it not being trivially loosenable.

### D3 — `pending_merges` state machine + retention

Each `pending_merges` row carries `status` ∈ {`pending`, `merged`, `dismissed`}. Transitions:

- `pending → merged` — via `POST /api/channel-merges/{id}/accept`. Writes a journal entry per §D6 and triggers the actual channel merge through the Dispatcharr client.
- `pending → dismissed` — via `POST /api/channel-merges/{id}/dismiss`. Writes a journal entry per §D6.
- `merged → (terminal)` — no transition out. A subsequent `/accept` on the same id is a no-op that returns the prior outcome envelope (idempotency, §D1).
- `dismissed → (terminal)` — no transition out. A subsequent `/dismiss` on the same id is a no-op.
- A pending row whose `candidate_channel_id` no longer resolves in Dispatcharr at `/accept` time returns 404 with detail `"target channel no longer exists — dismiss this pending merge and refresh"`. See §D4.

**Retention: indefinite for terminal rows in v0.17.1.** Merged and dismissed rows are kept so operators can review historical decisions. No automatic pruning, no cron sweep. This is deliberate for v1: the row volume is bounded by operator action (every row exists because a real M3U import or drag-drop happened), and the audit value is real ("when did we merge stream X into channel Y, and on whose authority?"). If the table grows large enough to matter, the **deferred retention reaper** (`enhancedchannelmanager-5136e`, P3) is the additive cleanup path — it lands only when SLO-10's queue-depth alert (BD-M) gives DBA a concrete signal that the table is becoming a problem. ADR-007's framing applies: bound-by-construction is preferable to bound-by-pruning, but here the bound (`operator-merge events × deployment lifetime`) is already small enough to defer.

### D4 — No local FK on `candidate_channel_id`; lazy resolution at accept time

`pending_merges.candidate_channel_id` is a **TEXT column with no foreign-key constraint**. Dispatcharr channel UUIDs live in Dispatcharr's database, not ECM's; an FK across the process boundary is not enforceable and the closest local approximation (a periodic reconciliation job) trades one operational surface for another with no analytic benefit.

The behavior on a deleted target channel is **lazy resolution at accept time**:

- `POST /api/channel-merges/{id}/accept` calls `client.get_channel(candidate_channel_id)` as its first step.
- A 404 from Dispatcharr returns HTTP 404 to the caller with detail `"target channel no longer exists — dismiss this pending merge and refresh"`.
- The operator's path is then `/dismiss` (which succeeds — the dismissal records the auto-cleanup intent in the journal) or re-running the original trigger (drag-drop, add stream, M3U refresh) after refreshing the channel list, which re-enqueues with a current candidate.

A reconciliation job that pre-detects orphan candidates is **not** built for v0.17.1. The lazy path covers correctness; the proactive path is additive if operator UX feedback warrants it (e.g., a stale pending-merges list showing N rows whose candidates have all been deleted in Dispatcharr). The `(candidate_channel_id)` index in §D8 supports an offline orphan-detection query if needed.

### D5 — Partial UNIQUE index on `(stream_name, candidate_channel_id) WHERE status='pending'`

Prevents duplicate **pending** rows from repeated bulk-M3U imports of the same stream against the same target channel. The constraint is partial (SQLite-supported via `CREATE UNIQUE INDEX ... WHERE`) so it does **not** conflict with terminal-state history rows — an operator can have a `merged` row for `(stream "ESPN HD", target channel UUID-A)` from last week's import and a fresh `pending` row for the same pair from this week's import without an `IntegrityError`. This is the right semantics for both "an operator un-merged manually and re-imports" and "a stream re-appears in the M3U after a provider hiccup".

If the bulk-M3U hook (BD-F) tries to enqueue a row that would collide with an existing `pending` row, the hook treats the existing row as the source of truth (oldest `created_at` wins) and does not overwrite. Logged at DEBUG so the operator can see in trace why a stream they expected to re-prompt did not.

### D6 — Audit field set (`pending_merge_journal` table)

Every accept / dismiss / queue / auto-aged-out action writes a row to the **new `pending_merge_journal` table** created in migration 0014 alongside `pending_merges` (see §D8 for the full schema). This is a discrete audit substrate, not a JSON blob — every field is a queryable column. The audit fields **cannot land on the existing `journal_entries` table** (PO decision, 2026-05-16 code-reviewer followup).

Fields:

- `actor_token_id` — **opaque token identifier**, not a username string. The token's database id, so subsequent revocation or rotation is traceable to the action that used the now-rotated credential. MCP-driven actions populate this with the MCP token's id; operator-driven actions populate this with the JWT session's underlying API token id.
- `action_type` — one of `merge_confirmed`, `merge_dismissed`, `auto_queued`, `auto_aged_out`. The last is reserved for the deferred retention reaper (`enhancedchannelmanager-5136e`); v0.17.1 only writes the first three.
- `source_channel_id` — the incoming Dispatcharr stream id that triggered the prompt.
- `target_channel_id` — the candidate Dispatcharr channel UUID.
- `confidence_score` — the RapidFuzz score captured **at action time**. The operator-configurable threshold can drift between queue and accept, so the score is what the operator was looking at when they decided, not what the threshold is now.
- `timestamp_utc` — epoch-ms, UTC.
- `trigger_context` — one of `drag_drop`, `add_stream`, `m3u_refresh`, `mcp_tool`. Distinct from `action_type` (what was decided) and from `actor_token_id` (who decided): this names the **surface** the decision came in through.

The `trigger_context` distinction matters for later analytical questions like "are MCP-agent merges accepted at a higher rate than operator-driven ones?" (the 1v4ht epic's stated motivation for distinguishing AI from operator decisions; complements rather than replaces the per-token attribution above).

### D7 — MCP tool surface mirrors REST

MCP tool names are encoded here verbatim so BD-O / BD-P engineers do not invent variants. All tools live in `mcp-server/tools/dedup.py` (BD-O) except the `add_stream` extension (BD-P), which lives alongside the existing `add_stream` implementation.

- `mcp__ecm__list_pending_channel_merges(group_id?, status?)` — paginated read; mirrors `GET /api/channel-merges?status=pending`. Both args optional; `status` defaults to `pending`.
- `mcp__ecm__accept_channel_merge(merge_id)` — mirrors `POST /api/channel-merges/{id}/accept`. The acting MCP-token id is recorded in the journal per §D6 (`actor_token_id`); `trigger_context` is `mcp_tool`.
- `mcp__ecm__dismiss_channel_merge(merge_id)` — mirrors `POST /api/channel-merges/{id}/dismiss`. Same audit shape.
- `mcp__ecm__add_stream(stream_name, group_id, dedup_action?)` — **extends** the existing `add_stream` MCP tool, does not replace it. The new `dedup_action` enum:
  - `prompt` (default) — async-queue the merge candidate and return the candidate list to the AI agent for the agent to decide-and-call-back via `accept_channel_merge` / `dismiss_channel_merge`. Mirrors the operator UI's modal prompt at MCP scale.
  - `force_new` — skip dedup entirely; create a new channel.
  - `merge_if_found` — auto-accept the top-1 candidate **if** its confidence is at or above the configured threshold. Below threshold (but above the §D2 floor by definition, because the matcher does not emit anything below the floor), this falls back to `prompt` semantics and returns the candidate list.

No `mcp__ecm__get_dedup_candidates` tool. The synchronous candidate-lookup behavior is covered by `add_stream(dedup_action='prompt')`, which returns the candidate list; carving the lookup into a separate tool would duplicate surface area without adding capability the agent does not already have through the `add_stream` extension.

**MCP tool error semantics:** When the underlying REST endpoint returns 4xx, the tool returns a structured `{error: {code, message}}` envelope and does NOT throw. The AI agent can then call `dismiss_channel_merge` to clean up a stale row referenced by `accept_channel_merge` that 404s on a deleted target channel (per §D4 lazy-resolution policy).

**MCP api_key holders are authorized to trigger merges as an intentional security posture** (ratified in the 2026-05-15 team-plan security position). The journal's `actor_token_id` + `trigger_context='mcp_tool'` is the audit trail. A future security audit asking "why can an MCP token mutate channels?" references this paragraph.

### D8 — `pending_merges` schema (informs BD-C migration **0014**)

> Migration revision is **0014**, not 0011 as the original BD-C bead title says. Current Alembic head as of v0.17.1-0002 (`backend/alembic/versions/`) is `0013_session_telemetry_channel_event_counters`. BD-C should update its bead title at implementation time; this ADR is the canonical reference for the migration number.

Columns:

| Column | Type | Constraint | Purpose |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Monotonic row id; the queue ordering key and the `accept` / `dismiss` route param |
| `stream_name` | TEXT | NOT NULL | Raw incoming stream name (the fuzzy-match key — normalization is applied by the matcher at compare time, not stored normalized here, so the operator sees what the M3U actually delivered) |
| `group_id` | INTEGER | nullable | Dispatcharr group id for scope; NULL is the "ungrouped import" case, treated as an open candidate scope by the matcher |
| `candidate_channel_id` | TEXT | NOT NULL | Dispatcharr channel UUID (no local FK — see §D4) |
| `confidence` | REAL | NOT NULL | RapidFuzz `token_set_ratio` score, 0.0–1.0. Always ≥ the §D2 floor (the matcher would not have emitted the row otherwise) |
| `status` | TEXT | NOT NULL DEFAULT 'pending', CHECK in ('pending','merged','dismissed') | State machine column (§D3) |
| `created_at` | INTEGER | NOT NULL | Epoch-ms (matches `session_telemetry` convention from ADR-007 / `skqln.2`) |
| `resolved_at` | INTEGER | nullable | Epoch-ms when the row left `pending`; NULL while pending |
| `resolution_source` | TEXT | nullable | One of `operator`, `auto`, `bulk_m3u_hook`, `mcp_tool`; NULL while pending |
| `trigger_context` | TEXT | NOT NULL, CHECK in ('drag_drop','add_stream','m3u_refresh','mcp_tool') | The surface that enqueued the row; mirrors the journal field of the same name (§D6) |

Indexes:

- `(group_id, created_at)` — the dominant **queue list per group** read path.
- `(status, created_at)` — sweeping `pending` rows (used by BD-J's Pending Merges page count badge query and by any future retention reaper).
- `(candidate_channel_id)` — supports offline orphan detection (§D4) without forcing a table scan.
- **Partial unique**: `CREATE UNIQUE INDEX uq_pending_merges_active ON pending_merges (stream_name, candidate_channel_id) WHERE status = 'pending'` — see §D5.

**Second table in migration 0014: `pending_merge_journal`**

The §D6 audit fields land here — a discrete, queryable audit substrate that is separate from `journal_entries` and separate from `pending_merges`.

| Column | Type | Constraint | Purpose |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Monotonic row id |
| `pending_merge_id` | INTEGER | NOT NULL, FK → `pending_merges.id` | Back-reference to the queue row. NOT NULL: every journal row is created in the context of a `pending_merges` row (drag-drop and add-stream both enqueue a `pending_merges` row before the operator acts; auto-aged-out rows reference the aging row). If a future path exists where no pending row is created (unlikely given §D3 and §D9), that is a schema addendum with its own ADR note |
| `actor_token_id` | TEXT | NOT NULL | Opaque token identifier — the token's DB id, not a username string (§D6) |
| `action_type` | TEXT | NOT NULL, CHECK in ('merge_confirmed','merge_dismissed','auto_queued','auto_aged_out') | What was decided (§D6) |
| `source_channel_id` | TEXT | NOT NULL | Dispatcharr stream UUID that triggered the prompt (§D6) |
| `target_channel_id` | TEXT | NOT NULL | Dispatcharr channel UUID that was the merge candidate (§D6) |
| `confidence_score` | REAL | NOT NULL | RapidFuzz score captured at action time, 0.0–1.0 (§D6) |
| `timestamp_utc` | INTEGER | NOT NULL | Epoch-ms, UTC — consistent with `pending_merges.created_at` and ADR-007 epoch-ms convention |
| `trigger_context` | TEXT | NOT NULL, CHECK in ('drag_drop','add_stream','m3u_refresh','mcp_tool') | The surface the decision came in through (§D6) |

Indexes on `pending_merge_journal`:

- `(pending_merge_id)` — look up all journal rows for a given pending merge row.
- `(timestamp_utc)` — time-range queries (audit log reviews, analytics).
- `(actor_token_id)` — revocation audits: "find all actions taken by this token."

**FK note:** `pending_merge_id` is a hard NOT NULL FK to `pending_merges.id`. The FK ordering in migration 0014 is unproblematic — `pending_merges` is created first in the same migration, so the FK is satisfiable within the single migration transaction. No circular dependency.

**Channel ID type note:** TypeScript clients receive `candidate_channel_id` as `string` (matches the stored TEXT column, matches ECM's Dispatcharr-UUID handling everywhere else — `backend/models.py:143` precedent). The epic body's `DedupCandidate.channel_id: number` is corrected to `string` at implementation time, mirroring the migration-0014 number correction earlier in §D8.

**Bead title vs filename drift note:** The bead title for bd-2pd5i references `ADR-008-stream-to-channel-deduplication.md`; the actual file landed as `ADR-008-interactive-stream-dedup.md`. Bead title to be updated at close time.

Migration discipline: BD-C follows the bd-5w6jz idempotency pattern (per-statement guards against pre-existing tables/indexes from `create_all()`, smart-bootstrap-fast-path safe), per `docs/database_migrations.md`. Five smoke tests in `test_alembic_smoke.py`-style coverage: fresh up, fresh down, full drift (table pre-created), partial drift (table present, one index missing), partial drift (table + indexes present but unique-partial-index missing). Schema-parity boot-guard (`_assert_schema_matches_models`) gates the model against migration head. Coverage should extend to `pending_merge_journal` with the same five-scenario matrix.

### D9 — Async queue model: rows in `pending_merges`, no broker

The "queue" is exactly rows in `pending_merges` with `status='pending'`. There is **no Celery, no Redis, no RQ, no APScheduler, no broker daemon, no background-worker process**. The bulk-M3U import hook (BD-F) writes rows during the import; operators (via UI) and MCP tools (via the API) drain rows by calling `/accept` or `/dismiss`.

This is documented explicitly to head off any future proposal to "add a broker for queue scalability." The scaling regime that would justify a broker (parallel consumers, sub-second SLA on dequeue, work-stealing across instances) is not the regime this queue lives in:

- The work is **operator-paced**, not throughput-bound. Dequeue rate is "as fast as a human or an AI agent decides per row."
- There is **one ECM container** (per ECM's deployment model — see ADR-007 §D7 for the single-writer constraint that backs this). A broker buys nothing single-process.
- The latency SLO from the epic (drag-drop p95 < 200 ms, bulk-M3U matcher p95 < 2 s for 200×500) is well inside what a SQLite read against a small indexed table delivers. The matcher is the latency bottleneck, not the queue.

The bd-5w6jz idempotency pattern on the migration and the §D1 idempotent-by-design endpoints together cover the correctness properties a broker would otherwise provide (at-least-once delivery, exactly-once effect): writes are guarded against duplicate `pending` rows by the §D5 partial unique index, and accept/dismiss is safe to retry.

If a future change introduces a second writer to the dedup path (an out-of-process import worker, a second ECM instance), the same trigger that would justify reconsidering SQLite (ADR-007 §D7's PostgreSQL threshold) is the trigger to reconsider the queue model. Until then, **rows are the queue**.

### D10 — Out of scope for v0.17.1

Explicitly deferred. Each is filed as a backlog candidate so it surfaces in grooming if the use case appears:

- **Per-group threshold override** — Settings is a single global `dedup_threshold` in v0.17.1. Per-group is `enhancedchannelmanager-s7lxd` (P3, backlog).
- **"Not a match" learning** — the matcher always re-asks on the same `(stream_name, candidate_channel_id)` pair; there is no negative-feedback store. Operators who dismiss the same candidate twice will see it again on the next M3U refresh. Acceptable for v1; revisit if dismissal duplication becomes a complaint.
- **Auto-aging of stale `pending` rows** — no cron prune in v0.17.1; the `auto_aged_out` action_type slot in §D6 is reserved for the deferred retention reaper (`enhancedchannelmanager-5136e`, P3).
- **Custom matcher backends** — RapidFuzz is the only matcher. The matcher service interface (BD-A's `find_candidate(...)`) is single-implementation by design; a pluggable adapter layer can be retrofitted later without breaking callers.
- **Bulk "Resolve All" UI actions** — `enhancedchannelmanager-qpgsx` (P3, backlog). The §D2 floor and the §D5 uniqueness constraint are what make this safe to add later; without them, "Resolve All" would be a bulk-destruction surface.
- **Systemic `ModalOverlay` focus trap** — BD-G ships a bespoke focus trap in `StreamDedupModal`; the systemic fix is `enhancedchannelmanager-bfbk8` (P3, backlog). UX-flagged pre-existing gap, not new with this work.

## Alternatives Considered

| # | Option | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| 1 | **Chosen — D1 plural-noun routes + D2 hard floor + D9 rows-are-the-queue** | Convention-consistent API; bounded blast radius via floor; no new infra; SQLite-native; ports cleanly to Postgres if ADR-007 §D7 fires | Operators need to be told what "channel merges" means in the docs (BD-N); the floor is one more constant to remember | High — no broker, no external dep beyond RapidFuzz | Low — one new router, one migration, one matcher service |
| 2 | **Original `/api/dedup/*` routes** (D4-pre-override) | Matches the algorithm name engineers already use in talk; what the team-plan ratified | Violates style-guide plural-noun convention (would block at Code Reviewer PR review); `dedup` is implementation-speak the operator never sees; `/merges/{id}/merge` reads redundantly | High | Same as chosen; the cost difference is the rename, which is cheap *now* and expensive after BD-D/E land |
| 3 | **No confidence floor, operator-configurable from 0%** | Maximum operator flexibility; one less constant | Bulk-destruction footgun (Security Engineer veto-class); a misconfigured threshold + a future "Resolve All" button = mass channel loss | High | Zero build cost, catastrophic worst-case |
| 4 | **Soft floor with override flag** | Operator can opt out of the floor for an edge case | Defeats the purpose of the floor — the misconfiguration mode is exactly "operator sets a low value because they don't realize the consequence"; the override flag would be set in the same misconfiguration | High | Same as chosen; worse safety |
| 5 | **Broker-backed queue (Celery/Redis or RQ) for bulk-M3U enqueue** | Battle-tested infra; out-of-process retry semantics; observability tooling exists | Adds a new container (Redis or RabbitMQ) to a single-container app; the latency budget doesn't need it; the scaling regime doesn't justify it; ADR-007's single-writer posture on SQLite makes parallel consumers wrong anyway | Low — new infra dep, contradicts ECM's deployment model | High — operator-facing deployment change for no analytic benefit |
| 6 | **APScheduler in-process for retry / reaper / metrics** | One Python dep, no new container | A new dependency for one job (the deferred reaper) that hasn't been justified yet; ADR-007 explicitly rejected this same pattern for the rollup job in favor of `asyncio` startup tasks; would set precedent for re-evaluation if/when the reaper lands | High | Low now, but a precedent we'd rather not set |
| 7 | **Synchronous in-request bulk-M3U dedup (no queue at all)** | No `pending_merges` table; no queue lifecycle to manage | Blows the bulk-import latency budget (200×500 candidates inline = many seconds of held connection); blocks the operator's M3U refresh on dedup decisions the operator wants to make later | High | Negative — degrades the most-used path |
| 8 | **Reuse `MergeChannelsModal` instead of a new `StreamDedupModal`** | One less component to build | Editing surface vs. decision surface — the UX-designer's call (epic body): `MergeChannelsModal` is "configure the merge you've already decided on," `StreamDedupModal` is "decide whether to merge"; combining them confuses both flows | High | Lower build cost, higher UX cost |
| 9 | **Pluggable matcher abstraction in v1** | Future-proof for adding semantic / EPG-id / phonetic matchers | YAGNI for v1; one-implementation interfaces calcify into hard-to-change abstractions; the cheap path is single-implementation now, refactor when the second matcher actually exists | High | Higher build cost, no v1 benefit |
| 10 | **Local FK on `candidate_channel_id` to a synced cache of Dispatcharr channels** | Catches deleted-candidate at write time | Requires a reconciliation job (the very thing §D4's lazy-resolution avoids); doesn't actually prevent the race (channel can be deleted in Dispatcharr between sync and accept); operationally heavier than the lazy path | High | High — new sync job, new failure modes, no correctness gain |

## Consequences

### Positive

- **Contract-lock for 16 sub-beads (BD-A through BD-P, including this ADR as BD-L and the SRE/docs cross-cutters BD-M / BD-N).** BD-A through BD-P consume this ADR as their interface document. The API path, the floor constant, the schema columns, the MCP tool names, and the journal field set are all named once here and referenced from there. Divergent implementation choices have nowhere to hide.
- **Bulk-destruction is bounded by construction.** The §D2 hard floor means a future "Resolve All" UI action (deferred backlog `qpgsx`) can ship without re-opening the misconfiguration vector — even the worst plausible operator threshold setting still gets matcher behavior at the floor. The Security Engineer signs off without needing per-action review.
- **API convention stays consistent with the rest of `/api/*`.** Plural-noun resource paths, flat outcome envelopes, idempotent POST verbs at sub-resources. A new engineer reading `backend/routers/channel_merges.py` should not have to learn a new convention.
- **No new infrastructure.** SQLite, the existing `asyncio` request loop, the existing JWT middleware, and the existing journal table cover everything. No Celery, no Redis, no APScheduler, no broker. Operators who self-host a single container do not learn a new operational surface.
- **Audit is real, not nominal.** Every accept / dismiss / queue is attributable to a specific token id, a specific surface, and a specific confidence-at-decision-time. The MCP-vs-operator distinction the epic asks for (was an AI agent driving the merges, or a human?) is answerable from the `pending_merge_journal` table (§D6 / §D8) without inferring from log timing. Every audit field is a queryable column — no JSON blobs.
- **Retention is deferrable, not ignored.** The terminal-state rows are kept indefinitely in v0.17.1; the reaper bead (`5136e`) is the additive lever if and when SLO-10 (BD-M) shows a real growth problem.
- **MCP and REST cannot drift.** §D7 names the MCP tools by their final identifiers so BD-O / BD-P land matching the REST surface; future MCP additions go through the same naming convention.
- **The auto-creation pipeline and the interactive dedup pipeline have a documented boundary.** Migration 0002 / bd-r9mtd is the unattended path; this ADR is the attended path. They do **not** share a matcher in v0.17.1 (epic decision; the architect's case for shared matcher is a backlog candidate but blocked by the auto-creation collision detection's stricter "separate not merge" semantics).

### Negative

- **The §D2 floor is one more constant to keep in sync** between the matcher module (`backend/services/dedup_matcher.py`) and the settings validator (`backend/routers/settings.py`). The mitigation in §D2 (single source-of-truth import) keeps drift mechanical, but a careless future refactor that hard-codes the value at one site and not the other would create a defense-in-depth gap. Code Reviewer should look for hard-coded `60` literals at PR review.
- **`/api/channel-merges/*` is a fresh router** without the muscle-memory the existing engineers have for `/api/channels`. The first few weeks of v0.17.1 development will see drafts that put endpoints in `/api/channels/merges/*` or `/api/streams/dedup/*`; BD-N's API reference and a one-time engineer-facing announcement in the PR description are the corrective.
- **Terminal-row retention is unbounded in v0.17.1.** Operator instances that run for years with frequent M3U refreshes will accumulate journal entries and `pending_merges` rows indefinitely. The DB-size gauge from bd-ygoqr (`ecm_database_size_bytes`) is the leading indicator; SLO-10 covers queue-depth specifically; the deferred reaper (`5136e`) is the lever. Acceptable risk for v1, but flagged in the runbook stubs (BD-M).
- **The MCP `add_stream(dedup_action=)` extension has three modes**, and one of them (`merge_if_found`) silently falls back to `prompt` semantics when confidence is below the operator threshold. An AI agent that does not read the documentation carefully might assume `merge_if_found` always force-accepts. BD-N's MCP doc must be explicit; BD-O's contract test must lock the fallback behavior.
- **Lazy resolution on `candidate_channel_id`** (§D4) means the worst-case operator UX for a deleted target is `accept` → 404 → dismiss → re-trigger. Two extra clicks. Acceptable for v1; if operator feedback says the stale-list problem is acute, the offline orphan-detection query supported by the §D8 `(candidate_channel_id)` index is the cheap first move.
- **No "not a match" learning** means an operator who dismisses the same candidate twice will see it again on the next M3U refresh. Possible source of dismissal fatigue at high M3U churn rates. Out-of-scope per §D10; revisit if the support signal shows up.

### Neutral / Out of Scope

- **The matcher's RapidFuzz dependency** is BD-A's deliverable; the dep-bump cadence and license review are governed by ADR-001's dep-bump path, not this ADR. Pinned version per BD-A.
- **The auto-creation pipeline's collision detection** (migration 0002, `match_scope_target_group`) is unchanged by this ADR. The interactive dedup work is **attended**; auto-creation is **unattended** and uses its own logic. The "shared matcher service" architect-recommended refactor is a backlog candidate, not v0.17.1 work.
- **Frontend lint rules, CSS conventions, modal patterns** — BD-G / BD-H / BD-I / BD-J inherit `docs/style_guide.md` and `docs/css_guidelines.md`; this ADR does not re-state them.
- **CodeQL exposure of the new router** — `backend/routers/channel_merges.py` is subject to ADR-005's delta-zero gating at PR time. No special carve-out.
- **SLO-10 specifics (warn-at-50 queue depth, p95 latency budgets)** are BD-M's deliverable; the epic body carries the PO-ratified numbers, this ADR encodes only the boundary (operator-paced queue, no page-class alert in v1).

## Exit Path

If the chosen contract turns out wrong:

1. **Soft exit — adjust the confidence floor.** The §D2 floor is a single constant; raising or lowering it is a one-line change + an ADR addendum noting the new value and the evidence that justified the move. Raising it is safe (strictly stricter); lowering it requires the Security Engineer to re-confirm the bulk-destruction analysis at the new value.
2. **Soft exit — rename API paths after launch.** If the `/api/channel-merges/*` naming proves awkward in practice, rename and add a deprecation alias for one release. Mechanical change in `backend/routers/channel_merges.py` and the frontend `services/api.ts`; MCP tool names follow the same pattern via aliasing in `mcp-server/tools/dedup.py`. ADR addendum required.
3. **Additive exit — pluggable matcher backends.** BD-A's `find_candidate(...)` is a single-implementation function today. If a second matcher (semantic embeddings, EPG-id pre-match, phonetic) becomes useful, extract to an adapter interface and add the second implementation. No schema change; the `confidence` column stays a normalized 0.0–1.0 score regardless of backend.
4. **Additive exit — retention reaper.** The deferred bead `enhancedchannelmanager-5136e` is the cleanup path: a nightly `asyncio` task (same pattern as ADR-007 §D3) that sweeps terminal rows older than a configurable age. Lands when SLO-10's queue-depth alert (BD-M) gives concrete evidence the table is growing problematically.
5. **Hard exit — broker-backed queue.** If a future change introduces a second writer to the dedup path (an out-of-process import worker, multi-instance ECM), reconsider the §D9 "rows are the queue" model alongside ADR-007 §D7's PostgreSQL trigger. Own ADR. Same evaluation criteria: justify the new infra against the actual workload.
6. **Hard exit — abandon RapidFuzz.** If RapidFuzz becomes unmaintained, license-incompatible, or unfixably wrong for the IPTV-stream-name shape, swap to an alternative (Python `difflib`, `rapidfuzz-py-stub`, or a custom token-set comparator). The matcher service is the only consumer; the `confidence` column's 0.0–1.0 contract is backend-agnostic. Engineer-scope change; ADR addendum noting the new dep and the score-equivalence justification.

No vendor relationship to unwind; no external dependency introduced by this ADR beyond RapidFuzz (BD-A's pin).

## Open Questions

### Resolved inline (no PO action needed)

- **API path naming?** → `/api/channel-merges/*` with verb sub-resources `/accept` and `/dismiss` (D4 override, §D1). Style-guide-aligned, Code-Reviewer-blockable form retired.
- **Confidence floor — soft or hard?** → **Hard, 60%, defense-in-depth** (§D2). No override flag; ADR-addendum-only change.
- **State machine on `pending_merges`?** → Three states (`pending` / `merged` / `dismissed`), terminal-state idempotent endpoints, indefinite retention in v0.17.1 (§D3).
- **FK on `candidate_channel_id`?** → No local FK; lazy resolution at accept time (§D4).
- **Duplicate-pending protection?** → Partial UNIQUE index on `(stream_name, candidate_channel_id) WHERE status='pending'` (§D5).
- **Journal field set?** → Token-id-based actor attribution + `trigger_context` enum + `confidence_score`-at-action-time (§D6).
- **MCP tool names?** → `list_pending_channel_merges` / `accept_channel_merge` / `dismiss_channel_merge` + `add_stream(dedup_action=)` extension (§D7).
- **Schema migration number?** → **0014** (current Alembic head is 0013; BD-C bead title says 0011 and needs updating at implementation time) (§D8).
- **Queue infrastructure?** → Rows in `pending_merges` are the queue; no broker, no scheduler daemon (§D9).
- **Per-group threshold override / 'not a match' learning / auto-pruning / pluggable matcher / "Resolve All" / systemic focus trap?** → All deferred to backlog beads (§D10).

### PO decisions — resolved 2026-05-16

1. **D4 override (API naming) accepted.** Code Reviewer's style-guide-alignment concern outweighs the original team-plan ratification; the rename is cheap *now*, expensive after BD-D / BD-E land code against the old paths.
2. **§D3 retention left indefinite for v0.17.1.** No reaper, no cron; deferred to `enhancedchannelmanager-5136e` (P3, backlog), gated on SLO-10 queue-depth alert firing.
3. **§D9 broker-free queue model locked.** No proposal for Celery / Redis / RQ / APScheduler in v0.17.1 or v0.17.x; revisit only if a second concurrent writer appears.

## References

- Bead `enhancedchannelmanager-2pd5i` — this ADR's tracker (BD-L)
- Bead `enhancedchannelmanager-1v4ht` — Dedup epic (v0.17.1); the PO-ratified design record this ADR encodes
- Beads `enhancedchannelmanager-7xo8e` (BD-A), `enhancedchannelmanager-0b6xj` (BD-B), `enhancedchannelmanager-6by2n` (BD-C) — Wave 0 sub-beads
- Beads `enhancedchannelmanager-kbqwb` (BD-D), `enhancedchannelmanager-acqkb` (BD-E), `enhancedchannelmanager-a5lb2` (BD-F) — Wave 1 API sub-beads
- Beads `enhancedchannelmanager-4vxjj` (BD-G), `enhancedchannelmanager-u6ftw` (BD-H), `enhancedchannelmanager-1lznl` (BD-I), `enhancedchannelmanager-gfxrz` (BD-J), `enhancedchannelmanager-ugzn4` (BD-K) — Wave 2 frontend sub-beads
- Beads `enhancedchannelmanager-ft3hk` (BD-M), `enhancedchannelmanager-0lsas` (BD-N) — Cross-cutting SRE + docs
- Beads `enhancedchannelmanager-70ylc` (BD-O), `enhancedchannelmanager-7u8ms` (BD-P) — MCP sub-beads
- Beads `enhancedchannelmanager-qpgsx`, `enhancedchannelmanager-5136e`, `enhancedchannelmanager-bfbk8`, `enhancedchannelmanager-s7lxd` — Deferred backlog candidates (§D10)
- Bead `enhancedchannelmanager-5w6jz` — Alembic 0006-0010 idempotency + smart-bootstrap fast-path (BD-C inherits)
- Bead `enhancedchannelmanager-r9mtd` — Auto-creation separate-not-merge collision detection (migration 0002); the unattended counterpart to this ADR's attended path
- `backend/routers/channels.py:1961` — `merge_channels` endpoint; the flat-outcome response-envelope precedent §D1 mirrors
- `backend/alembic/versions/20260515_2000_0013_session_telemetry_channel_event_counters.py` — current Alembic head; BD-C's migration 0014 lands on top
- `docs/database_migrations.md` — Alembic authoring guide
- `docs/style_guide.md` — Naming conventions (drove the D4 override)
- `docs/architecture.md` — System overview (BD-N updates)
- `docs/api.md` — API reference (BD-N updates)
- `docs/adr/ADR-007-session-telemetry-retention.md` — Sibling ADR; "bounded by construction" framing this ADR adopts for §D3
- `docs/adr/ADR-005-code-security-gating-strategy.md` — CodeQL delta-zero gate covering the new router at PR time

## Revision History

| Date | Bead | Change | Rationale |
|---|---|---|---|
| 2026-05-16 | `enhancedchannelmanager-2pd5i` | Proposed + accepted same day | Contract-lock for BD-A through BD-P; encodes 2026-05-15 team-plan + 2026-05-16 D4 API-naming override. Hard prerequisite for the 16 sub-beads of the dedup epic |
