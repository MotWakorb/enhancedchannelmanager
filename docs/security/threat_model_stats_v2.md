# Threat Model & Data Classification: Stats v2 / `session_telemetry`

**Bead:** enhancedchannelmanager-skqln.7 (Privacy 11a — pre-implementation threat model + data classification)
**Author:** Security Engineer persona (Claude)
**Date:** 2026-05-12
**Status:** PO decisions resolved 2026-05-12 (§8). Conditional schema sign-off granted (§9) — converts to full sign-off when conditions (a)–(c) land in the skqln.2 bead.
**Blocks:** skqln.2 (`session_telemetry` schema + Alembic migration) — schema merge requires this document's sign-off. skqln.8 (post-implementation privacy sign-off) re-checks the built result against this.
**Feeds:** skqln.1 (ADR-007 retention/rollup policy) — §5 below states the **retention MINIMUM**. ADR-007 authors set the operational maximum on top of that floor.
**Related:** `docs/auth_middleware.md` (global secure-by-default auth; admin vs. non-admin), `docs/security/threat_model_dbas_import.md` (house style, redaction pattern), epic skqln body (scope-history trail, PO override pulling GH-59 in).

---

## 1. Scope & System Overview

Stats v2 introduces a new database table, `session_telemetry`, written once per stream-stats poll by `BandwidthTracker` (single-write design — see epic). Each row captures, per poll, who was watching what, via which upstream M3U provider, with what bytes-transferred delta and buffer-event count. Two new read-only Stats-tab panels consume it:

- **Users panel (GH-62, skqln.5/.6)** — watch-time-by-user: daily trend, channel table (total minutes, last watched), 7/30/90-day selector.
- **Providers panel (GH-59, skqln.16/.18)** — operator view of upstream-provider performance: buffering events by provider over time, watch-time per provider, channels-by-provider heatmap, derived bitrate by provider. Used to make provider keep/drop decisions at renewal.

Per the DBA's refined schema (skqln.2), the columns are:

| Column | Type | Notes |
|---|---|---|
| `session_id` | TEXT (UUID) | indexed; correlates rows in one viewing session |
| `observed_at` | INTEGER (unix epoch ms) | mandatory index; retention sweeps key off this |
| `user_id` | INTEGER, FK `users(id)` ON DELETE SET NULL | nullable |
| `provider_id` | INTEGER, FK `providers(id)` ON DELETE SET NULL | nullable until provider tagging ships |
| `channel_id` | INTEGER, FK `channels(id)` ON DELETE SET NULL | nullable |
| `bytes_delta` | INTEGER NOT NULL, CHECK ≥ 0 | per-poll byte delta |
| `buffer_event_count` | INTEGER NOT NULL DEFAULT 0 | per-poll buffer-event count |
| `poll_interval_ms` | INTEGER NOT NULL | poll cadence, so watch-time math doesn't assume a fixed interval |

This is a self-hosted single-container app. There is no multi-tenant boundary in the SaaS sense — but there **is** a user boundary: ECM has accounts (`users` table) with an `is_admin` flag, optional auth (`auth.require_auth`), and a global auth middleware over `/api/*`. The relevant trust boundaries:

- **Browser → ECM** — authenticated user (admin or non-admin), or unauthenticated when `auth.require_auth=False`.
- **Non-admin user → other users' data** — a non-admin viewing the Users panel must not become a way to surveil other household members' viewing habits. This is the new boundary Stats v2 introduces.
- **ECM → operator-visible diagnostics** — app logs, support bundles / diagnostic exports, the Discord release-note / digest automation. Behavioral data must not leak here.
- **ECM → SQLite (`journal.db` / the app DB)** — `session_telemetry` lives in the app database.

### 1.1 What's new vs. existing stats

ECM already exposes `/api/stats/*` (channel stats, popularity, activity, bandwidth) — but those are *channel-scoped aggregates pulled live from Dispatcharr*, not *persisted per-user behavioral history*. Stats v2 is the first time ECM **stores, indefinitely (via rollups), a per-user record of what each human watched and when**. That is a categorical change in the app's data sensitivity, and it's why this threat model exists before the schema lands rather than after.

### 1.2 Shared-household reality

One Dispatcharr username is routinely shared by multiple humans (spouse, kids, roommates). `user_id` in `session_telemetry` is therefore **a household identity, not necessarily a person** — but in single-occupant installs it *is* a person. The model treats `user_id`-keyed history as person-level PII-adjacent data regardless, because the conservative case is the one that causes harm if mishandled.

