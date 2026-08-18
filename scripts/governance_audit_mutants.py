#!/usr/bin/env python3
"""Mutation harness for the Security Governance Audit's guard tests.

A guard test that has never been proven red is a guard test that might be
matching its own error message. This repository has hit that failure twice on
this workflow alone: a "no swallowed exit status" guard whose regex could not
match `|| :`, and a three-outcome guard satisfied entirely by the audit's own
header comment. Both looked green, and both were.

So every mutant below is a defect somebody could plausibly introduce — usually
by shortening something — applied to the real files, with the real gate run
against it. A mutant that SURVIVES is a hole in the guards, not a curiosity.

    python3 scripts/governance_audit_mutants.py            # run them all
    python3 scripts/governance_audit_mutants.py --list     # names only
    python3 scripts/governance_audit_mutants.py -k paused  # a subset

The working tree is restored after every mutant, including on Ctrl-C and on an
unexpected exception. It refuses to run against a dirty checkout of the files
it mutates, so an interrupted run can never be mistaken for your own edits.

This harness is a developer tool and is deliberately NOT wired into CI: it
rewrites tracked files, and it costs one full pytest run per mutant. The
properties it proves are enforced continuously by the two suites it runs —
`backend/tests/unit/test_release_gate_policy.py` (shape) and
`backend/tests/unit/test_governance_audit_behavior.py` (behaviour under a
stubbed `gh`). Re-run this harness whenever either of those changes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ".github/workflows/security-governance-audit.yml"
TESTS = ".github/workflows/test.yml"

GATE = [
    sys.executable,
    "-m",
    "pytest",
    "backend/tests/unit/test_release_gate_policy.py",
    "backend/tests/unit/test_governance_audit_behavior.py",
    "-q",
    "-x",
    "--no-cov",
    "-p",
    "no:cacheprovider",
]

MUTATED_FILES = (AUDIT, TESTS)


# The elevated step exactly as it read before this bead — no credential
# handling, no 401/403/404 branching, no explicit assertions. Substituting it
# wholesale is the strongest single mutant here: it is what the guards were
# supposed to have been detecting all along, and an earlier version of the
# suite passed against it.
NAIVE_ELEVATED_BODY = """          set -euo pipefail
          gh api "repos/$REPO/branches/main/protection" > main-protection.json
          gh api "repos/$REPO/branches/dev/protection" > dev-protection.json

          jq -e '.enforce_admins.enabled == true' main-protection.json
          gh api "repos/$REPO/vulnerability-alerts" >/dev/null
          gh api "repos/$REPO/automated-security-fixes" >/dev/null
          gh api --paginate "repos/$REPO/secret-scanning/alerts?state=open" \\
            --jq '.[]' > open-secret-alerts.txt
"""


class Mutant:
    def __init__(self, name: str, why: str, path: str, find: str, replace: str):
        self.name = name
        self.why = why
        self.path = path
        self.find = find
        self.replace = replace

    def apply(self) -> None:
        target = ROOT / self.path
        text = target.read_text(encoding="utf-8")
        count = text.count(self.find)
        if count != 1:
            raise SystemExit(
                f"mutant {self.name!r}: anchor text occurs {count} times in "
                f"{self.path}; it must occur exactly once. Update the harness."
            )
        target.write_text(text.replace(self.find, self.replace), encoding="utf-8")


def _elevated_body() -> str:
    """The elevated step's `run:` block, verbatim, for wholesale replacement."""
    text = (ROOT / AUDIT).read_text(encoding="utf-8")
    start = text.index("      - name: Verify repository controls")
    start = text.index("        run: |\n", start) + len("        run: |\n")
    end = text.index("      - name: Verify the authoritative beads branch", start)
    return text[start:end]


