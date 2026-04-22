# Runbook: NormalizationPolicy Unified Mode (GH #104 Rollback Switch)

Owner: SRE. Source: bd-eio04.1 (absorbs bd-eio04.4), under epic bd-eio04.

## What this runbook covers

Starting with bd-eio04.1 (release that closes GH #104), the normalization
engine's Test Rules preview path and its auto-creation execution path
share a single `NormalizationPolicy`. Both paths apply identical Unicode
preprocessing to every input:

1. **NFC canonicalization** — NFD-decomposed input (e.g. `e` + `U+0301`)
   collapses to pre-composed form (`U+00E9`). NFC, not NFKC — ligatures,
   fullwidth digits, and Roman-numeral compatibility forms are preserved.
2. **Narrow Cf-code-point stripping** — `U+200B` ZWSP, `U+200C` ZWNJ,
   `U+200D` ZWJ, and `U+FEFF` BOM are removed. RTL/LTR bidi marks
   (`U+200F`, `U+202E`) are **preserved** so channel names that use them
   intentionally are not mangled.
3. **Full superscript conversion** — both letter-superscripts
   (`ᴴᴰ` → `HD`, `ᴿᴬᵂ` → `RAW`) and numeric-superscripts
   (`ESPN²` → `ESPN2`, `⁶⁰fps` → `60fps`) convert on every code path.
   The prior `preserve_superscripts=True` carve-out was dropped in
   bd-eio04.1 because divergence between paths was the root cause of
   GH #104.

This is a **one-way-door** change for existing channel names: any
channel created before the fix that carried `²`, `³`, or other numeric
superscripts through auto-creation retains those glyphs. New channels
created after the fix will have ASCII digits instead.

## Configuration

| Variable | Default | Meaning |
|-|-|-|
| `ECM_NORMALIZATION_UNIFIED_POLICY` | `true` | When `true` (default), apply the unified policy (NFC + Cf-strip + full superscript conversion) on every normalization code path. Set to `false` to roll back to pre-bd-eio04.1 behavior (superscript conversion only, no NFC, no Cf-stripping). Accepted truthy values: `true`, `1`, `yes`, `on` (case-insensitive). Accepted falsy: `false`, `0`, `no`, `off`. |

The flag is latched at engine construction time. Change by setting the
env var in `docker-compose.yml` (or your container runtime) and
restarting the container. No image rebuild required.

## When to flip the flag to `false`

**This is a rollback switch, not a feature toggle.** Flip it only if
you hit one of the following:

- A report that auto-creation is producing channel names that diverge
  from what operators expect *because* of the new preprocessing (e.g. a
  downstream system that indexed `ESPN²` as the primary key).
- A Unicode edge case you did not anticipate (the engineer's best
  guess would be a homoglyph locale we do not test — track with a new
  bead, then flip the flag while that bead is worked).

Do **not** flip the flag to dodge a related failure elsewhere. If a
test or probe is failing, diagnose the cause first.

## How to flip the flag

```bash
# Example: docker-compose.yml
services:
  ecm:
    environment:
      - ECM_NORMALIZATION_UNIFIED_POLICY=false
```

Then:

```bash
docker compose up -d ecm
# or, for an ad-hoc restart:
docker restart ecm-ecm-1
```

Verify the flag was picked up:

```bash
docker exec ecm-ecm-1 python -c \
  "from normalization_engine import get_default_policy; \
   print(get_default_policy().unified_enabled)"
# Expected: False after rollback
```

## Symptoms → Diagnosis → Action

### Symptom: Test Rules preview and auto-created channel names diverge again

Either the flag was set to `false` (intentional rollback) or the policy
singleton was not reloaded after a flag change. Check:

```bash
docker exec ecm-ecm-1 env | grep ECM_NORMALIZATION_UNIFIED_POLICY
docker exec ecm-ecm-1 python -c \
  "from normalization_engine import get_default_policy; \
   print('unified_enabled:', get_default_policy().unified_enabled)"
```

If the env says `true` but the policy reports `False`, the container
was started before the flag was set. Restart the container.

### Symptom: Channel names now strip characters that users expected to keep

If the "characters" are superscripts, BOM/ZWSP/ZWNJ/ZWJ, or NFD-form
accents, this is the intended behavior — the fix corrected a
long-running divergence. Explain to the user that Test Rules now
matches what auto-creation produces, and their stored names will
converge on the canonical form as channels are recreated.

If the characters are **bidi marks** (U+200F, U+202E), **ligatures**
(`ﬁ`), or **fullwidth digits** (`１`), that's a bug — open a bead
with the reproducer. The policy uses NFC (not NFKC) and a narrow Cf
whitelist specifically to preserve these.

### Symptom: Post-rollback, NFC tests fail in CI

Expected if CI runs against the disabled flag. The parity suite
(`backend/tests/unit/test_normalization_parity.py`) has a
`TestLegacyPolicyFallback` class that exercises the disabled path
directly via `NormalizationPolicy(unified_enabled=False)` — those
remain green. The `TestPinnedRegressions::test_nfd_cafe_normalizes_to_nfc`
test will fail under the rollback, because the rollback explicitly does
not canonicalize NFD. Treat the NFC-specific failures as expected while
the flag is off.

## Decommissioning the flag

Once the unified policy has soaked in production for 30+ days with no
escalations tied to the behavior change, the flag should be removed.
File a follow-up bead under bd-eio04 to:

- Delete `_unified_policy_enabled()` and the `unified_enabled` field
  from `NormalizationPolicy`.
- Inline the unified branch of `NormalizationPolicy.apply_to_text`.
- Delete `TestLegacyPolicyFallback`.
- Remove this runbook.

## Related material

- `backend/normalization_engine.py` — `NormalizationPolicy` definition.
- `backend/tests/unit/test_normalization_parity.py` — parity sweep.
- `backend/tests/fixtures/unicode_fixtures.py` — shared fixture bank (bd-eio04.3).
- GH #104 — the issue this flag backs out.
- bd-eio04.1 / bd-eio04.4 — the beads that landed the unified policy.
