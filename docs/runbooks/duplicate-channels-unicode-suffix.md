# Runbook: Duplicate Channels — Unicode Suffix Divergence

> Two channels exist for the same logical network because one copy carries a Unicode suffix (ᴴᴰ, ᴿᴬᵂ, ², ³, ZWSP, ZWJ, NFD-decomposed accents) and the other does not. Normalization should have collapsed them into one; it did not. Triage via Test Rules, fix via Re-normalize Existing Channels.

- **Severity**: P2
- **Owner**: SRE (primary), Project Engineer (normalization-engine specialist, support)
- **Last reviewed**: 2026-04-22
- **Related beads**: `enhancedchannelmanager-eio04` (epic), `enhancedchannelmanager-eio04.10` (this runbook), `enhancedchannelmanager-eio04.1` (unified policy), GH-104

## Alert / Trigger

This runbook is **manually triggered**. There is no Prometheus alert on duplicate channels directly — dedupe is a correctness property, not a latency/error budget. Expect to reach this runbook via one of:

- **User report** — "two copies of ESPN appeared after a refresh," "the channel list shows `RTL` and `RTL ᴿᴬᵂ` as separate entries," "we merged these last week, they came back."
- **Auto-creation audit** — `ecm_auto_creation_channels_created_total{normalized="false"}` incremented unexpectedly for a rule that has a normalization group configured. Sampled INFO log under the same rule_id shows the raw names.
- **Post-incident sweep** — after a normalization rule change lands, operator wants to know if any now-collapsible duplicates exist.
- **Canary fired first** — if `ecm_normalization_canary_divergence_total` went non-zero, stop here and follow [`normalization-canary-divergence.md`](./normalization-canary-divergence.md) instead. The canary is the leading indicator; duplicates on disk are the lagging symptom.

## Symptoms

What the responder observes:

- Two channel rows in the channels list for the same logical network. One carries a suffix, letter-superscript, numeric-superscript, ZWSP/ZWJ, or an NFD-decomposed accent; the other does not.
- `channel_watch_stats` / `ecm_watch_sessions_*` cardinality shows both rows as distinct — viewer counts split, popularity rankings degrade, EPG matches may land on the wrong row.
- Log signatures (grep `docker logs ecm-ecm-1`):
  - `[AUTO-CREATE-EXEC]` lines show `Lookup 'X': not found` for a name that should have matched an existing channel.
  - `[SAFE_REGEX]` WARN lines mentioning a `rule_id` imply the normalization rule's pattern timed out or failed to compile — the rule effectively did not run for that input.
  - **Absence** of a normalization decision log line (`NORMALIZATION_DECISION_LOGGER`, sampled INFO) for the raw input under any rule_id means no rule matched.

If the duplicates appeared *after* the bd-eio04.1 unified-policy cutover but *before* operator-driven Re-normalize Existing Channels, the duplicates are stale pre-fix data — see Resolution B.

## Diagnosis

Ordered, if/then. Do not skip steps — the wrong resolution for the wrong cause will mask the underlying bug.

1. **Identify the duplicate pair.** Use the channels list search or query the DB:

   ```bash
   docker exec ecm-ecm-1 sqlite3 /config/journal.db \
     "SELECT id, name FROM channels WHERE name LIKE '%ESPN%' ORDER BY name;"
   ```

   Note both `id` and exact `name` for each row. Copy the `name` bytes exactly — do not retype; Unicode differences are the root cause of the ticket and the human eye does not reliably distinguish `RTL` from `RTL` + U+200B.

2. **Paste both raw names into Settings → Normalization → Test Rules.**

   - If both inputs produce the **same output**, the normalization rule set is correct today — the duplicates are stale data from before the rule/policy change. Go to **Resolution B**.
   - If the two inputs produce **different outputs**, a rule is missing or broken for this suffix class. Go to **Resolution A**.
   - If Test Rules returns HTTP 5xx or the preview hangs, check the logs for `[SAFE_REGEX]` WARN on the rule_id. Go to **Resolution C**.