---

## 2. Data Classification

Classification scale (ECM-local, ordered):

- **C0 — Public/operational** — no privacy concern; safe to log, export, surface anywhere.
- **C1 — Internal** — operational data with no behavioral inference; safe in admin-visible diagnostics, not deliberately published.
- **C2 — Behavioral-sensitive** — reveals an individual's (or household's) media-consumption behavior. Viewing habits. Treated as PII-adjacent: not legally PII in most jurisdictions, but it's the kind of data whose disclosure causes real interpersonal/embarrassment/profiling harm. Default-deny across the user boundary; never in logs/exports.
- **C3 — Relationship-sensitive (operator)** — reveals the operator's commercial relationship with upstream providers (which M3U services they subscribe to, how heavily). Lower harm than C2 to an *end user*, but still operator-private; should not be exposed to non-admin users and should not leak into diagnostics that might be shared publicly (Discord, GitHub issue attachments).
- **C4 — Secret** — credentials, tokens. Not present in `session_telemetry`. Listed only to note its absence.

### 2.1 `session_telemetry` field classification

| Field | Classification | Rationale | Handling rules |
|---|---|---|---|
| `session_id` | **C1** alone; **C2** in combination | A random UUID is meaningless by itself, but it's the join key that reconstructs a full viewing session (channel sequence, timing). Treat as C2 the moment it's correlated with `user_id`/`channel_id`. | Do not log raw `session_id` alongside `user_id`. Fine in DB. Not in support bundles. |
| `observed_at` | **C1** alone; **C2** in combination | A timestamp is C1; "user X was watching at 02:14" is C2. | Never log together with `user_id` + `channel_id`. |
| `user_id` | **C2** | The pivot for "whose viewing history." Even as an opaque integer it's re-identifiable against the `users` table. Household-or-person (see §1.2). | Default-deny across the user boundary (§3). Never in logs, support bundles, diagnostic exports, or Discord automation. Rollups keyed by `user_id` are still C2. |
| `channel_id` | **C1** alone; **C2** when joined to `user_id` | The channel catalog is operator-internal but not behavioral; "channel 412 was watched 9000 min total" is C1-ish aggregate. "User X watched channel 412" is C2. | Aggregate-by-channel (no user dimension) may appear in operator-visible stats. Per-user channel history is C2. |
| `provider_id` | **C3** | Reveals which upstream M3U providers the operator uses and how heavily — a commercial-relationship signal. Bounded cardinality (<20). Not end-user PII. | Operator-visible (admin) panels OK. Not exposed to non-admin users (recommendation — see §3 / §8 Q3). Not in publicly-shareable diagnostics. Allowed as a Prometheus label per SRE pre-clearance (epic), unlike `user_id`/`channel_id` which are vetoed as labels. |
| `bytes_delta` | **C1** alone; **C2** in combination | Bandwidth volume is operational. But high-resolution `bytes_delta` over time *is* a behavioral fingerprint (you can infer what was watched from the bitrate curve). Combined with `user_id` → C2; combined with `provider_id` → C3. | Aggregates OK in operator stats. Don't log per-row deltas keyed to a user. |
| `buffer_event_count` | **C1** alone; **C3** in combination with `provider_id` | Buffer events are a quality metric. Per-provider buffer rates are the operator's keep/drop signal — C3. Not behavioral about a user. | Operator-visible. Fine in aggregate. |
| `poll_interval_ms` | **C0** | Pure operational metadata about the poller. | No restriction. |

**The combination rule is the important one.** Almost every field is benign in isolation and sensitive in combination — specifically, *any tuple that includes `user_id` and a time/channel/byte dimension is C2*. Engineers and reviewers should treat "does this code path put `user_id` next to `observed_at`/`channel_id`/`bytes_delta` somewhere a non-admin or a log reader can see it?" as the litmus test.

### 2.2 Derived artifacts

