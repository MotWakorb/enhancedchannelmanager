# Release Security Control Audit — 2026-08-18

Scope: read-only live GitHub settings audit for
`enhancedchannelmanager-04c0u.11`. Evidence was collected through GitHub's API
on 2026-08-18 UTC; no repository settings or alerts were changed.

| Control | Live result | Required follow-up |
|---|---|---|
| `main` required checks | Backend, Frontend, both CodeQL checks; `Release Cut Gate` absent | Add `Release Cut Gate` |
| `dev` required checks | Eight existing checks; ADR-001 DAST/Trivy/image checks absent | Add the three documented dependency checks |
| Admin enforcement / force push | enforced / disabled on both branches | None |
| Dependabot alerts | API returned `403: Dependabot alerts are disabled` | Enable alerts |
| Automated security fixes | API returned 200 | None |
| Secret scanning | Enabled with push protection; one open alert | Disposition alert #1 as test-only |
| Open HIGH/CRITICAL CodeQL | Zero (100 total open alerts, none HIGH/CRITICAL) | Continue quarterly dismissal audit |
| Remote authoritative board | Configured sync branch is `beads`; no remote branch exists | Publish and maintain the board branch |
| Audit cadence | Last committed snapshot/audit evidence was 2026-04-25 | Quarterly workflow restores durable cadence |

Secret alert #1 points only to
`SettingsTab.notificationRedaction.test.tsx:122` and
`test_9ej7f_settings_secret_redaction.py:48` at commit `cc3203fc`. Both values
are explicitly assembled synthetic fixtures used to test redaction. Risk is
Informational (0.0): no issued token exists, so the correct disposition is
`used_in_tests`, with both paths cited in the resolution comment.
