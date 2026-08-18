# Release Security Governance

**Owner:** Security Engineer persona. **Backup owner:** Project Engineer persona.
The owner runs and dispositions the `Security Governance Audit` workflow on
the first day of January, April, July, and October. A failed scheduled run is
a release blocker until its evidence is explained or the control is restored.

The workflow verifies the live control surfaces rather than documentation:

- `main` requires the four test/CodeQL contexts plus `Release Cut Gate`;
- `dev` requires the accepted ADR-001 image, DAST, and Trivy dependency gates
  in addition to its test/CodeQL contexts;
- administrator enforcement is on and force pushes are off on both branches;
- the remote `beads` branch exists as the current board source;
- dependency alerts and automated security updates are enabled; and
- every secret-scanning alert has been dispositioned.

The Action summary is the verified evidence for each cycle. If a live-setting
change is needed, the owner records the settings change and the successful
manual rerun URL in the audit bead. This supplements the committed protection
snapshots; it does not treat a historical snapshot as proof of current state.

Dependabot checks npm, backend Python, MCP Python, GitHub Actions, and both
Docker build roots weekly. Compatible minor/patch changes may be grouped;
major updates remain separate under ADR-001. All update PRs target `dev`, so
the accepted dependency gates run before merge.

## Bootstrap actions after this change lands

These are repository settings and are intentionally performed separately by
the PO/SRE after reviewing this PR:

1. Publish the current board export on the configured `beads` sync branch and
   keep `bd sync` in the board-change landing workflow. The release gate fails
   closed while that remote branch is absent.
2. Add `Release Cut Gate` to `main` required status checks.
3. Add `Build Docker Image (AMD64)`, `DAST Security Scan`, and
   `Container Security Scan (Trivy)` to `dev` required status checks.
4. Enable Dependabot alerts (automated security fixes are already enabled).
5. Resolve secret-scanning alert #1 as `used_in_tests`: both locations are
   synthetic Telegram-token-shape fixtures, not an issued credential. Preserve
   that rationale in the alert comment.
6. Manually run `Security Governance Audit`; link the green run in bead
   `enhancedchannelmanager-04c0u.11` as bootstrap evidence.