3. **Check for `[SAFE_REGEX]` WARN in logs.**

   ```bash
   docker logs ecm-ecm-1 2>&1 | grep '\[SAFE_REGEX\]' | tail -20
   ```

   A WARN line that names a rule_id appearing in your Test Rules preview means the pattern is pathological (timeout, oversize, compile error). Go to **Resolution C** even if step 2 suggested A or B — fix the rule first, then re-diagnose.

4. **Correlate with the canary.** If `ecm_normalization_canary_divergence_total` is non-zero for the same period, the divergence is between Test Rules and the auto-create executor — not between "correct rule" and "stale channel name." Stop this runbook and follow [`normalization-canary-divergence.md`](./normalization-canary-divergence.md).

**Escalate** if:

- Scope > 100 channels. Bulk rename/merge over that size warrants a postmortem and a staged rollout plan. Do not run Re-normalize Existing Channels against a set that large without PO authorization.
- The duplicate pair differs by characters outside the known class (not a superscript, not a Cf code point, not an NFD accent). That is a new Unicode class — file a bead and attach the raw bytes before acting.
- The duplicate rows are in use by an active Export Profile or a Channel Profile with downstream consumers. Fixing the duplication will change the channel ID that downstream references; the consumer must be coordinated.

## Resolution

Pick the resolution the diagnosis routed you to. Do not combine.

### Resolution A — Normalization rule is missing or broken for this suffix class

1. **Author or fix the rule.** Settings → Normalization → add a regex rule (or edit an existing one) that collapses the offending suffix. Follow the Regex section in [`docs/style_guide.md`](../style_guide.md) <!-- pending bd-eio04.8 — this doc is in-flight as of 2026-04-22; the linter enforces the same rules regardless --> for pattern hygiene. The write-time linter (`/api/normalization/lint-findings` + 422 on save) rejects bounded-repetition abuse, nested quantifiers, and lookbehind-lookahead combinations that trip ReDoS.

2. **Test Rules preview — both inputs.** Paste both raw names again. Both must now produce the same output. If not, the rule is still wrong; iterate.

3. **Dry-run Re-normalize Existing Channels.**

   ```bash
   curl -sS -X POST "http://localhost:6100/api/normalization/apply-to-channels?dry_run=true" \
     -H "Authorization: Bearer $TOKEN" | jq '.channels_with_changes, .diffs[] | {channel_id, current_name, new_name, collision}'
   ```

   Or use the UI: Settings → Normalization Rules → **Apply to existing channels** → review the diff.

4. **Review collisions.** For each row with `"collision": true`, the new normalized name matches another existing channel — choose `merge` to fold into that channel, not `rename`. `rename`-into-collision is rejected with HTTP 422 at execute time (bd-u9odj).