def mutants() -> list[Mutant]:
    items: list[Mutant] = []

    def add(name, why, find, replace, path=AUDIT):
        items.append(Mutant(name, why, path, find, replace))

    # ─── Swallowed exit status ─────────────────────────────────────────────
    alerts_call = 'gh api "repos/$REPO/vulnerability-alerts" >/dev/null 2> gh-error.txt || status=$?'
    add(
        "swallow-or-true",
        "the documented `|| true` defect class",
        alerts_call,
        alerts_call.replace("|| status=$?", "|| true"),
    )
    add(
        "swallow-or-colon",
        "`|| :` is the idiomatic spelling of `|| true` and survived the old regex",
        alerts_call,
        alerts_call.replace("|| status=$?", "|| :"),
    )
    add(
        "swallow-status-zero",
        "`|| status=0` turns a disabled-Dependabot 404 into a clean board",
        alerts_call,
        alerts_call.replace("|| status=$?", "|| status=0"),
    )
    add(
        "swallow-bin-true",
        "`|| /bin/true` is `|| true` spelled by path",
        alerts_call,
        alerts_call.replace("|| status=$?", "|| /bin/true"),
    )
    add(
        "swallow-printf",
        "`|| printf ''` swallows the status and emits nothing",
        alerts_call,
        alerts_call.replace("|| status=$?", "|| printf ''"),
    )
    add(
        "swallow-echo-empty-list",
        "the repository's own documented `|| echo '[]'` defect",
        alerts_call,
        alerts_call.replace("|| status=$?", "|| echo '[]'"),
    )
    add(
        "relax-set-e",
        "`set +e` disarms every unguarded command from that point on",
        "          status=0\n" + f"          {alerts_call}",
        "          set +e\n          status=0\n" + f"          {alerts_call}",
    )
    add(
        "drop-strict-mode",
        "`set -euo pipefail` is load-bearing and nothing used to pin it",
        "        run: |\n          set -euo pipefail\n\n          # Four outcomes",
        "        run: |\n\n          # Four outcomes",
    )

    # ─── Status-test operators ─────────────────────────────────────────────
    add(
        "status-ne-to-lt",
        "`-lt 0` is never true against an exit status, so the branch never fires",
        'gh api "repos/$REPO/vulnerability-alerts" >/dev/null 2> gh-error.txt || status=$?\n'
        '          if [[ "$status" -ne 0 ]]; then',
        'gh api "repos/$REPO/vulnerability-alerts" >/dev/null 2> gh-error.txt || status=$?\n'
        '          if [[ "$status" -lt 0 ]]; then',
    )
    add(
        "cadence-eq-to-ge",
        "`-ge 0` is always true, so the cadence always reports ARMED",
        'if [[ "$status" -eq 0 ]]; then\n            cadence="ARMED',
        'if [[ "$status" -ge 0 ]]; then\n            cadence="ARMED',
    )
    add(
        "cadence-404-to-else",
        "a bare `else` makes a rate-limited run report the schedule inert forever",
        "          elif grep -q 'HTTP 404' gh-error.txt; then\n            cadence=\"PENDING",
        '          else\n            cadence="PENDING',
    )

    # ─── The credential guard ──────────────────────────────────────────────
    add(
        "guard-default-x",
        "one character makes the emptiness guard permanently false",
        'if [[ -z "${GOVERNANCE_AUDIT_TOKEN:-}" ]]; then',
        'if [[ -z "${GOVERNANCE_AUDIT_TOKEN:-x}" ]]; then',
    )
    add(
        "guard-does-not-exit",
        "an annotation without a non-zero exit is an audit that runs unauthenticated",
        '::error::GOVERNANCE_AUDIT_TOKEN is absent or empty, so this audit cannot read any repository control and will not pretend otherwise. Provision a repository secret named GOVERNANCE_AUDIT_TOKEN holding a read-only fine-grained personal access token scoped to this repository only, granting Administration (read) and Secret scanning alerts (read). If this run came from .github/workflows/test.yml, also confirm that caller still passes the credential through its explicit one-entry secrets mapping."\n            exit 1',
        '::error::GOVERNANCE_AUDIT_TOKEN is absent or empty."',
    )
    add(
        "guard-deleted",
        "no emptiness guard at all",
        "      - name: Verify the elevated audit credential is present\n",
        "      - name: Skipped credential check\n        if: false\n",
    )

    # ─── Credential binding ────────────────────────────────────────────────
    add(
        "token-fallback-to-builtin",
        "a fallback restores the original 403 and can make an unreadable list read empty",
        "          GH_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN }}\n          REPO: ${{ github.repository }}\n        run: |\n          set -euo pipefail\n\n          # Four outcomes",
        "          GH_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN || github.token }}\n          REPO: ${{ github.repository }}\n        run: |\n          set -euo pipefail\n\n          # Four outcomes",
    )
    add(
        "beads-read-over-elevated",
        "the built-in token can serve this call; using the PAT widens its blast radius",
        '      - name: Verify the authoritative beads branch\n        env:\n          GH_TOKEN: ${{ github.token }}',
        '      - name: Verify the authoritative beads branch\n        env:\n          GH_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN }}',
    )
    add(
        "declaration-dropped",
        "an undeclared secret makes the caller mapping a hard error",
        "    secrets:\n      GOVERNANCE_AUDIT_TOKEN:\n        description: >-",
        "    secrets:\n      SOMETHING_ELSE:\n        description: >-",
    )
    add(
        "workflow-write-scope",
        "a write scope on a read-only audit is a standing privilege",
        "permissions:\n  contents: read\n\njobs:",
        "permissions:\n  contents: write\n\njobs:",
    )

    # ─── HTTP outcome branching ────────────────────────────────────────────
    add(
        "drop-401-branch",
        "an expired PAT then reads as a control violation",
        "            if grep -q 'HTTP 401' gh-error.txt; then\n              credential_error \"repos/$REPO/secret-scanning/alerts\"\n            elif grep -q 'HTTP 403'",
        "            if grep -q 'HTTP 403'",
    )
    add(
        "collapse-403-into-404",
        "a provisioning defect reported as a disabled control misroutes the fix",
        "            elif grep -q 'HTTP 403' gh-error.txt; then\n              scope_error \"repos/$REPO/vulnerability-alerts\" \"Administration\"\n",
        "",
    )
    add(
        "flatten-404-hedge",
        "GitHub answers 404 for both 'off' and 'invisible'; a flat claim misroutes",
        "Dependabot alerts read as DISABLED for this repository (HTTP 404 from repos/$REPO/vulnerability-alerts). Re-enable them under Settings, Code security. If they ARE enabled, then GOVERNANCE_AUDIT_TOKEN has lost 'Administration' (read) instead: GitHub answers this endpoint with 404 both when the control is off and when the credential cannot see it.",
        "Dependabot alerts are DISABLED for this repository. Re-enable them.",
    )
    add(
        "expiry-hint-back-on-403",
        "expiry returns 401 and never reaches the 403 branch",
        "Grant the token '$2' (read) on this repository. This is a credential-provisioning defect",
        "Grant the token '$2' (read) on this repository, or reissue it if it has expired. This is a credential-provisioning defect",
    )

    # ─── Control assertions ────────────────────────────────────────────────
    add(
        "drop-paused-assertion",
        "GitHub auto-pauses Dependabot; `enabled: true, paused: true` opens no PRs",
        "          if ! jq -e '.paused == false' automated-security-fixes.json >/dev/null; then\n",
        "          if false; then\n",
    )
    add(
        "bare-jq-enforcement",
        "a bare `jq -e` prints an unannotated `false` and leans entirely on `set -e`",
        "            if ! jq -e '.enforce_admins.enabled == true and .allow_force_pushes.enabled == false' \\\n              \"$branch-protection.json\" >/dev/null; then",
        "            jq -e '.enforce_admins.enabled == true and .allow_force_pushes.enabled == false' \"$branch-protection.json\"\n            if false; then",
    )
    add(
        "alerts-count-by-wc",
        "`wc -l` counts newlines; an unterminated final line undercounts",
        "          if [[ -s open-secret-alerts.txt ]]; then\n            echo \"::error::$(grep -c '' open-secret-alerts.txt) secret-scanning",
        '          if [[ "$(wc -l < open-secret-alerts.txt)" -ne 0 ]]; then\n            echo "::error::$(wc -l < open-secret-alerts.txt) secret-scanning',
    )
    add(
        "widen-secret-projection",
        "the plaintext `.secret` field would land unmasked in a world-readable log",
        "--jq '.[] | \"#\\(.number) \\(.secret_type) opened \\(.created_at)\"'",
        "--jq '.[]'",
    )
    add(
        "naive-elevated-body",
        "the whole pre-bead step body: no credential handling, no branching",
        _elevated_body(),
        NAIVE_ELEVATED_BODY,
    )

    # ─── Cadence honesty ───────────────────────────────────────────────────
    add(
        "cadence-claims-registration",
        "presence on the default branch is what was checked, not trigger registration",
        'cadence="ARMED (checked: this file is present on \'$branch\', which is what GitHub requires before it registers the quarterly schedule and the manual dispatch button; the cron expression itself is not validated here)"',
        'cadence="ARMED (this file is on \'$branch\', so the quarterly schedule and manual dispatch are registered)"',
    )
    add(
        "cadence-probe-loses-ref-pin",
        "without the ref pin the probe reads its own branch and reports ARMED forever",
        'gh api "repos/$REPO/contents/$AUDIT_WORKFLOW_PATH?ref=$branch"',
        'gh api "repos/$REPO/contents/$AUDIT_WORKFLOW_PATH"',
    )

    # ─── The caller side ───────────────────────────────────────────────────
    add(
        "caller-inherits-every-secret",
        "`secrets: inherit` widens the call as soon as any repository secret is added",
        "    secrets:\n      GOVERNANCE_AUDIT_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN }}",
        "    secrets: inherit",
        path=TESTS,
    )
    add(
        "caller-mapping-dropped",
        "without the mapping the audit runs on the built-in token again",
        "    secrets:\n      GOVERNANCE_AUDIT_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN }}",
        "",
        path=TESTS,
    )
    add(
        "caller-passes-an-extra-secret",
        "exactly one named credential, or the blast radius grows quietly",
        "    secrets:\n      GOVERNANCE_AUDIT_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN }}",
        "    secrets:\n      GOVERNANCE_AUDIT_TOKEN: ${{ secrets.GOVERNANCE_AUDIT_TOKEN }}\n      UNRELATED: ${{ secrets.UNRELATED }}",
        path=TESTS,
    )
    add(
        "caller-regains-security-events",
        "a scope no call uses is a standing grant",
        "    permissions:\n      contents: read\n    uses: ./.github/workflows/security-governance-audit.yml",
        "    permissions:\n      contents: read\n      security-events: read\n    uses: ./.github/workflows/security-governance-audit.yml",
        path=TESTS,
    )
    add(
        "caller-written-in-remote-form",
        "an `owner/repo/...@ref` caller used to be invisible to all three caller guards",
        "    uses: ./.github/workflows/security-governance-audit.yml\n    # Explicit single-secret mapping.",
        "    uses: MotWakorb/enhancedchannelmanager/.github/workflows/security-governance-audit.yml@dev\n    # Explicit single-secret mapping.",
        path=TESTS,
    )

    return items


