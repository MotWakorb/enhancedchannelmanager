# Runbook: <Alert or Scenario Name>

> One-line summary of what this runbook covers. Keep it readable at 3 AM.

- **Severity**: P1 / P2 / P3 / P4
- **Owner**: <team or persona — e.g. SRE, Project Engineer>
- **Last reviewed**: YYYY-MM-DD
- **Related beads**: `enhancedchannelmanager-xxxxx`, ...

## Alert / Trigger

What fires this runbook. Be specific:

- Alert name and query (Prometheus expression, log pattern, or manual trigger).
- Dashboard link if applicable.
- Manual triggers (e.g. "user reports checkout is broken").

## Symptoms

What the responder observes. Write from the responder's perspective, not the system's:

- User-facing impact (what is broken, for whom).
- Metric signatures (which graphs spike, which flatline).
- Log signatures (characteristic error messages, with trace_id hints if relevant).

## Diagnosis

Ordered steps to confirm the failure mode and rule out lookalikes. Use `if / then` branching, not prose.

1. Check `<metric or dashboard>` — what you are looking for.
2. If <condition>, go to step 3. If not, go to step N (alternate scenario).
3. Run: `exact command here`
   Expected output: `...`
4. ...

State what would make you escalate instead of continuing (scope too large, blast radius unclear, destructive action required without PO authorization).

## Resolution

Ordered steps to restore service. **Mitigate first, root-cause after.** Rollback, scale up, failover, feature-flag off — whatever stops the bleeding.

1. `exact command`
2. `exact command`
3. Verify:
   - `command to confirm fix`
   - Expected: `...`

If any step fails, **stop** and escalate per the Escalation section — do not improvise mid-rollback.

## Escalation

If the above does not resolve within `<time budget>`:

- Page `<persona or on-call>` via `<channel>`.
- Provide: incident start time, symptoms, diagnosis steps run, resolution steps attempted, current state.

## Post-incident

- [ ] Update status page / internal channel if user-facing.
- [ ] Open a bead for root-cause investigation if not yet identified.
- [ ] Schedule postmortem if P1/P2 (use `/postmortem` skill).
- [ ] Update this runbook with anything that was unclear or missing.
- [ ] If a new alert or metric would have caught this earlier, file a bead for it.

## References

- Linked ADRs, bead IDs, prior postmortems, external docs.