5. **Execute with per-row actions.**

   ```bash
   curl -sS -X POST "http://localhost:6100/api/normalization/apply-to-channels?dry_run=false" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"actions":[{"channel_id": 42, "action": "merge", "target_channel_id": 17}, {"channel_id": 99, "action": "rename"}]}'
   ```

   Every rename/merge is written to the journal (`/api/journal`) with the rule_set_hash at execute time — undo path is journal-driven (see "Undo" in [`docs/normalization.md`](../normalization.md#re-normalize-existing-channels)).

6. **Verify:**

   - Test Rules: both raw inputs produce identical output.
   - Channels list: only one row for the logical network.
   - Logs: no new `[SAFE_REGEX]` WARN lines under this rule_id.
   - `ecm_auto_creation_channels_created_total{normalized="false"}` rate returns to baseline.

### Resolution B — Rules are correct, channel data is stale

1. **Skip rule authoring.** The rules already collapse both inputs to the same output; the only work is to rewrite the stored names.

2. **Dry-run, review, execute.** Identical procedure to Resolution A steps 3–5, but you expect every row's `current_name` to be an existing (pre-fix) variant and `new_name` to be the already-correct canonical form.

3. **Scope-narrow via per-row actions.** For a targeted sweep, send only the `channel_id`s you want fixed in the `actions` array. Unspecified channels default to `skip` and are left alone.

4. **Verify** — same four checks as Resolution A.

### Resolution C — Rule pattern is pathological (`[SAFE_REGEX]` WARN)

1. **Edit the offending rule's pattern** per the Regex section in [`docs/style_guide.md`](../style_guide.md) <!-- pending bd-eio04.8 -->. Common moves: replace nested quantifiers with character classes, replace bounded repetitions over 1000 with unbounded anchored matches, remove lookbehind with variable-length match.

2. **Re-save the rule.** The write-time linter (bd-eio04.5 + bd-eio04.7) fires on save and rejects a still-pathological pattern with HTTP 422. If the save succeeds, the pattern passed the linter but may still be ReDoS-prone against exotic inputs — watch `[SAFE_REGEX]` WARN for another hour before declaring it fixed.

3. **Re-run Test Rules** against both original inputs. Expect fast return and matching output.

4. **Continue with Resolution B** — the rules are now correct; only the stored names need rewriting.

### Verification (all resolutions)

Paste-ready checklist:

```bash
# 1. Both raw inputs normalize to the same output
curl -sS -X POST http://localhost:6100/api/normalization/test-batch \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"texts": ["<raw name A>", "<raw name B>"]}'
# Expected: .results[*].normalized equal.

# 2. Channels list shows one row for the logical network
docker exec ecm-ecm-1 sqlite3 /config/journal.db \
  "SELECT id, name FROM channels WHERE name LIKE '%ESPN%' ORDER BY name;"

# 3. No new [SAFE_REGEX] WARN lines
docker logs --since 5m ecm-ecm-1 2>&1 | grep '\[SAFE_REGEX\]'
# Expected: empty.
```

If any step fails, **stop** and escalate — do not improvise a merge from the UI mid-runbook.

## Escalation

If the above does not resolve within **2 hours** of the initial user report:

- Page the Project Engineer on-call rotation (or the engineer who last touched `backend/normalization_engine.py` per `git log`).
- Provide: `trace_id` from the Test Rules preview request (returned in the `X-Request-ID` response header), the two raw names (copy-paste, not retyped), affected `channel_id`s, before/after names from the dry-run diff, and any `[SAFE_REGEX]` WARN log excerpts with the rule_id.
- If the affected scope spans more than one M3U account / provider, also notify the PM — cross-provider dedupe usually needs a policy decision before execution.

## Post-incident

- [ ] **Mandatory**: add the offending input pair to `backend/tests/fixtures/unicode_fixtures.py` with `origin="runbook-YYYY-MM-DD"` and a `notes` field pointing to this runbook incident. Without this step, the same regression can slip back in.
- [ ] Confirm the journal entry for each rename/merge is present (`/api/journal?category=normalization` or the UI Journal view). The journal is the undo path.
- [ ] If this is the second Unicode-suffix incident in 30 days, file a bead to extend the linter and/or tighten the fixture bank — recurrence means the class is under-tested.
- [ ] Update this runbook with any step that was unclear or missing. Reference the incident in the update commit.
- [ ] If the user-reported scope was > 10 channels, post a brief note in the user communication channel explaining the one-time rewrite so downstream (Export Profiles, dashboards, dispatcharr consumers) don't see the reshape as a second outage.

## References

- [`docs/normalization.md`](../normalization.md) — user-facing guide to the rules engine, Test Rules / Auto-Create parity, and Re-normalize Existing Channels.
- [`docs/runbooks/normalization-unified-policy.md`](./normalization-unified-policy.md) — the `ECM_NORMALIZATION_UNIFIED_POLICY` rollback switch.
- [`docs/runbooks/normalization-canary-divergence.md`](./normalization-canary-divergence.md) — read this first if the canary fired before the user report.
- [`docs/sre/slos.md`](../sre/slos.md#slo-5-normalization-correctness) — SLO-5 definition; duplicates are a correctness concern even if outside the SLI.
- [`docs/api.md`](../api.md#normalization) — `POST /api/normalization/apply-to-channels`, `GET /api/normalization/lint-findings`.
- `backend/normalization_engine.py` — `NormalizationPolicy`, `NormalizationEngine.normalize`, `NormalizationEngine.test_rule`.
- `backend/routers/normalization.py` — apply-to-channels endpoint, lint-findings endpoint.
- `backend/tests/fixtures/unicode_fixtures.py` — regression fixture bank.
- Epic: `enhancedchannelmanager-eio04` — Normalization parity.
