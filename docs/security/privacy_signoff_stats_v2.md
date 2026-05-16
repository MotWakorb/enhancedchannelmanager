# Privacy Sign-off: Stats v2 (post-implementation review)

**Bead:** enhancedchannelmanager-skqln.8 (Privacy 11b — post-implementation privacy sign-off for Stats v2)
**Author:** Security Engineer persona (Claude)
**Date:** 2026-05-13
**Branch / commit baseline:** `origin/dev` @ `67c092cc` (Merge PR #255 — skqln.10 perf gate; latest in the Stats v2 trifecta + read-API + observability + perf-gate stack)
**Scope:** verification that what shipped matches what the pre-implementation threat model (`docs/security/threat_model_stats_v2.md`, skqln.7) specified.
**Companion:** `docs/security/threat_model_stats_v2.md` (the pre-impl reference this sign-off is checked against).

---

## Sign-off Status: **APPROVED WITH CONDITIONS**

The shipped Stats v2 surface (raw `session_telemetry` table, write path, read APIs, frontend, observability) is **structurally privacy-compliant** with the pre-implementation threat model. The eight redaction requirements in §7 of the pre-impl doc, the access-control boundary in §3, and the metric cardinality discipline in §7.4 are all enforced in code and exercised by tests on `origin/dev` HEAD.

Three deferred items prevent **unconditional** sign-off — they are tracked in beads that are already in flight and gate the v0.17.0 release in their own right, so this is a sequencing dependency rather than a missing-control finding:

| Gating bead | Status | Pre-impl requirement it satisfies | Risk if shipped without it |
|---|---|---|---|
| `enhancedchannelmanager-7i2vv` | IN_PROGRESS | §5.2 #1 (pruning job actually runs); §5.2 #2 rollup-table extension of the account-deletion scrub; ADR-007 D3 nightly job + D6 alerting | Unbounded `session_telemetry` growth in SQLite (the ADR-007 problem statement) **and** PII-adjacent rows retained past their stated use case (T5). |
| `enhancedchannelmanager-tp1pd` | IN_PROGRESS | §6.3 / §8 Q1 (operator-global telemetry opt-out toggle) | Operator cannot disable telemetry collection — the PO-resolved privacy lever for shared-household installs is missing. |
| `enhancedchannelmanager-skqln.9` | IN_PROGRESS | §6.1 user-facing disclosure + §6.2 "what's recorded about you" framing of the Users panel | Silent behavioral tracking with no plain-language disclosure (T6). The Users panel as shipped is functional but not framed as a transparency surface. |

**Conversion to unconditional APPROVED:** when `7i2vv`, `tp1pd`, and `skqln.9` merge to `dev`, this sign-off converts automatically — no additional security review is required. Filing a follow-up bead is not necessary; the existing beads already track each gap.

**Highest residual risk:** **T1 (IDOR on watch-time read API) — closed by `_check_admin` + the inline admin-gate in `get_watch_time_by_user`/`get_watch_time_for_user`.** Verified by `TestWatchTimeAuthEnforcement` and `TestProviderStatsAuthEnforcement` in `backend/tests/integration/test_api_watch_time.py` and `test_api_provider_stats.py` respectively. The pre-impl model rated T1 as High; with the shipped enforcement it drops to **Low** (residual: an admin can still see all users' watch-time, which is the documented intent).

---

## Checklist — 7 items

### 1. Data classification adherence — **PASS** (with a noted schema deviation)

The shipped `session_telemetry` schema (`backend/models.py:1245`, migrations 0006 + 0007) is the conservative shape the pre-impl model approved, with one deliberate deviation:

| Column | Pre-impl spec | Shipped | Verdict |
|---|---|---|---|
| `session_id` | TEXT (UUID), indexed | `Text NOT NULL`, indexed (`idx_session_telemetry_session_id`) | match |
| `observed_at` | INTEGER (unix epoch ms), indexed | `Integer NOT NULL`, indexed (`idx_session_telemetry_observed_at`) | match |
| `user_id` | INTEGER FK `users(id)` ON DELETE SET NULL, nullable | `Integer ForeignKey("users.id", ondelete="SET NULL")`, nullable | match (the §9 condition (a) `ON DELETE SET NULL` retained — verified by `test_session_telemetry_user_id_fk_set_null_on_delete`) |
| `provider_id` | INTEGER FK `providers(id)` ON DELETE SET NULL, nullable | `Integer` **plain indexed column, NOT a FK**, nullable | **deviation — accepted** (see below) |
| `channel_id` | INTEGER FK `channels(id)` ON DELETE SET NULL, nullable | **`String(64)` (Dispatcharr UUID), NOT a FK, NOT NULL** | **deviation — accepted** (see below) |
| `bytes_delta` | INTEGER NOT NULL CHECK ≥ 0 | `BigInteger NOT NULL` + `CheckConstraint("bytes_delta >= 0")` | match |
| `buffer_event_count` | INTEGER NOT NULL DEFAULT 0 | `Integer NOT NULL DEFAULT 0` | match |
| `poll_interval_ms` | INTEGER NOT NULL | `Integer NOT NULL` | match |

**Schema deviations — explanation and privacy assessment:**

`channels` and `providers` are **not ECM-owned tables** — they are Dispatcharr-owned, and ECM keys to them by Dispatcharr's identifiers (UUID string for channels, integer m3u_account id for providers). The shipped schema's plain-indexed columns are the only correct shape; the FK-based form in the pre-impl table was a drafting artifact. Every other channel-keyed table in the schema (`channel_watch_stats`, `channel_bandwidth`, `channel_popularity_scores`, `unique_client_connections`) uses `String(64)` for `channel_id`. **Privacy impact: none.** The C2 classification of the `user_id`-keyed tuples is unchanged; the `channel_id`-by-itself classification is still C1. The shipped schema is, if anything, more conservative — it cannot leak a foreign-table relationship that doesn't exist.

The model docstring at `backend/models.py:1255-1265` explicitly cites the pre-impl threat model §2.1 / §7 and explains why the deviation is privacy-safe. No new columns were added; no PII fields snuck in. **PASS.**

---

### 2. Retention policy alignment — **CONDITIONAL** (gated on `enhancedchannelmanager-7i2vv`)

ADR-007 (`docs/adr/ADR-007-session-telemetry-retention.md`) is **shipped** and incorporates every §5.2 mandatory guarantee from the pre-impl threat model:

- D1: **raw retention = 30 days** (matches the pre-impl "30 days raw is fine" position; not exceeded)
- D4: **`watch_time_by_user_daily` rollup retention = 400 days** (matches the pre-impl §5.3 recommendation; explicitly rejects "rollups forever" for user data — see ADR §8 PO decision)
- D5: **`provider_performance_daily` rollup retention = 400 days** (operator-private commercial data; bounded horizon)
- D3: nightly `asyncio` rollup-then-prune job design (rollup-before-prune, idempotent, catch-up-capable)
- D6: alerting design (>36h warn, >25d page, raw-row-count ceiling, prune lock-contention guard)

**However, the rollup tables, the `telemetry_rollup_state` marker, and the nightly prune job are NOT YET SHIPPED.** They are in flight in `enhancedchannelmanager-7i2vv` (status: IN_PROGRESS at the time of this sign-off). Without them, raw `session_telemetry` accumulates indefinitely — which is the failure mode the pre-impl §5.2 #1 "pruning job actually runs and is monitored" requirement was written to prevent.

**The account-deletion scrub for the raw table is in place** and tested:
- `session_telemetry.user_id` has `ON DELETE SET NULL` (verified in `backend/models.py:1293` and `backend/tests/integration/test_session_telemetry_migration.py:264 test_session_telemetry_user_id_fk_set_null_on_delete`)
- The rollup-table extension of that scrub (T8 from the pre-impl model) is explicitly named as `7i2vv`'s responsibility — there are no rollup tables to scrub yet, so this is naturally deferred to the bead that creates them.

**Conditional verdict:** the retention *design* is signed off; retention *enforcement* converts from CONDITIONAL to PASS when `enhancedchannelmanager-7i2vv` merges to `dev`. Until then, every ECM install on a Stats-v2 release accumulates raw rows past the 30-day mark with no automated prune — a privacy minimization gap, not a confidentiality breach.

---

### 3. Logging discipline — **PASS**

The pre-impl §7.1 redaction rule is: *"No log line — at any level, including DEBUG and exception dumps — may contain `user_id` together with any of `observed_at`, `channel_id`, `session_id`, or per-row `bytes_delta`."*

**Evidence — every `[STATS_V2]` log emission, audited:**

All 21 `[STATS_V2]` log lines live in `backend/bandwidth_tracker.py`. None contain `user_id`. Specifically:

- `[STATS_V2] provider_resolution_failed channel=... reason=...` (lines 940, 965, 999) — emits `channel_uuid` and a reason code. No `user_id`.
- `[STATS_V2] provider_resolution_failed reason=lookup_raised error=...` (line 959) — emits an exception string. No row contents.
- `[STATS_V2] provider_resolution resolved=X unresolved=Y` (line 1036) — emits aggregate counts only.
- `[STATS_V2] buffer_event_fetch_failed reason=request_raised error=...` (line 1113) — emits an exception string. No row contents.
- `[STATS_V2] buffer_event_skipped reason=no_event_id` (line 1133) — no row contents.
- `[STATS_V2] buffer_event_unmapped_channel event_id=... channel_id=...` (line 1156) — emits a Dispatcharr-side event id and the channel id of an unmapped event. No `user_id`. The `channel_id` here is C1-by-itself per the pre-impl classification (no user dimension paired with it in the same line).
- `[STATS_V2] buffer_event_ingest fetched=X deduped=Y attributed=Z` (line 1205) — emits aggregate counts only.
- `[STATS_V2] buffer_event_dropped channel=... count=... reason=no_active_session_row` (line 1387) — emits `channel_uuid` and a count. No `user_id`.
- `[STATS_V2] Wrote N session_telemetry row(s) (observed_at=...)` (line 1395, DEBUG) — emits a row count and the poll's `observed_at` (a timestamp). No `user_id`, no `channel_id`, no `session_id`. The pre-impl model classifies a timestamp alone as C1.
- `[STATS_V2] session_telemetry write failed observed_at=... channels_attempted=... error=...` (line 1421) — emits `observed_at`, a count of attempted channels (not the channel ids), and the exception. The comment block at lines 1417-1419 explicitly cites the Privacy 11a requirement: *"we deliberately do NOT enumerate per-row user_id+channel_id pairs — those are aggregated away by the time we get here."* No `user_id` paired with `channel_id`.
- `[STATS_V2] failed to emit ... metric` (lines 1050, 1241, DEBUG) — emits no row contents.

**`backend/observability.py`** emits no `[STATS_V2]` log lines. The only `logger.info` in the module is the `[NORMALIZE]` decision log (line 908) — unrelated to Stats v2 and audited under the normalization threat model.

**Test enforcement:** `backend/tests/unit/test_stats_v2_observability.py:468 test_write_failure_log_does_not_pair_user_id_with_channel_id` asserts that the write-failure path (the highest-risk log emission, since it executes during an exception with row context in scope) does not pair `user_id` with the channel uuid in the same log line. This pins the §7.1 rule against regression.

**PASS.** Every `[STATS_V2]` log emission complies with the pre-impl redaction rule; the discipline is also enforced by a test that survives future edits.

---

### 4. Cardinality discipline on metrics — **PASS**

The pre-impl §7.4 rule is: *"`user_id` / `channel_id` never become Prometheus metric labels. `provider_id` is permitted (bounded <20, C3, operator-private)."*

**Evidence:**

The five Stats v2 Prometheus metric families (`backend/observability.py:487-540`) declare these label sets:
- `session_telemetry_writes_total` → labels: `["result"]` (enum: success/failure)
- `session_telemetry_write_duration_seconds` → no labels (Histogram)
- `session_telemetry_row_count` → no labels (Gauge)
- `provider_resolution_total` → labels: `["result"]` (enum: resolved/unresolved)
- `stats_query_duration_seconds` → labels: `["endpoint", "granularity"]` (FastAPI route pattern + group_by axis; combined ceiling ~15 series)

The comment block at `backend/observability.py:465-486` explicitly re-states the SRE veto and notes that `provider_id` is allowed but no Stats v2 metric in this bead uses it as a label. **None of the banned dimensions (`user_id`, `channel_id`, `session_id`, `target_id`, `client_ip`, `trace_id`) appear as labels on any of the five metrics.**

**Test enforcement** (the parametrized test the bead instructions asked me to verify):
- `backend/tests/unit/test_stats_v2_observability.py:174-182 test_no_banned_label_on_metric` is parametrized over all five Stats v2 metric names (`STATS_V2_METRIC_NAMES` tuple at line 166) and asserts no overlap with `BANNED_LABELS` (`{user_id, channel_id, session_id, target_id, client_ip, trace_id}`, line 156).
- `test_no_user_id_or_channel_id_label_after_full_poll` (line 388) drives a full BandwidthTracker poll cycle with real `user_id` and `channel_id` values in the rows and asserts the rendered Prometheus body never contains `user_id="..."`, `channel_id="..."`, or `session_id="conn-..."` as a label. This is the end-to-end check that complements the static label-name assertion.
- `test_session_telemetry_writes_result_label_is_bounded` and `test_provider_resolution_result_label_is_bounded` (lines 184, 196) pin the `result` label enums against silent expansion.

**PASS.** Every Stats v2 metric honors the no-`user_id`/no-`channel_id`/no-`session_id` rule, and the discipline is enforced by both a label-name unit test and an end-to-end render assertion.

---

### 5. Admin-only auth boundary — **PASS**

PO directive 2026-05-13: both `/api/stats/watch-time*` and `/api/stats/providers/*` are admin-only. The pre-impl threat model §3 specified least-privilege (own-data for non-admin; admin-or-all for admin); the PO escalated this to admin-only for both surfaces. The shipped behavior matches the **escalated** posture, which is strictly more restrictive than the pre-impl baseline — a security improvement, not a regression.

**`skqln.5` enforcement (`backend/routers/stats.py`):**
- `GET /api/stats/watch-time` (line 534): inline admin check at line 580 — *"if caller is not None and not caller.is_admin: raise HTTPException(403, 'Watch-time stats are admin-only')"*. Caller is resolved via `get_watch_time_caller` (line 33), which calls `get_current_user` (the same auth dependency `backup.py` uses).
- `GET /api/stats/watch-time/{user_id}` (line 665): same inline admin check at line 701, with explicit comment *"PO directive 2026-05-13: non-admins do not see stats — including their own."* — this closes T1 (IDOR) more aggressively than the pre-impl §3.1 spec (which permitted non-admin own-data).

**`skqln.16` enforcement (same file):**
All four provider endpoints — `/api/stats/providers/buffering` (line 936), `/api/stats/providers/watch-time` (line 1007), `/api/stats/providers/channel-heatmap` (line 1066), `/api/stats/providers/bitrate` (line 1162) — call `_check_admin(caller)` (helper at line 837) on the very first line of the try block. The helper raises 403 on non-admin callers with message *"Provider stats are admin-only"*. Matches §8 Q3 (provider-attribution visibility → admin-only).

**`AUTH_EXEMPT_PATHS` audit (`backend/main.py:440-482`):** no `/api/stats/*` path appears in the exempt set. The exempt set is bounded to health/version/auth-flow/setup/openapi endpoints (per the pre-impl §3.5 *"never make /api/stats/providers/* exempt"* requirement). **Compliant.**

**Test enforcement:**
- `backend/tests/integration/test_api_watch_time.py:458 test_non_admin_per_user_endpoint_other_user_id_gets_403` (IDOR case — closes T1)
- `test_api_watch_time.py:480 test_non_admin_per_user_endpoint_own_user_id_gets_403` (verifies the PO escalation — non-admin cannot see own data either)
- `test_api_watch_time.py:509 test_admin_can_query_any_user_id` (positive case)
- `test_api_watch_time.py:536 test_non_admin_list_endpoint_gets_403` (the `/watch-time` collection endpoint)
- `backend/tests/integration/test_api_provider_stats.py:680 test_non_admin_gets_403` (parametrized over all 4 provider endpoints)
- `test_api_provider_stats.py:702 test_admin_gets_200` (positive case, all 4 endpoints)

**`skqln.6` frontend handles 403 gracefully (`frontend/src/components/tabs/UserStatsPanel.tsx`):**
- `isAdminOnly403` helper at line 100 detects the 403 status
- `knownNonAdmin || adminOnly` short-circuit at line 197 renders an `admin-only-state` notice *"User watch-time statistics require admin access"* (line 202) instead of cascading the error
- The component avoids the API call entirely when `useAuth()` reports a known non-admin user (line 109 comment); the 403 fallback handles auth-disabled mode and the "non-admin user object not yet hydrated" race
- Test file `UserStatsPanel.test.tsx` exists alongside the component (full unit-test coverage not audited here — out of scope for this review; the 403 *handling code* is what the pre-impl model required and it is present)

**PASS.** All three layers (skqln.5 backend, skqln.16 backend, skqln.6 frontend) enforce the admin-only boundary correctly, and `AUTH_EXEMPT_PATHS` is clean.

---

### 6. External data flow — **PASS**

The pre-impl model implicitly assumed Stats v2 added no new outbound data flows. **Verified:**

- `backend/m3u_digest_template.py` and `backend/alert_methods_discord.py` contain **zero references** to `provider_id`, `provider_name`, or `session_telemetry`. The §7.3 requirement (no provider names / no telemetry-derived aggregates templated into Discord) is satisfied by absence. The pre-impl §4 T4 mitigation is in effect by construction.
- `backend/export_manager.py` and `backend/export_models.py` contain **zero references** to `session_telemetry`. No telemetry rows can leak into export bundles.
- `backend/cloud_storage/*` contains **zero references** to `session_telemetry`. Telemetry does not flow to backup/sync surfaces.
- No support-bundle or diagnostic-export feature exists in the codebase (grep for `support_bundle`/`diagnostic.export` returns nothing). Per §8 Q4, this stays a **forward requirement** on whatever bead introduces such a surface — currently no such bead is in flight, so there is nothing to gate today.

**PASS.** Stats v2 introduces no outbound data flows. The shipped system writes `session_telemetry` rows to local SQLite, reads them through the admin-only `/api/stats/*` API, renders them in the operator-facing Stats tab — and that is the full data lifecycle. Nothing goes to Dispatcharr, Discord, cloud storage, or any external service.

---

### 7. Operator opt-out — **CONDITIONAL** (gated on `enhancedchannelmanager-tp1pd`)

PO decision §8 Q1 (resolved 2026-05-12): *"Opt-out support → operator-level global toggle only (no per-user opt-out) for v0.17.0. When the toggle is off, `session_telemetry` collection is disabled entirely."*

The pre-impl threat model **required this for sign-off** (§6.3, T6 mitigation: "support at least an operator-level global toggle"). The toggle is **not yet shipped** — it is in flight in `enhancedchannelmanager-tp1pd` (IN_PROGRESS), which the bead description says is a "fast-follow" to skqln.3.

**Risk while deferred:** every Stats-v2 install collects `session_telemetry` rows with no opt-out lever; a privacy-conscious operator (or one in a shared-household setting where another resident objects) currently has no way to disable collection short of editing the DB or disabling the BandwidthTracker. This is a **gap against the stated PO posture**, not against an industry-baseline obligation.

**Conversion path:** when `tp1pd` lands the SystemSettings boolean and gates `_write_session_telemetry` on it (per the bead description), this item converts from CONDITIONAL to PASS. The expected gate location is at the top of `BandwidthTracker._write_session_telemetry` (`bandwidth_tracker.py:1245`) — a single early-return when the toggle is off. The toggle's UI surface (Settings tab, alongside the existing error-telemetry opt-out) and the disclosure update (skqln.9) are part of the same conversion.

---

## Summary Table

| # | Item | Verdict | Gating bead (if conditional) |
|---|---|---|---|
| 1 | Data classification adherence | PASS | — |
| 2 | Retention policy alignment | CONDITIONAL | `enhancedchannelmanager-7i2vv` |
| 3 | Logging discipline | PASS | — |
| 4 | Cardinality discipline on metrics | PASS | — |
| 5 | Admin-only auth boundary | PASS | — |
| 6 | External data flow | PASS | — |
| 7 | Operator opt-out | CONDITIONAL | `enhancedchannelmanager-tp1pd` |

**Additional note — disclosure / transparency surface (T6):** the pre-impl §6.1 plain-language disclosure and §6.2 "what's recorded about you" framing of the Users panel are part of `enhancedchannelmanager-skqln.9` (user-guide bead, IN_PROGRESS). T6 was rated Medium in the pre-impl model; the disclosure gap is a soft-block on v0.17.0 release per the bead chain (skqln.9 also depends on skqln.8, this sign-off, closing). Not called out as its own checklist item because the pre-impl model placed it under §6 (visibility UX), which is the natural home for skqln.6 + skqln.9 jointly; logging it here under the conditional bucket so the v0.17.0 release-gate review can see it.

## No follow-up beads filed

All three CONDITIONAL items map to beads that already exist in the backlog and are in flight (`7i2vv`, `tp1pd`, `skqln.9`). Filing parallel "Security follow-up" beads would create dependency churn and ambiguity over ownership — the existing beads' acceptance criteria already capture the privacy requirements verbatim (e.g., `7i2vv`'s description includes *"Account-deletion scrub extends to BOTH rollup tables (Privacy 11a condition)"*). The PO already has these on the radar via the standing v0.17.0 release-gate review.

If any of those beads slip out of v0.17.0 scope without their privacy requirements being met, **the v0.17.0 release notes must surface the gap to the operator** (in a "what's known to be deferred" section) — flagged here as a release-gate hand-off, not a separate bead.

---

## References

- `docs/security/threat_model_stats_v2.md` — pre-implementation threat model + data classification (skqln.7)
- `docs/adr/ADR-007-session-telemetry-retention.md` — retention policy
- `backend/models.py:1245` (`SessionTelemetry`) — shipped schema
- `backend/bandwidth_tracker.py:1245` (`_write_session_telemetry`) — write path
- `backend/routers/stats.py` — read APIs (skqln.5, skqln.16); admin-gate at lines 580, 701, 837 (`_check_admin`)
- `backend/observability.py:487-540` — five Stats v2 Prometheus metric families
- `backend/main.py:440-482` — `AUTH_EXEMPT_PATHS` (verified clean)
- `frontend/src/components/tabs/UserStatsPanel.tsx` — 403 handling
- `backend/tests/unit/test_stats_v2_observability.py` — cardinality + log-line PII pairing tests
- `backend/tests/integration/test_api_watch_time.py` — admin-only enforcement tests (watch-time)
- `backend/tests/integration/test_api_provider_stats.py` — admin-only enforcement tests (provider stats)
- `backend/tests/integration/test_session_telemetry_migration.py` — `ON DELETE SET NULL` scrub test
- `enhancedchannelmanager-skqln.7` — pre-impl threat model bead (CLOSED)
- `enhancedchannelmanager-skqln.8` — this sign-off (closes when v0.17.0 release-gate review accepts the conditional verdict)
- `enhancedchannelmanager-7i2vv` — rollup tables + prune job + rollup-table account-deletion scrub (IN_PROGRESS, gates item 2)
- `enhancedchannelmanager-tp1pd` — operator-global telemetry opt-out toggle (IN_PROGRESS, gates item 7)
- `enhancedchannelmanager-skqln.9` — user-guide entry + privacy disclosure (IN_PROGRESS, gates the T6 disclosure requirement)
