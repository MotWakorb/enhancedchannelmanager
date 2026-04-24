# Release vX.Y.Z

<!--
  Release-cut PR template. Invoke via:
      gh pr create --base main --head release/vX.Y.Z --template release.md
  PR title MUST match `Release vX.Y.Z` (exact prefix). See ADR-004 for context:
      docs/adr/ADR-004-release-cut-promotion-discipline.md
  Mechanics: docs/shipping.md §Release Workflow (Cut Mechanics).
-->

<!-- Paste the promoted CHANGELOG [X.Y.Z] block here as the top section. -->

## Pre-Cut Gate Checklist

All seven gates must pass before this PR can merge. G1a, G1b, G5, G6, G7 are author/reviewer-verified at Phase 1; G2, G3, G4 are mechanically enforced. See `docs/shipping.md` §Pre-Cut Gate Checklist for full criteria.

- [ ] **G1a**: Zero open P0/P1 bugs at the cut SHA — verified via `bd list --status open --priority 0` and `bd list --status open --priority 1` (both empty, or each open item explicitly justified below).
- [ ] **G1b**: Zero open HIGH/CRITICAL security findings not formally waived — verified via `gh api repos/:owner/:repo/code-scanning/alerts --paginate | jq '[.[] | select(.state=="open" and (.rule.security_severity_level=="high" or .rule.security_severity_level=="critical"))] | length'` returns `0`. Any waived alert MUST cite alert number + dismissal category here per the G1b "formally waived" semantics in `docs/shipping.md`.
- [x] **G2**: `Backend Tests` green on the release branch (CI will verify via branch protection required check).
- [x] **G3**: `Frontend Tests` green on the release branch (CI will verify via branch protection required check).
- [x] **G4**: CodeQL delta-zero vs. `main` (CI will verify via Code Scanning merge protection rule). If a security hotfix triggers re-attribution, see ADR-004 §Hotfix Path G4 waiver — does not apply to release-cut PRs.
- [ ] **G5**: `CHANGELOG.md` `[Unreleased]` promoted to `[X.Y.Z]` with today's date and a fresh empty `[Unreleased]` section above it.
- [ ] **G6**: Version in `frontend/package.json` matches `X.Y.Z` (the release branch name `release/vX.Y.Z`).
- [ ] **G7**: No other release-cut or hotfix PR targeting `main` is open at merge time — verified via `gh pr list --base main --state open --json number,title`.

## G1a Justifications (only if G1a items remain open at cut)

<!--
  Per ADR-004, every open P0/P1 at cut SHA must be explicitly justified here.
  Format:
      - bd-xxxxx (P1, title): reason for inclusion in this cut despite open status.
  If the box above is checked because G1a is empty, leave this section blank.
-->

## G1b Waivers (only if G1b items remain open at cut)

<!--
  Per docs/shipping.md G1b "formally waived" semantics, every waived HIGH/CRITICAL
  alert must cite both (a) the GitHub Security-tab dismissal with rationale AND
  (b) the alert number + dismissal category here. Mirrors ADR-005 dismiss-with-comment policy.
  Format:
      - Alert #NNNN (rule id, severity): dismissed as <category> on <date> by <user>; rationale: <one-line summary>.
      Categories per ADR-005 §Dismiss-With-Comment Policy: false-positive (with evidence) | test-only sink.
      "Won't fix" is NOT a Phase 1 dismissal category — see ADR-005 item 3.
-->

## Hotfix-Coexistence Note (G7)

<!--
  If a hotfix PR is open simultaneously, hotfix has priority per ADR-004 §4.
  This release-cut PR rebases on the merged hotfix and re-runs the gate.
-->

## Post-Merge Tasks (reminder, not gates)

After this PR merges:

- [ ] Tag and create GitHub Release: `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z && gh release create vX.Y.Z --target main --title "vX.Y.Z" --notes-file <(gh pr view <PR_NUM> --json body -q .body)`
- [ ] Back-merge `release/vX.Y.Z` → `dev` (catches CHANGELOG + version bump): `git checkout dev && git pull && git merge release/vX.Y.Z --no-edit && git push origin dev`
- [ ] Delete the release branch: `git branch -d release/vX.Y.Z && git push origin --delete release/vX.Y.Z`
- [ ] Bump `dev`'s `frontend/package.json` to next build-numbered version (`X.Y.(Z+1)-0000` or next planned minor's `-0000`) per `shipping.md` §Cut Mechanics step 10.
- [ ] If this is the first release cut under ADR-004, file a retro bead capturing what worked, what was unclear, and any gate-item refinements.

## References

- ADR-004: `docs/adr/ADR-004-release-cut-promotion-discipline.md`
- ADR-005 (delta-zero gate that powers G4): `docs/adr/ADR-005-code-security-gating-strategy.md`
- Cut mechanics: `docs/shipping.md` §Release Workflow
- Rollback runbook (if this cut is later retracted): `docs/runbooks/v0.16.0-rollback.md`
