# Normalization

> **Audience:** Operator dealing with noisy stream names — `"Channel Name HD"`, `"[US] Channel Name"`, `"Channel Name (Backup)"` — and wanting them to collapse into a single clean channel name.
>
> **Status:** Stub — articles below are placeholders. The deep technical reference already exists at [`docs/normalization.md`](../../normalization.md); this section will be the operator-task wrapper around it, not a duplicate.

## Section purpose

Show operators how to author normalization rules in the **Settings → Normalization Rules** UI, how to use the **Test Rules** preview before saving, and how to run the **Apply to existing channels** one-time bulk rewrite when the rule set changes. Defer to the existing `docs/normalization.md` for the technical reference (parity contract, policy, SLO-5, regex sandboxing).

## Intended audience

- **Operator** authoring normalization rules.
- **Operator** running the *Apply to existing channels* flow after a rule change.

End users do not read this section.

## Planned articles

| Article | Purpose |
|-|-|
| `concepts.md` | What normalization is and isn't, in operator language. The three places it runs (Test Rules, Auto Create, Apply to existing channels) and why they must agree. Quick pointer to the parity contract for operators who care. |
| `author-your-first-rule.md` | Walk-through: open Settings → Normalization Rules, add a rule, preview it in Test Rules, save it. Includes the "iterate before saving" discipline. |
| `rule-groups-and-ordering.md` | Why groups exist, group priority vs. rule priority, the pipeline semantics (each rule sees the previous rule's output). |
| `condition-and-action-types.md` | Tour of the available condition types (regex, prefix, contains, etc.) and action types (replace, strip, lowercase, etc.) with short examples. |
| `apply-to-existing-channels.md` | The one-time bulk rewrite flow: when to use it, what gets changed, undo/safety notes, expected duration on a large library. |
| `when-things-look-wrong.md` | "Test Rules and Auto Create disagree" — what that means, why it's a bug not a configuration issue, and the path to escalation (link to the canary-divergence runbook). |

## Going deeper

- [`docs/normalization.md`](../../normalization.md) — the existing dual-audience reference. The first half is for operators (concepts, examples, parity contract); the developer reference at the bottom is for engineers integrating with the engine. The user guide articles above will summarise and link, not duplicate.
- [`docs/runbooks/normalization-canary-divergence.md`](../../runbooks/normalization-canary-divergence.md) — what happens when the parity canary fires.
- [`docs/runbooks/normalization-unified-policy.md`](../../runbooks/normalization-unified-policy.md) — operator implications of the unified policy.
