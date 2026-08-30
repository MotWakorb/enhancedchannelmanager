# Operator Workspace Final Validation

**Bead:** `enhancedchannelmanager-2896r.9`

**Validation date:** 2026-07-27

**Target build:** exact local production build from the `newui` worktree

**Browsers:** Chromium via Playwright 1.59.1

**Viewports:** 1280×720 and 1920×1080

**Accessibility engine:** `@axe-core/playwright` 4.10.2, WCAG 2 A/AA and
WCAG 2.1 A/AA tags

## Outcome

The target viewport matrix passes for every primary route. The run covers
populated content, the mapped empty/error state for each applicable route,
Channel Manager permission denial, navigation widths, the dashboard filtered
deep link, contextual Settings navigation and return, long-page state
retention, dense-toolbar actions, keyboard operation, layout containment, and
request budgets.

The first automated accessibility pass found real product defects. The final
pass has no serious or critical axe violations and uses no axe rule
exclusions. Fixes made during validation:

- Channel Manager group toggle, group help, and empty badge contrast
  (`enhancedchannelmanager-rv84z`).
- M3U Changes and Journal status/action badge contrast.
- Stats stream-name and error-state contrast.
- An accessible name for the Stats **Time Period** selector and an explicit
  label association for **Channel**.
- Keyboard activation, visible focus, current-page state, and valid
  presentation semantics for all Settings section choices.
- Theme-safe Channel Manager group treatment: empty groups no longer reduce
  all descendant text through ancestor opacity, and channel ranges no longer
  reduce otherwise-valid text contrast through local opacity.

## Reproduction

From the repository root:

```bash
npm ci
npm run docs:check
npm run test:e2e:operator-workspace-release
E2E_START_SERVER=true E2E_EXACT_BUILD=true npx playwright test \
  e2e/operator-shell.spec.ts --project=chromium \
  --grep "all primary routes have no serious automated accessibility" \
  --workers=1 --retries=0
E2E_START_SERVER=true E2E_EXACT_BUILD=true npx playwright test \
  e2e/operator-shell.spec.ts --project=chromium \
  --grep "Channel Manager group text meets AA contrast" \
  --workers=1 --retries=0
E2E_START_SERVER=true E2E_EXACT_BUILD=true npx playwright test \
  e2e/operator-shell.spec.ts --project=chromium --workers=1 --retries=0
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

The exact-build commands build the checked-out frontend and serve it from the
isolated Playwright preview server; they do not rely on a possibly stale
development server.

## Route and state matrix

| Primary route | Populated evidence | Alternate evidence | Permission |
| --- | --- | --- | --- |
| Dashboard | Six-card operational summary and filtered links | Independent source failures | N/A |
| Channel Manager | Full `.14` two-pane, edit, selection, health, drag, and action matrix | Loading, true empty, scoped error/retry | Protected panes and actions withheld on 403 |
| Guide | Guide controls and populated rows | Empty guide | N/A |
| M3U Manager | Account row, status, and primary action | Empty accounts | N/A |
| EPG Manager | Source row, freshness, and primary action | Empty sources | N/A |
| Logo Manager | Logo result and count | Empty logos | N/A |
| Channel Pipeline | Rule row, stats, compact overflow actions | Empty rules | N/A |
| M3U Changes | Populated change row and summary | Error, retained stale data, retry | Explicit denial with protected actions absent |
| Stats | Populated cards and long-page section navigation | Independent metric errors | N/A |
| Journal | Populated entry and summary | Error, retained stale data, retry | Explicit denial with purge absent |
| Settings | General settings and audited long-page sections | Save and reload failures retain edits | Admin-only destinations hidden for non-admin users |

Each route is run at both viewports. The 1280×720 pass also traverses every
route with the primary navigation collapsed. Channel Manager release capture
adds populated/empty/error, normal/edit, expanded/collapsed, selection menu,
and non-color health/artwork states at both viewports.

## Journey results

- **Dashboard filtered deep link:** **Recent M3U changes** targets
  `#m3u-changes?hours=24`; Scheduled work targets its exact Settings
  destination. Unfiltered totals remain stable after Channel Manager search
  and provider scoping.
- **Navigation:** expanded and 68-pixel collapsed modes remain contained,
  persist safely, preserve real-link behavior, canonicalize aliases, and keep
  browser history focus stable.
- **Contextual Settings:** **Channel default settings** uses a stable hash.
  Clean Edit Mode exits directly; staged changes offer **Keep Editing** and
  **Discard**, with focus and route state preserved.
- **Long pages:** direct section entry updates the hash and current-location
  state. Dirty navigation, browser history, cancel/reload failure, save
  failure, successful retry, and state cleanup are exercised.
- **Dense controls:** route-specific filter/sort controls remain reachable.
  Channel Manager selection and bulk actions are exercised. Routes without
  row selection expose no selection action bar. Channel Pipeline secondary
  actions move into a keyboard-operable overflow menu at 1280 pixels.
- **Channel Manager `.14`:** exact two-pane geometry, width recovery, populated
  and alternate states, selection/bulk menus, keyboard drag/drop, edit exit,
  non-color health cues, and deterministic release screenshots pass.

## Accessibility and manual semantic review

Automated scans include `#root` after each route reaches its deterministic
settled control. All WCAG A/AA rules, including color contrast, remain enabled.
The final artifacts report no serious or critical violations.

Channel Manager additionally runs a group-scoped axe and computed-style
contrast matrix over populated and empty group states in dark, light, and
high-contrast themes at both target viewports. The matrix requires the
applicable `.group-toggle`, `.group-subtext`, and `.group-empty-badge`
fixtures to exist and calculates WCAG relative luminance from the composited
foreground/background colors. Every asserted ratio is at least 4.5:1; no axe
rules are excluded.