| Artifact | Classification | Notes |
|---|---|---|
| `watch_time_by_user_daily` rollup (ADR-007) | **C2** | Coarsened (daily granularity, no `session_id`) but still per-user behavioral data. Retention-forever per current plan — accepted, but it stays C2 forever; it's not "anonymized" by rolling up. |
| `provider_performance_daily` rollup (ADR-007) | **C3** | Per-provider aggregates. No user dimension. Operator-private. |
| Channel-only aggregates (e.g., total minutes per channel, popularity) | **C1** | No user dimension → not behavioral about an individual. This is the safe shape for any broadly-visible stat. |
| API responses from `/api/stats/providers/*` | **C3** | Contain provider attribution. Admin-or-all-authenticated is a PO call (§8 Q3); recommend admin-only. |
| API responses from the Users panel read API (skqln.5) | **C2** | Must be scoped — see §3. |

---

## 3. Access-Control Boundary

**Decision (Security domain — this stands unless challenged on reasoning):**

> **Least-privilege default: a non-admin user sees only their own `user_id`'s watch-time data. Cross-user visibility (any other user's, or the all-users view) is admin-only. Provider-attribution data (the Providers panel and `/api/stats/providers/*`) is recommended admin-only (operator-private, C3) — but whether non-admins may see it is a PO call (§8 Q3).**

Concretely, for the implementing engineers (skqln.5 backend, skqln.6 frontend, skqln.16 backend, skqln.18 frontend):

1. **Watch-time read API (skqln.5)** must derive the target `user_id` from the authenticated principal, **not** from a client-supplied parameter — *unless* the caller is an admin, in which case an explicit `user_id` query param (or "all users") is permitted. A non-admin passing `?user_id=<someone else>` gets `403`, not someone else's data. This must be a server-side check; the frontend hiding the control is not sufficient.
2. **When `auth.require_auth=False`** (single-user install, no login), there is effectively one principal — the Users panel shows that install's aggregate. That's acceptable: the threat ("one household member surveils another via the panel") only exists when there *are* distinct accounts, and the operator chose to run without auth. Document this in the user guide (skqln.9) so it isn't a surprise.
3. **Provider panels (skqln.16/.18)** — gate behind `RequireAdminIfEnabled` (matching `backup.py`'s admin-only pattern) per the recommendation. If the PO decides provider stats are fine for all authenticated users (Q3), drop to `RequireAuthIfEnabled` — but never make `/api/stats/providers/*` exempt in `AUTH_EXEMPT_PATHS`.
4. **No `user_id` enumeration oracle.** The admin "all users" view returns the list of users the admin can already see in the Users-management UI — it must not become a way to discover accounts an admin otherwise couldn't see (there's only one admin tier today, so this is mostly a "don't regress later" note).
5. **Tests must assert the boundary** (see §6): non-admin → own data only; non-admin requesting another `user_id` → 403; provider endpoints → 403 for non-admin (if admin-only); endpoints absent from `AUTH_EXEMPT_PATHS`.

Rationale: the harm scenario for C2 data is *interpersonal*, not external-attacker — a roommate, parent, partner, or guest with a non-admin login using the panel to see what someone else watched. Least-privilege-by-default closes that without any feature loss for the legitimate use case (each person sees their own history; the operator sees the aggregate). The cost is one server-side authz check per endpoint — cheap, and exactly the pattern `backup.py` already uses.

---

## 4. DREAD Threat Analysis

DREAD scoring, 1–10 per dimension (Damage, Reproducibility, Exploitability, Affected users, Discoverability), averaged to a 1–10 risk score, mapped to the persona's standard severity bands (Crit 9–10, High 7–8.9, Med 4–6.9, Low 0.1–3.9). "Status" ∈ {**existing** (already enforced), **to-build** (a Stats-v2 bead must implement it), **accepted-risk** (PO-signed deviation)}.

