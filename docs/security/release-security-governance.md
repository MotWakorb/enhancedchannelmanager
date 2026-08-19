# Release Security Governance

> **The automated audit is gone.** `.github/workflows/security-governance-audit.yml`
> and the `Security Governance Audit` job that invoked it on every push to
> `dev` were removed in the CI gate reduction. Nothing verifies the live
> control surfaces on a schedule any more. The cadence below is retained as
> the owner's manual checklist; the "verified evidence" it used to produce
> does not exist unless someone gathers it by hand.

**Owner:** Security Engineer persona. **Backup owner:** Project Engineer persona.
The owner checks the control surfaces below on the first day of January,
April, July, and October. A control found missing is a release blocker until
its absence is explained or the control is restored.

The checks are against the live settings rather than documentation:

- `main` requires the four test/CodeQL contexts plus `Release Cut Gate`;
- `dev` requires those same four test/CodeQL contexts. The ADR-001 image and
  Trivy gates were never made required contexts and still are not, though
  `build-amd64` and all four Trivy scans do run and do block publication;
  the DAST gate was removed outright and is no longer expected;
- administrator enforcement is on and force pushes are off on both branches;
- the remote `beads` branch exists as the current board source;
- dependency alerts and automated security updates are enabled; and
- every secret-scanning alert has been dispositioned.

The owner records what was checked, what it showed, and any settings change in
the audit bead. This supplements the committed protection snapshots; it does
not treat a historical snapshot as proof of current state.

Dependabot checks npm, backend Python, MCP Python, GitHub Actions, and both
Docker build roots weekly. Compatible minor/patch changes may be grouped;
major updates remain separate under ADR-001. All update PRs target `dev`.
A dependency bump merges on `Backend Tests`, `Frontend Tests` and CodeQL. The
image build and the four Trivy scans are not absent from the PR — `build.yml`
runs on every pull request to `dev`, and a dependency manifest is a code path
by `scripts/classify_changed_paths.py`, so `build-amd64` and all four scans do
execute there. What ADR-001 asked for and never got is for them to *block* that
merge: they are advisory on the PR, not required contexts. The DAST gate was
removed outright. On the push to `dev` after the merge the same four scans run
again and gate publication, which is where they do block.

## Bootstrap actions after this change lands

These are repository settings and are intentionally performed separately by
the PO/SRE after reviewing this PR:

1. Publish the current board export on the configured `beads` sync branch and
   keep `bd sync` in the board-change landing workflow. The release gate fails
   closed while that remote branch is absent.
2. Add `Release Cut Gate` to `main` required status checks.
3. Enable Dependabot alerts (automated security fixes are already enabled).
4. Resolve secret-scanning alert #1 as `used_in_tests`: both locations are
   synthetic Telegram-token-shape fixtures, not an issued credential. Preserve
   that rationale in the alert comment.
5. Record the manual control review in bead
   `enhancedchannelmanager-04c0u.11` as bootstrap evidence. (The
   `Security Governance Audit` workflow that used to produce this evidence
   automatically has been removed.)