Manual and assertion-backed checks:

- One `main` landmark and one exact route `h1`; named primary, section, pane,
  toolbar, and menu regions.
- **Skip to main content** is the first Tab stop, focuses `main`, and does not
  alter route or history.
- Primary links and Settings section choices work with keyboard activation and
  have visible focus.
- Forward and reverse Tab order crosses page header, Channels pane, channel
  row, Streams pane, and stream row logically.
- Escape closes menus/dialogs or cancels keyboard drag and returns focus to
  the invoking control.
- Collapsed navigation retains accessible names while hiding visual labels.
- Status is not color-only: labels, icons, text, badges, and accessible names
  remain present.
- Required controls are not unrecoverably clipped. The Stats **On this page**
  strip is an intentional named horizontal scroll owner; every item remains
  keyboard-focusable and scrolls into view.

## Geometry and request budgets

Every route asserts:

- no document or main-content horizontal overflow;
- required controls contained or owned by an intentional accessible
  horizontal scroller;
- primary task content above the fold;
- focused primary controls clear of sticky headers and the footer;
- no unjustified nested same-axis task scrollers.

The automated matrix records exact GET counts per route and viewport. An
identical GET may occur at most twice. Total route budgets are:

| Route | Maximum GETs |
| --- | ---: |
| Dashboard | 12 |
| Channel Manager | 20 |
| Guide | 12 |
| M3U Manager | 10 |
| EPG Manager | 12 |
| Logo Manager | 8 |
| Channel Pipeline | 12 |
| M3U Changes | 8 |
| Stats | 30 |
| Journal | 8 |
| Settings | 40 |

After each route settles, deterministic browser time advances through two
31-second observation windows. Request evidence is sampled at 0, 31, and 62
seconds. The storm detector groups requests by HTTP method, pathname, and
sorted query-key shape, while retaining exact full URLs and their original
budgets. The lifecycle grace boundary is after route settlement, transient
toast cleanup, axe, and geometry checks. Any request growth beyond a
route-scoped allowance in either subsequent window fails. A synthetic
regression stays fully quiet through the first checkpoint, starts a
query-changing `cursor` loop only in the second window, and proves that delayed
value churn cannot evade the detector.

The only continuing requests allowed are bounded, documented product polls:
global notifications; Channel Manager pending-merge freshness (30 seconds,
allowed only while Channel Manager is current); the four visible Stats
overview metrics (configured refresh interval, allowed only while Stats is
current); and Settings detection of externally scheduled stream probes (5
seconds, allowed only while Settings is current). Each request artifact records
the policy owner, reason, active route, whether the request is allowed there,
and leaked-owner results. A prior-route-owned poll after the next route's grace
boundary fails even when its cadence would have been valid on its owning route;
synthetic Stats-on-Dashboard and pending-merges-on-Dashboard cases lock this
behavior. Finite dependent loads are included in the initial exact-URL route
budgets and may not continue after the lifecycle grace boundary.

## Artifacts

Generated evidence is under:

- `test-results/operator-workspace-release/`: 26 unique PNG screenshots and
  paired Material Icons readiness metadata.
- `test-results/operator-workspace-final-validation/`: per-route, per-viewport
  axe JSON and request-budget/time-series JSON, plus per-viewport,
  per-theme, per-state Channel Manager contrast evidence (56 files).
- `playwright-report/`: interactive test report when enabled by the local
  reporter.

The release artifact verifier rejects missing, empty, duplicate, or
icon-unready screenshots.

## Layout-settling regression

`enhancedchannelmanager-a0ekz` traced the order-dependent 640×360 sticky-control
and 1920×1080 M3U Manager width failures to geometry being sampled before the
rendered task, web fonts, and CSS transitions had settled. The regression now
waits for deterministic task visibility, loaded fonts, stable geometry across
animation frames, and completed subtree animations before retaining the same
focus-clearance and horizontal-overflow assertions. The affected ordered prefix
passes five consecutive exact-build, single-worker, no-retry runs.

The evidence was produced from commit `21b48eee569450066c58ffe2337fe41f243ef5f3`.
The complete-suite command was:

```bash
E2E_START_SERVER=true E2E_EXACT_BUILD=true npx playwright test e2e/operator-shell.spec.ts --project=chromium --workers=1 --retries=0 --reporter=line
```

It completed twice as two separate serial, no-retry runs: run 1 passed 63/63
and run 2 passed 63/63. The affected-prefix command was run independently five
consecutive times:

```bash
E2E_START_SERVER=true E2E_EXACT_BUILD=true npx playwright test e2e/operator-shell.spec.ts --project=chromium --grep 'Channel Manager group text|every primary route preserves the audited vertical working-area budget|moves a channel drag overlay|audited sticky controls' --workers=1 --retries=0 --reporter=line
```

All five runs passed, totaling 40/40 checks. This establishes the causal class:
the assertions lacked a deterministic completed-layout boundary. The historical
failure artifacts do not identify which individual subcondition (async task
content, font readiness, or an active CSS/focus/theme transition) was decisive
in each occurrence, so no narrower subcondition claim is made. The
machine-readable run record is
[`evidence/a0ekz-layout-settling-runs.json`](evidence/a0ekz-layout-settling-runs.json).

## Sign-off

- [x] QA validation evidence complete for the target matrix.
- [x] UX semantic and interaction checklist complete.
- [ ] Product Owner confirms the implemented Channel Manager matches the
  approved Sites v16 / commit `48353d7` standard.
- [ ] Product Owner approves closing `enhancedchannelmanager-2896r.9`.

PO sign-off is intentionally not inferred from automated results.