| # | Threat | D | R | E | A | Di | Score | Severity | Mitigation | Status | Bead |
|---|---|--|--|--|--|--|--|--|--|--|--|
| **T1** | **Non-admin reads another user's watch history** by passing a forged `user_id` to the watch-time read API (the IDOR / broken-object-level-authz classic). | 7 | 9 | 7 | 6 | 6 | **7.0** | **High** | Server-side derive `user_id` from principal; admin-only override; 403 on cross-user request by non-admin (§3.1). | to-build | skqln.5 |
| **T2** | **Watch-time / behavioral data leaks into application logs** — a `logger.info(f"...user {uid} watching {channel_id} at {ts}")` style line, or an exception dump that includes a row. | 6 | 8 | 5 | 7 | 5 | **6.2** | **Medium** | Redaction rules §7: no `user_id`+time/channel tuple in any log line; `BandwidthTracker` write path logs counts/aggregates only; exception handlers don't echo rows. Lint/grep test. | to-build | skqln.2, skqln.3, skqln.12 |
| **T3** | **Behavioral data leaks into a support bundle / diagnostic export** that the operator then posts publicly (forum, GitHub issue). | 7 | 6 | 4 | 5 | 6 | **5.6** | **Medium** | Support-bundle / diagnostic-export generators must exclude `session_telemetry` rows and per-user rollups (or include only C1 channel-aggregates). Documented in §7; verified when/if such an export exists. | to-build | (whichever bead owns support-bundle export; note for PO if none) |
| **T4** | **Provider attribution leaks into the Discord release-note / digest automation** (the `m3u_digest_template` / Discord alert path), revealing the operator's provider list to a Discord channel. | 4 | 5 | 3 | 4 | 5 | **4.2** | **Medium** | Discord automation must never template `provider_id`/provider-name or `session_telemetry` aggregates into messages. §7. Confirm the existing digest template doesn't already pull provider names. | to-build / verify | skqln.12, and audit `m3u_digest_template.py` / `alert_methods_discord.py` |
| **T5** | **Indefinite-retention rollups become a long-tail privacy liability** — `watch_time_by_user_daily` kept forever means a years-long behavioral dossier per household, surviving long after anyone remembers it exists. | 5 | 8 | 2 | 6 | 3 | **4.8** | **Medium** | Retention *minimum* in §5; ADR-007 sets the max. Recommendation: cap the per-user rollup at a bounded window (see §5.3) rather than literally forever; provider rollups (no user dimension) can stay longer. PO call on the ceiling. | to-build | skqln.1 (ADR-007) |
| **T6** | **No consent / visibility surface** — users have no way to see that ECM is recording their viewing history or what it has on them. Silent behavioral tracking. | 6 | 9 | 1 | 8 | 7 | **6.2** | **Medium** | §6 consent/visibility UX requirement: a per-user "what's recorded about you" view and a plain-language disclosure in the user guide + first-run/admin docs. Opt-out is a PO call (§8 Q1). | to-build | skqln.6 (the panel is the natural home), skqln.9 (docs) |
| **T7** | **`user_id` becomes a Prometheus metric label** (or `channel_id`), exporting per-user behavior into the metrics pipeline (high cardinality *and* a privacy leak — metrics are often scraped by external dashboards). | 7 | 7 | 3 | 6 | 4 | **5.4** | **Medium** | SRE veto already standing: `user_id`/`channel_id` NEVER metric labels; `provider_id` is allowed (bounded <20, C3, operator-private). Enforce in skqln.12 instrumentation review. | existing (SRE veto) + to-build (enforce) | skqln.12 |
| **T8** | **`user_id` not nulled on account deletion** — `ON DELETE SET NULL` is in the schema, but a rollup table built later might copy `user_id` and not get the same cascade, leaving orphaned identifiable history after a user is deleted. | 5 | 6 | 3 | 5 | 3 | **4.4** | **Medium** | ADR-007 must specify: rollup tables either (a) carry the same `ON DELETE SET NULL`/cascade, or (b) the pruning/rollup job scrubs rows whose `user_id` no longer resolves. Don't let "delete my account" leave a behavioral shadow. | to-build | skqln.1 (ADR-007), skqln.2 |
| **T9** | **Heatmap / aggregate endpoint re-identification** — the channels-by-provider heatmap or a small-N install's "all users" aggregate is granular enough that a single user's behavior is trivially inferable (k=1). | 4 | 5 | 4 | 4 | 4 | **4.2** | **Medium** | Acknowledge: in a 1–2 user install, *all* aggregates are effectively per-user — that's inherent and acceptable (the operator is the user). The mitigation is the access-control boundary (§3), not k-anonymity: don't show non-admins the all-users/heatmap views. No suppression-threshold needed for a self-hosted single-operator app. | accepted-risk (documented) | — |
| **T10** | **`session_id` correlation across channels reveals a full viewing session** to anyone who can read raw rows (e.g., a future "export my data" feature dumps raw `session_telemetry`). | 5 | 6 | 3 | 5 | 3 | **4.4** | **Medium** | The per-user visibility view (§6) should show *rollup-level* data (daily minutes per channel), not raw per-poll rows — raw rows are higher-resolution than the user needs and higher-risk if exported. PII-minimization: surface rollups, not raw rows, to users. | to-build | skqln.6 |
| **T11** | **Migration / fixture data leaks real viewing history** — the 5M-row seeded fixture for migration testing (skqln.2/.10) uses real-looking or, worse, real `user_id`s and gets committed. | 6 | 4 | 3 | 4 | 5 | **4.4** | **Medium** | Fixture generator must use synthetic `user_id`s (sequential ints with no link to any real account) and synthetic provider/channel IDs. No production DB dumps in test fixtures. Note for skqln.10 fixture-generator update. | to-build | skqln.2, skqln.10 |
| **T12** | **`bytes_delta` time-series fingerprinting** — even without `channel_id`, a high-resolution per-user `bytes_delta` curve can be matched against known-content bitrate profiles to infer *what* was watched. | 4 | 4 | 2 | 4 | 3 | **3.4** | **Low** | Inherent to storing bandwidth telemetry; the realistic exposure is "someone with DB access," which is already the operator. Mitigation: don't expose per-user per-poll `bytes_delta` outside the DB (covered by §3 and the redaction rules). No further action. | accepted-risk (documented) | — |

