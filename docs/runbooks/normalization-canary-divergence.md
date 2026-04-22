# Runbook: Normalization Canary Divergence

> The nightly canary detected that the Test Rules preview path and the auto-creation executor path produce different output for at least one fixture. This is an **SLO-5 (Normalization Correctness) breach** — zero error budget.

- **Severity**: P2
- **Owner**: Project Engineer (primary) + SRE (support)
- **Last reviewed**: 2026-04-22 (bd-eio04.9)
- **Related beads**: `enhancedchannelmanager-eio04` (epic), `enhancedchannelmanager-eio04.9` (this instrumentation)
- **SLO**: [`docs/sre/slos.md` § SLO-5 Normalization Correctness](../sre/slos.md)

## Alert / Trigger

The canary fires exactly one of the following:

- **Slack alert** (`#ops` or whichever channel the `SLACK_WEBHOOK_URL` secret routes to): title `Normalization canary DIVERGED`.
- **GitHub issue** (durable fallback — always created regardless of Slack): label `regression,normalization`, title `[CANARY] normalization divergence YYYY-MM-DD`.
- **Workflow** (drives both): `.github/workflows/normalization-canary.yml`, job `canary`.

Manual trigger — if you suspect divergence outside the nightly window:

```bash
gh workflow run normalization-canary.yml
```

Or run the harness locally from this repo's `backend/` directory:

```bash
cd backend
python -m scripts.normalization_canary
```

## Symptoms

- The `canary` job on the `Normalization Canary` workflow is red.
- The workflow log ends with `[canary] FAIL — N divergence(s) detected:` followed by a JSON report listing each fixture name, both outputs, and the mismatch reason.
- `ecm_normalization_canary_divergence_total` has incremented by 1 for this run (visible on the Normalization dashboard once metrics are scraped from the CI-adjacent Prometheus).
- Users may or may not have reported impact yet — the canary is deliberately a **leading** indicator. Do not wait for a user report to act.

If the canary job is red but the JSON report is absent / truncated, the harness itself crashed. Treat that as a runbook execution failure — skip to **Escalation**.

## Diagnosis

Work the JSON report top-down. Each `divergences[].reason` is one of two classes — they require different diagnostic moves.

1. **Read the JSON report from the workflow run.**
   - Pull the `normalization-canary-log` artifact from the failed run, or scroll to the `Run canary harness` step for the inline output.
   - For each entry in `divergences[]`, note the `fixture`, `input`, `http_output`, `executor_output`, and `reason`.

2. **Branch on `reason`:**

   - If `reason == "normalized output byte-mismatch"` → one path mutated characters the other didn't. Most likely cause: a preprocessing step was added to one path (or dropped from one path) without the mirror change. **Go to step 3.**
   - If `reason == "matched_rule_ids mismatch"` → both produced the same string but via different rule sequences. Most likely cause: a rule ordering / short-circuit change in one path only. **Go to step 4.**
   - If `reason` starts with `"harness error"` → the canary itself is broken, not the code under test. **Go to step 6.**

3. **Diagnose an output byte-mismatch:**

   ```bash
   git log --oneline -20 backend/normalization_engine.py backend/observability.py backend/routers/normalization.py backend/auto_creation_executor.py
   ```

   - Anything touched since the last green canary is a candidate.
   - Re-run the failing fixture in isolation:

     ```python
     from normalization_engine import NormalizationEngine, get_default_policy
     # ... construct engine against a fresh in-memory session ...
     engine.normalize(INPUT)   # executor path
     engine.test_rule(INPUT, ...)  # Test Rules path
     ```

   - Compare `engine.normalize()` output vs. the `test-batch` HTTP response byte-for-byte. If they already diverge without HTTP in the middle, the issue is in the engine. If they match in-process but HTTP differs, the issue is in JSON encoding / middleware.

4. **Diagnose a `matched_rule_ids` mismatch:**

   - Same git-log move as step 3.
   - The two paths must share one `NormalizationPolicy` instance; confirm that by checking `ECM_NORMALIZATION_UNIFIED_POLICY` is not set to `false` in the failing environment.
   - Inspect each path's `result.transformations`. A path-specific `stop_processing` shortcut or a new rule filter is the usual culprit.

