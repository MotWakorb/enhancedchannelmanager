# Security Governance Audit — test harnesses

Two harnesses back the guard tests for
`.github/workflows/security-governance-audit.yml`. They exist because the guards
on that workflow have twice been green while asserting nothing: a
"no swallowed exit status" regex that could not match `|| :`, and a
three-outcome guard satisfied entirely by the workflow's own header comment.

## 1. Scenario harness (CI-enforced)

`backend/tests/unit/test_governance_audit_behavior.py`

Runs each of the audit's `run:` bodies under `bash` against a stubbed `gh`, and
asserts the exit status and the operator-visible annotation for each scenario:
a compliant repository, a paused Dependabot, a disabled Dependabot, an expired
credential (401), an under-scoped credential (403), a disabled control (404), a
weakened branch protection, a missing required context, an open secret-scanning
alert with and without a trailing newline, a missing `beads` branch, and the
three cadence-probe outcomes.

It is a pytest module rather than a script so it runs on every push. Nothing to
invoke by hand:

```bash
python3 -m pytest backend/tests/unit/test_governance_audit_behavior.py -q
```

It skips itself if `bash` or `jq` is unavailable. Both are present on
`ubuntu-latest`.

The stub records every `gh` invocation and the `GH_TOKEN` it saw, which is how
the tests assert that the beads-branch read stays on the built-in token and the
elevated reads do not.

## 2. Mutation harness (developer tool)

`scripts/governance_audit_mutants.py`

Applies each of ~35 plausible defects to the real workflow and its caller, runs
the two guard suites against the mutated tree, and reports kill/survive. A
survivor is a hole in the guards.

```bash
python3 scripts/governance_audit_mutants.py           # all mutants
python3 scripts/governance_audit_mutants.py --list    # names and rationale
python3 scripts/governance_audit_mutants.py -k paused # a subset
```

It is **not** wired into CI: it rewrites tracked files and costs one full pytest
run per mutant. It refuses to start against a dirty checkout of the files it
mutates, and restores them with `git checkout` after every mutant, including on
an exception or Ctrl-C — so an interrupted run cannot be mistaken for your own
edits. Commit your work before running it.

Re-run it whenever either guard suite or the audit workflow changes, and paste
the result in the pull request. The properties it proves are enforced
continuously by the two suites it runs; the harness is what proves those suites
are not vacuous.

### Adding a mutant

Add a `Mutant` in `mutants()` with a one-line rationale. Anchor text must occur
**exactly once** in the target file — the harness fails loudly rather than
silently mutating the wrong place, or nothing.