**Coverage note:** 12 threats spanning the three new data classes (user behavioral / channel / provider attribution), the access-control boundary, retention, the four diagnostic-leak channels named in the bead (logs, support bundles, diagnostic exports, Discord automation), PII minimization (raw rows vs. rollups), and the consent/visibility gap. Highest residual risk after planned mitigations is **T1 (High)** — and that's a single server-side authz check away from Medium. Everything else is Medium or below.

---

## 5. Retention — Security MINIMUM (for ADR-007)

This section is the deliverable ADR-007 (skqln.1) authors must incorporate. **Security sets the floor; SRE/DBA set the operational ceiling and the enforcement mechanism.**

### 5.1 The minimum

> **There is no security-imposed *minimum* retention on raw `session_telemetry` rows. Zero days of raw retention is acceptable from a privacy standpoint — shorter is strictly safer for C2/C3 data. The feature's *functional* need (the team's "keep raw 30 days" plan, so the 7/30/90-day Users-panel selector works without an immediate rollup dependency) is the real floor, and Security does not object to 30 days raw. Security objects only if raw retention is proposed *beyond* what the panels functionally need — every extra day of raw per-poll `user_id`-keyed rows is additional C2 exposure for no privacy benefit.**

In other words: the privacy-minimum is 0; the functional-minimum is whatever the panels require (the team says ~30 days raw); Security's position is "30 days raw is fine, don't go higher than the feature needs, and the rollups are where the long-tail risk lives — bound them."

### 5.2 Mandatory deletion guarantees (these ARE security requirements, not just operational nice-to-haves)

ADR-007 **must** specify all of:

1. **A pruning job actually runs and is monitored.** Raw rows past the chosen window are deleted on a schedule; if the job falls behind, an alert fires (SRE owns the alert). "Retention policy exists on paper but the cron is broken" is the failure mode that turns 30 days into forever.
2. **Account deletion scrubs behavioral history.** When a `users` row is deleted, every `session_telemetry` row and every rollup row for that `user_id` is either deleted or has `user_id` set to NULL — *including in rollup tables created later* (see T8). This must be tested.
3. **Rollups inherit the classification, not an exemption.** `watch_time_by_user_daily` is C2 forever; rolling up is coarsening, not anonymization. If the PO wants a literally-forever per-user rollup (current plan), that's the PO's risk to accept (§8 Q2) — but it must be an explicit acceptance in ADR-007, not a default that slid in.

### 5.3 Recommendation (not a hard requirement — PO/ADR-007 call)

- **Cap the per-user rollup (`watch_time_by_user_daily`) at a bounded window** — e.g., rolling 13 months (covers year-over-year comparison) or 2 years — rather than literally forever. The Users panel's longest selector is 90 days; nothing in the *feature* needs five-year-old per-user data. A bounded ceiling limits the dossier-accumulation risk (T5) at essentially no functional cost.
- **Provider rollups (`provider_performance_daily`) carry no user dimension (C3, not C2)** and *do* have a stated need for multi-quarter trend analysis (provider keep/drop at renewal — per the GH-59 pull-in). Keeping these longer (e.g., 2–3 years, or forever) is fine — there's no individual-privacy concern, only operator-private commercial data the operator owns.
- **Net:** if the team wants a single simple rule, "raw 30 days; per-user rollup capped at ~2 years; provider rollup retained long-term/forever" satisfies the feature, the GH-59 quarterly use case, *and* the privacy posture. The "rollups forever" line in the epic is acceptable **only for the provider (non-user) rollup**; for the per-user rollup, prefer a cap and require explicit PO acceptance if forever is chosen anyway.