5. **Check the recent commit set.**

   Look for one of these red-flag patterns:

   - `normalize` touched without a matching change to `test_rule` / `test_rules_batch`, or vice versa.
   - A new Unicode codepoint added to `_STRIPPED_CF_CODEPOINTS` on one path only.
   - A change to `_match_single_condition` that bypassed `get_default_policy().apply_to_text(...)`.
   - A new condition / action type that only one path understands.

6. **Harness error path** (rare): check the `Install dependencies` and `Prepare test config directory` steps. A missing dep or permission error at those steps produces a misleading `harness error` in the JSON report. Fix those, re-run the workflow.

**Escalate** if:

- You've been in diagnosis for more than 60 minutes without finding the diverging commit.
- The diverging change is in a dependency (FastAPI, Pydantic, uvicorn) rather than our code.
- You cannot reproduce the divergence locally — the CI environment is producing different results than `python -m scripts.normalization_canary` on your machine.

## Resolution

**Mitigation is not "silence the canary" — it is "close the divergence."** If you cannot close it in one commit, revert the offending change.

Pick the smallest move that makes the canary green, in this order:

1. **Revert the offending commit.**

   ```bash
   git log --oneline backend/normalization_engine.py | head -5
   git revert <offending-sha>
   ```

   - Safe default when the diverging commit is recent and the feature it introduced is deferrable.
   - Open a follow-up bead to re-land the feature behind the unified policy contract.

2. **Land a corrective fix in both paths.**

   - Patch `normalization_engine.py` so `test_rule` / `test_rules_batch` and `normalize` apply the same preprocessing and rule sequencing.
   - The parity tests in `backend/tests/unit/test_normalization_parity.py` must pass before merging — they are the same contract the canary enforces.

3. **Add the failing input to the fixture bank.**

   Regardless of which resolution you chose:

   ```python
   # backend/tests/fixtures/unicode_fixtures.py
   NormalizationFixture(
       name="case_canary_YYYY_MM_DD_<slug>",
       input=<failing_input>,
       expected_normalized=<correct_output>,
       origin="canary-YYYY-MM-DD",
       category=<pick_from_taxonomy>,
       notes="Added after canary divergence on YYYY-MM-DD. See GH issue #<n>.",
   )
   ```

   This is mandatory — without it, the exact same regression can slip back in.

4. **Verify the canary turns green.**

   ```bash
   cd backend
   python -m scripts.normalization_canary
   # Expected: "[canary] PASS — all N fixtures match across paths."
   ```

5. **Close the GH issue.**

   Cross-reference the fixing PR in the issue body before closing.

If any step fails, **stop** and escalate — do not merge half-fixes. A canary that's green because the failing fixture was removed is worse than a red canary.

## Escalation

If resolution is not complete within **4 hours** of the initial alert:

- Escalate to SRE + the engineer who last touched `backend/normalization_engine.py` per `git log`.
- Post to the `#ops` channel (or equivalent) with: incident start time, divergence count, fixture names, commits under suspicion, diagnosis steps already run.
- If release cuts are in-flight, notify the PM — SLO-5 policy blocks the next cut until the canary is green.

## Post-incident

- [ ] Cross-reference the fixing PR in the canary GH issue; close the issue once the next canary run is green.
- [ ] Add the failing input to `unicode_fixtures.py` with `origin=canary-YYYY-MM-DD` (mandatory — see Resolution step 3).
- [ ] If the root cause was a class of bug the canary cannot catch (e.g., a divergence that only appears under load), file a bead for a fuzz / load variant.
- [ ] If this is the second SLO-5 breach within 30 days, schedule a blameless postmortem (`/postmortem` skill) per the SLO-5 error-budget policy.
- [ ] If the runbook was unclear or missing a step, edit this file and reference the incident in the update commit.

## References

- SLO definition: [`docs/sre/slos.md` § SLO-5 Normalization Correctness](../sre/slos.md)
- Canary harness source: `backend/scripts/normalization_canary.py`
- Canary workflow: `.github/workflows/normalization-canary.yml`
- Parity tests (same contract, different cadence): `backend/tests/unit/test_normalization_parity.py`
- Unified-policy implementation: `backend/normalization_engine.py` (`NormalizationPolicy`)
- Observability wiring (metrics + decision log): `backend/observability.py` (`record_normalization_decision`, `NORMALIZATION_DECISION_LOGGER`)
- Epic: bd-eio04 (Normalization parity) — wave 1 closed GH #104, wave 2 added the observability layer this runbook protects.