def _dirty(paths: tuple[str, ...]) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--", *paths],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def _restore(paths: tuple[str, ...]) -> None:
    subprocess.run(
        ["git", "-C", str(ROOT), "checkout", "--", *paths],
        check=True,
        capture_output=True,
    )


def _gate() -> subprocess.CompletedProcess:
    return subprocess.run(GATE, cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", dest="filter", default="", help="substring of mutant name")
    parser.add_argument("--list", action="store_true", help="list mutants and exit")
    args = parser.parse_args()

    selected = [m for m in mutants() if args.filter in m.name]
    if args.list:
        for mutant in selected:
            print(f"{mutant.name:34s} {mutant.path:44s} {mutant.why}")
        return 0

    dirty = _dirty(MUTATED_FILES)
    if dirty:
        print(
            "Refusing to run: the files this harness mutates have uncommitted "
            "changes, and the harness restores them with `git checkout`.\n"
            + "\n".join(dirty),
            file=sys.stderr,
        )
        return 2

    baseline = _gate()
    if baseline.returncode != 0:
        print("Baseline gate is already red; fix that first.", file=sys.stderr)
        print(baseline.stdout[-4000:], file=sys.stderr)
        return 2
    print(f"baseline: {len(selected)} mutants to run, gate green\n")

    survivors = []
    try:
        for index, mutant in enumerate(selected, 1):
            mutant.apply()
            try:
                result = _gate()
            finally:
                _restore(MUTATED_FILES)
            killed = result.returncode != 0
            if not killed:
                survivors.append(mutant)
            print(
                f"[{index:2d}/{len(selected)}] "
                f"{'KILLED ' if killed else 'SURVIVED'}  {mutant.name:34s} "
                f"{mutant.why}"
            )
    finally:
        _restore(MUTATED_FILES)

    print()
    if survivors:
        print(f"{len(survivors)} SURVIVOR(S) — each is a hole in the guards:")
        for mutant in survivors:
            print(f"  - {mutant.name}: {mutant.why}")
        return 1
    print(f"All {len(selected)} mutants killed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