---

## 6. Consent / Visibility UX Requirement

**Requirement (Security asks; UX owns the design; PO owns the opt-out decision):**

1. **Disclosure.** ECM must tell users, in plain language, that it records per-user viewing history (which channels, when, how much). Home: the Stats-v2 user-guide entry (skqln.9) **and** a short note surfaced in the app where a user would encounter the Users panel — not buried. First-run / admin-setup docs should mention it too.
2. **"What's recorded about you" view.** A user (any authenticated user, scoped to their own `user_id`) must be able to see what `session_telemetry`-derived data exists about them. The Users panel itself is the natural home — it already shows "your watch time by channel." It should be framed not just as a feature ("look at your stats!") but as transparency ("this is what's recorded"). Show **rollup-level** data (daily minutes per channel), not raw per-poll rows (T10).
3. **Opt-out — flagged for PO (§8 Q1).** Whether a user (or the operator on a user's behalf, or globally) can disable recording of their viewing history is a **PO decision**, not Security's call. Security's *recommendation*: support at least an operator-level global toggle ("don't record per-user watch history") — it's cheap (gate the `user_id` write in `BandwidthTracker`; write NULL instead), it's the strongest privacy lever, and some operators in shared-household setups will want it. A per-user opt-out is nicer but more work; not required. **Document the decision in ADR-007 or the epic either way** — "we considered opt-out and decided X" is the minimum, so skqln.8 (post-impl sign-off) can check the as-built against the decision.

---

## 7. Redaction Requirements — Hand-off to the Implementing Engineer

The following are **acceptance criteria** for the schema/write beads (skqln.2, skqln.3), the instrumentation bead (skqln.12), and the read-API beads (skqln.5, skqln.16). They mirror the redaction discipline in `threat_model_dbas_import.md` §4.9.

1. **No behavioral tuple in application logs.** No log line — at any level, including DEBUG and exception dumps — may contain `user_id` together with any of `observed_at`, `channel_id`, `session_id`, or per-row `bytes_delta`. The `BandwidthTracker` single-write path logs **counts and aggregates only** ("wrote 47 telemetry rows", "poll completed in 31ms"), never row contents keyed to a user. Exception handlers that catch DB errors on the telemetry write must not echo the row. *(T2)*
2. **`session_telemetry` excluded from support bundles / diagnostic exports.** Any support-bundle or diagnostic-export generator must exclude `session_telemetry` rows and per-user rollup tables. If a bundle needs *any* stats data for debugging, it includes only **C1 channel-only aggregates** (no `user_id` dimension). If no such export feature exists yet, this becomes a requirement on whatever bead introduces one. *(T3)*
3. **`provider_id` / provider names never in Discord automation.** The Discord release-note / digest path (`alert_methods_discord.py`, `m3u_digest_template.py`, `task_engine` digest jobs) must not template `provider_id`, provider names, or any `session_telemetry`-derived aggregate into outbound Discord messages. Audit the existing digest template to confirm it doesn't already pull provider names; if it does, that's a pre-existing C3 leak to file separately. *(T4)*
4. **`user_id` / `channel_id` never become Prometheus metric labels.** Standing SRE veto — re-state it as a redaction rule. `provider_id` is permitted as a label (bounded <20, C3, operator-private). Any new metric in skqln.12 that would put `user_id` or `channel_id` in a label fails review. *(T7)*
5. **Read-API responses are access-scoped, not just filtered client-side.** The watch-time read API derives `user_id` from the authenticated principal for non-admins; admins may pass an explicit `user_id`/"all". A non-admin requesting another user's data gets `403`. Provider endpoints gate on `RequireAdminIfEnabled` (recommended) and are never in `AUTH_EXEMPT_PATHS`. *(T1, §3)*
6. **User-facing visibility view shows rollups, not raw rows.** The "what's recorded about you" surface (the Users panel) presents daily-granularity rollup data, not raw per-poll `session_telemetry` rows. If a future "export my data" feature is built, it too exports rollups, not raw rows — or if it must export raw rows, that needs its own privacy review. *(T10)*
7. **Test fixtures use synthetic identities.** The 5M-row migration/benchmark fixture (skqln.2, skqln.10) uses synthetic `user_id`/`provider_id`/`channel_id` values with no linkage to real accounts. No production DB dumps in committed fixtures. *(T11)*
8. **Account deletion scrubs the behavioral trail.** Verified by test: deleting a `users` row leaves no `session_telemetry` row (or rollup row) carrying that `user_id` — either deleted or NULLed, in raw *and* rollup tables. *(T8, and a hard ask on ADR-007.)*

---

## 8. PO Decisions — resolved 2026-05-12

- **Q1 — Opt-out support → operator-level global toggle only** (no per-user opt-out) for v0.17.0. When the toggle is off, `session_telemetry` collection is disabled entirely. The §6.1 disclosure is surfaced regardless of the toggle. Recorded in ADR-007 and to be verified by the post-impl sign-off (skqln.8).
- **Q2 — Per-user rollup horizon → 400 days (≈13 months).** Matches Security's recommendation to bound the C2 per-user rollup; "rollups forever" is not adopted. ADR-007 D4.
- **Q3 — Provider-attribution visibility → admin-only.** The Providers panel and `/api/stats/providers/*` use `RequireAdminIfEnabled`. Provider attribution (C3, operator-private commercial data) is not exposed to non-admin users.
- **Q4 — Support-bundle / diagnostic-export ownership → forward requirement.** No decision needed now; §7.2 stays a forward requirement and gets a follow-up review item on whatever bead introduces a support-bundle/diagnostic-export surface. (Restated under §9.)

---

## 9. Sign-off Status

- **Pre-implementation threat model + data classification:** complete (this document).
- **Schema sign-off (gates skqln.2 merge):** the DBA's refined `session_telemetry` schema (§1) is **acceptable from a privacy standpoint** with the following conditions carried into the schema/migration bead: (a) `user_id` FK keeps `ON DELETE SET NULL` (already specified) — and ADR-007 extends the same scrub to rollup tables (T8); (b) the redaction requirements in §7 are added to skqln.2/.3 acceptance criteria; (c) test fixtures use synthetic identities (§7.7). No additional columns required; no columns need to be removed for data-minimization (the schema is already minimal — no IP, no user-agent, no device fingerprint, no derived bitrate). **Conditional sign-off granted; convert to full sign-off when (a)–(c) are reflected in the skqln.2 bead.**
- **Retention sign-off (informs skqln.1):** §5 is the input. Full sign-off on ADR-007 happens when ADR-007 incorporates the §5.2 mandatory guarantees; the §5.3 recommendations are advisory.
- **Post-implementation sign-off (skqln.8):** out of scope here — that bead re-checks the as-built against §3, §6, and §7.

---

## 10. References

- `docs/security/threat_model_dbas_import.md` — house style; redaction-discipline precedent (§4.9).
- `docs/auth_middleware.md` — global secure-by-default auth; `RequireAdminIfEnabled` / `RequireAuthIfEnabled`; `AUTH_EXEMPT_PATHS`.
- `backend/auth/dependencies.py` — `get_current_active_admin`, `require_admin_if_enabled` (the admin-gate pattern §3 references).
- `backend/routers/stats.py` — existing `/api/stats/*` router (channel/popularity/activity/bandwidth) — Stats v2 adds persisted per-user history alongside it.
- `backend/routers/backup.py` — existing admin-only endpoint pattern (`RequireAdminIfEnabled`).
- Epic `enhancedchannelmanager-skqln` — scope-history trail, PO override (2026-04-23) pulling GH-59 in, SRE Prometheus-label veto, "raw 30d / rollups forever" plan.
- `enhancedchannelmanager-skqln.2` — `session_telemetry` schema + Alembic migration (blocked by this doc).
- `enhancedchannelmanager-skqln.1` — ADR-007 retention/rollup policy (consumes §5).
- `enhancedchannelmanager-skqln.5/.6/.16/.18` — Users / Providers read APIs + panels (must satisfy §3 and §7).
- `enhancedchannelmanager-skqln.8` — post-implementation privacy sign-off (re-checks against this).
- `enhancedchannelmanager-skqln.9` — Stats v2 user guide (home for the §6.1 disclosure).
- `enhancedchannelmanager-skqln.12` — observability instrumentation (enforces §7.4, audits §7.3).
