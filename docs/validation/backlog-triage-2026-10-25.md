# Backlog triage — 2026-10-25

## Outcome and boundaries

Reviewed the active Beads backlog and the repository's open GitHub issues. Created
14 missing beads and reused `enhancedchannelmanager-8hatb` for GitHub #1026.
Every one of the 15 open GitHub issues now has one canonical external-reference
bead, acceptance criteria, priority, and a `github-first` label.

This is backlog triage, not implementation, runtime acceptance, a release, or
authorization to relax a security boundary. No GitHub issues were closed or
edited, no PRs were merged, and no production operations were performed. The
original checkout's uncommitted application work was left untouched; this report
was prepared in an isolated worktree based on `origin/dev` at `0bc12600`.

## Sources and counting method

- Active tracker: Beads 1.1.0, embedded Dolt, pulled from its configured remote
  before writes. The pull did not change the starting 133 non-closed records.
- Examined all 3,530 pre-intake records for existing GitHub links and semantic
  matches, including closed records; reviewed the 133 non-closed records for
  scheduling importance. Only active records are included in the inventory.
- GitHub: all 15 open issues, including bodies, labels and comments. Independently
  checked GitHub GraphQL `issues(states: OPEN).totalCount = 15`.
- Reviewed current PR states and descriptions to distinguish contribution review,
  merged implementation, residual scope and verification. PR authors' test claims
  are historical receipts, not fresh runtime evidence from this triage.
- Counts include `open`, `in_progress`, `blocked`, and `deferred`; exclude `closed`.
  Aggregate list snapshots were cross-checked with separate `bd count` calls for
  each stored status. A stored blocked status is not the same as an active
  dependency edge; the inventory also identifies blocking dependencies.
- Legacy `.beads/issues.jsonl` and SQLite records have different, older states.
  They were not imported, merged or treated as the active board. Dolt is the
  source of truth for this report.

## Scheduling policy

1. Preserve P1 security/release gates and existing blocked/deferred decisions.
   A GitHub link does not make cosmetic work outrank a security or playback risk.
2. Prefer GitHub-linked work within the same impact class. Existing linked work
   gets review/reuse before a replacement implementation: #1026 maps to `8hatb`,
   and open PR #1027 should be evaluated first in the P2 contribution-review lane.
3. Address reported viewer interruption, service starvation and broken MCP
   execution before measurement accuracy or new probe features.
4. Separate implementation from validation/closure. A merged PR does not prove
   the deployed artifact is fixed, and an open GitHub issue does not necessarily
   mean the fix needs writing again.
5. Work leaf deliverables, not portfolio totals. Epics and delivery coordinators
   overlap their children and must not be counted as independent feature effort.

Priority meanings: **P0** active emergency; **P1** high-impact interruption or
security/release gate; **P2** meaningful capability, correctness or safety work;
**P3** lower-urgency improvement, internal quality, polish or scoped investigation;
**P4** optional/speculative work. Importance below is a separate completion-order
assessment, not a claim of fresh exploitability or reproduced customer impact.

## Recommended execution order

| Order / lane | Work | Why / next action |
|---|---|---|
| P1 security gates — preserve | `97j9u`, `l5z6r` | Both remain blocked; resolve their documented vendor/native-image delivery constraints without waivers. |
| 1 — GitHub P1 | #1032 → `0m06f.6.2` | Protect active viewers from probes exceeding provider connection limits; cover every entry point and admission race. |
| 2 — GitHub P1 | #1031 → `rtst2.11` | Keep the UI/API/health checks responsive during pipeline evaluation. |
| 3 — GitHub P1 | #1033 → `rtst2.12` | Restore authorized MCP prepare/commit execution without weakening principal separation or content binding. |
| Same P1 lane — existing exposure | `5e3r3`, `ej4um`, `71imf` | Newly raised from P2 on recorded credential-disclosure and profile-overexposure evidence. Keep these ahead of P2 work; do not confuse them with previously accepted diagnostic findings. |
| First P2 contribution review | #1026 → existing `8hatb` | Evaluate open PR #1027 before replacement work; it covers routing/filtering only, not the generic JSON webhook. Check related `j28qs` Telegram delivery behavior. |
| Next GitHub P2 correctness | #1028 → `0m06f.6.3`; #1029 → `0m06f.6.4`; #1018 → `rtst2.13` | Correct stored FPS, burst-biased bitrate, and preview/live pending-merge parity. |
| Next GitHub P2 capability | #1034 → `0m06f.6.5` | Review #1035 first, then stacked #1036. Their new probe safeguards do not close #1032. |
| Parallel verification lane | #1015, #1013, #1011, #1005, #1007 | Revalidate merged fixes and accepted scope; avoid writing duplicate implementations or premature closure. |
| Next non-GitHub delivery | In-flight security, restore, YAML, SBOM and safe-error work | Finish review/integration and the stated validation obligations before starting additional unrelated branches. |
| Later | Lower-importance internal work, visual polish, speculative upgrades and mobile scope decisions | Do not let the large P2 bucket make these appear equally urgent as playback/data-safety defects. |

All short bead IDs in this report have prefix `enhancedchannelmanager-`.

## GitHub reconciliation

| GitHub | Canonical bead | Priority / type | Disposition |
|---|---|---|---|
| [#1032](https://github.com/MotWakorb/enhancedchannelmanager/issues/1032) | `0m06f.6.2` | P1 / bug | Highest GitHub implementation priority: reported probes interrupt active viewers. PR #1035 narrows the race only for its new task and explicitly does not resolve this issue. |
| [#1031](https://github.com/MotWakorb/enhancedchannelmanager/issues/1031) | `rtst2.11` | P1 / bug | P1 because the reported run makes the entire service unresponsive for its duration. Existing closed event-loop work is related precedent, not a bead covering this pipeline regression. |
| [#1033](https://github.com/MotWakorb/enhancedchannelmanager/issues/1033) | `rtst2.12` | P1 / bug | P1: the entire MCP-driven execution workflow is reported unusable. The suggested resolver change is a hypothesis to verify, not permission to bypass authentication. |
| [#1026](https://github.com/MotWakorb/enhancedchannelmanager/issues/1026) | `8hatb` | P2 / task | Existing bead reused and prioritized. PR #1027 covers Part 1 only; generic webhook remains open scope. |
| [#1028](https://github.com/MotWakorb/enhancedchannelmanager/issues/1028) | `0m06f.6.3` | P2 / bug | P2, GitHub-first: incorrect persisted FPS can select a worse primary stream and hide low-FPS failures. |
| [#1029](https://github.com/MotWakorb/enhancedchannelmanager/issues/1029) | `0m06f.6.4` | P2 / bug | P2, GitHub-first: the report quantifies ranking bias, but its supplied captures are historical evidence, not fresh live validation. |
| [#1018](https://github.com/MotWakorb/enhancedchannelmanager/issues/1018) | `rtst2.13` | P2 / task | P2, GitHub-first: explicitly excluded from merged PR #1020/#1016; it is still implementation work, not covered by the daily-rollover fix. |
| [#1034](https://github.com/MotWakorb/enhancedchannelmanager/issues/1034) | `0m06f.6.5` | P2 / feature | P2, GitHub-first after core probe-safety and measurement bugs. Existing external PRs #1035/#1036 are OPEN, not shipped; reuse them rather than reimplementing. |
| [#1015](https://github.com/MotWakorb/enhancedchannelmanager/issues/1015) | `rtst2.14` | P2 / task | Verification/reconciliation only unless revalidation finds a regression. GitHub PR #1020 merged 2026-09-20 and explicitly closes the daily-rollover scope; queue lifecycle and #1018 were excluded. No GitHub closure or live validation performed by this intake. |
| [#1013](https://github.com/MotWakorb/enhancedchannelmanager/issues/1013) | `rtst2.15` | P2 / task | Verification/closure candidate: PR #1019 and original #1014 are MERGED, but GitHub #1013 remains OPEN. No fresh runtime or deployment verification claimed. |
| [#1011](https://github.com/MotWakorb/enhancedchannelmanager/issues/1011) | `rtst2.16` | P2 / task | Verification/scope reconciliation, not a new implementation bug: PR #1012 MERGED 2026-09-20. Planned-mode limitation is intentional in that PR and must not be reported as fixed end to end. |
| [#1005](https://github.com/MotWakorb/enhancedchannelmanager/issues/1005) | `rtst2.17` | P2 / task | Verification/closure candidate: PR #1006 MERGED 2026-09-20. No reimplementation or GitHub closure requested during intake. |
| [#1025](https://github.com/MotWakorb/enhancedchannelmanager/issues/1025) | `8gmk8.5` | P3 / bug | P3 pending support/scope decision, not P1 solely from the issue-template severity. Keep reported defect visible without silently overriding the existing mobile exclusion. |
| [#1022](https://github.com/MotWakorb/enhancedchannelmanager/issues/1022) | `0m06f.6.6` | P3 / feature | P3, lower urgency than playback, automation and data-safety failures; GitHub-first within the polish/capability lane. |
| [#1007](https://github.com/MotWakorb/enhancedchannelmanager/issues/1007) | `rtst2.18` | P3 / task | P3 verification/product-decision item: PR #1008 MERGED 2026-09-20; it explicitly does not relax curated-master support. |

### Scope traps to retain

- **#1026:** PR #1027 is open and says it covers Part 1 only, despite its closing
  keyword. A generic JSON webhook still needs delivery or an explicit follow-up
  disposition. The original `8hatb` email-only baseline predates ntfy support;
  its appended intake note corrects that premise without erasing history.
- **#1034:** #1035 and #1036 are open, stacked contributions, not shipped work.
  Review the probe/task foundation before the Smart Sort classifier. Their
  account checks are not a universal solution to #1032.
- **#1015:** #1020/#1016 merged for daily-rollover matching, candidate filtering
  and live-run diagnostics. Dry-run parity is #1018. Sticky dismissal and queue
  expiry/orphan lifecycle were excluded and still need explicit disposition.
- **#1011:** #1012 merged for same-run-created channels on the direct execution
  path. Planned mode intentionally retains an explicit no-match failure; do not
  claim the planned path performs Pass 5 regeneration before commit.
- **#1007:** #1008 merged wording/docs only. Curated-master ownership relaxation
  remains a product decision, not an implementation commitment.
- **#1025:** prior PO guidance recorded in `3ie14` excludes mobile and specifies a
  1280×720 floor. Retain the reported iPad defect, but resolve supported-platform
  scope before committing to touch/mobile implementation.

## Complete non-closed inventory

### Totals after triage

| Priority | Bug | Task | Feature | Epic | Total |
|---|---:|---:|---:|---:|---:|
| P0 | 0 | 0 | 0 | 0 | 0 |
| P1 | 7 | 1 | 0 | 0 | 8 |
| P2 | 16 | 58 | 1 | 22 | 97 |
| P3 | 2 | 37 | 1 | 2 | 42 |
| P4 | 0 | 0 | 0 | 0 | 0 |
| **Total** | **25** | **96** | **2** | **24** | **147** |

Stored statuses: **open: 119**; **in_progress: 11**; **blocked: 15**; **deferred: 2**.

**120 leaf records + 27 containers** (24 epics and three task-typed umbrellas: `gflo5`, `71a2o`, `cg0og`). Leaf counts still include shared deliveries and verification records; they are not 120 independent implementation commitments.

Lane labels are persisted as `triage-lane-s` (security/release), `triage-lane-w` (workflow/data integrity), `triage-lane-u` (capability), `triage-lane-q` (quality/polish), `triage-lane-c` (container). A lane is a scheduling classification, not a CVSS rating. **Critical** means immediate triage/highest completion importance for the recorded impact, not a declaration of a P0 outage.

### Priority changes applied

| Bead | Change | Rationale |
|---|---|---|
| `5e3r3` | P2 → P1 | Documented credential sentinel reaches console/ring/capture logs; separate repair. |
| `71imf` | P2 → P1 | Silent excess profile membership and false count; held candidate needs intent/readback proof. |
| `ej4um` | P2 → P1 | Documented terminal HTTP poll exposes upstream key; preserve write accounting. |
| `1vm5r` | P3 → P2 | Unlock implemented backup-retention controls in UI; not merely changelog polish. |
| `k9e4y` | P3 → P2 | UI half of 0r0w7 shared-warning delivery; backend already exists; count once. |
| `0y596` | P2 → P3 | Only section-rail label stale; stable anchor already repaired. |
| `3vjk2` | P2 → P3 | Base-selector refactor; route-wide visual measurement prerequisite remains. |
| `4ba6m` | P2 → P3 | Optional major assessments; preserve isolated major PRs and actual need. |
| `63yri` | P2 → P3 | Changelog bead traceability only; no shipped behavior defect. |
| `9vyuc` | P2 → P3 | Trivial singular/plural copy; schedule after functional defects. |
| `gezpf` | P2 → P3 | Four-pixel metric-role mismatch; measure shared consumers before changing. |
| `ke9lo` | P2 → P3 | Coordinate source-citation cleanup with vgbwm without losing distinct requirements. |
| `lngo5` | P2 → P3 | Description says P3; selection state unreachable from shipping callers. |
| `nczsy` | P2 → P3 | External tooling ownership/cwd-prefix diagnosis; no digits-only or history rewrite fix. |
| `o742t` | P2 → P3 | Cosmetic count beside explicit failure/rollback, not silent corruption. |
| `otahr` | P2 → P3 | Developer-reference correctness; screenshot work owned by existing capture beads. |
| `vgbwm` | P2 → P3 | Guide policy cleanup; retain approved automation/rescue/operator exceptions. |
| `wd2av` | P2 → P3 | Both verification procedures work; simplify while preserving external-upload safety. |
| `wiezs` | P2 → P3 | Optional SDK2 migration, distinct from already-shipped SDK1 security repair. |
| `wwhhl` | P2 → P3 | Decorative exemptions ratified; remaining deterministic guard seeding is internal. |
| `y6j4g` | P2 → P3 | Development Alembic cap; real-schema autogenerate proof before lifting. |

No security finding was downgraded. The three existing P1 promotions reflect separately recorded credential disclosures/profile exposure, not the previously accepted diagnostic findings. All original status/deferral/assignee/parent/blocking decisions were retained.

### Per-record inventory

Importance: **Critical** = highest-impact gate/interruption/exposure; **High** = security/release protection or core data-safety obligation; **Medium** = meaningful workflow/capability; **Low** = polish/internal quality; **Container** = aggregate only. Priorities shown are stored priorities after this triage. Original types are preserved, including defect-shaped tasks.

#### P1 — 8 records

| Bead | Type | Status | Importance / lane | Remaining work / disposition |
|---|---|---|---|---|
| `0m06f.6.2` | bug | open | Critical / W | GitHub #1032: Highest GitHub implementation priority: reported probes interrupt active viewers. PR #1035 narrows the race only for its new task and explicitly does not resolve this issue. |
| `rtst2.11` | bug | open | Critical / W | GitHub #1031: P1 because the reported run makes the entire service unresponsive for its duration. Existing closed event-loop work is related precedent, not a bead covering this pipeline regression. |
| `rtst2.12` | bug | open | Critical / W | GitHub #1033: P1: the entire MCP-driven execution workflow is reported unusable. The suggested resolver change is a hypothesis to verify, not permission to bypass authentication. |
| `5e3r3` | bug | open | Critical / S | Documented credential sentinel reaches console/ring/capture logs; separate repair. |
| `97j9u` | bug | blocked | Critical / S | PR1040; vendor-authenticated ARM CA cohort unavailable; no trust workaround. |
| `ej4um` | bug | open | Critical / S | Documented terminal HTTP poll exposes upstream key; preserve write accounting. |
| `l5z6r` | bug | blocked | Critical / S | PR1040; ARM Brotli floors unavailable; retain vendor-backed path. |
| `71imf` | task | open | Critical / W | Silent excess profile membership and false count; held candidate needs intent/readback proof. |

#### P2 — 97 records

| Bead | Type | Status | Importance / lane | Remaining work / disposition |
|---|---|---|---|---|
| `0m06f.6.5` | feature | open | Medium / U | GitHub #1034: P2, GitHub-first after core probe-safety and measurement bugs. Existing external PRs #1035/#1036 are OPEN, not shipped; reuse them rather than reimplementing. |
| `0m06f.6.3` | bug | open | Medium / W | GitHub #1028: P2, GitHub-first: incorrect persisted FPS can select a worse primary stream and hide low-FPS failures. |
| `0m06f.6.4` | bug | open | Medium / W | GitHub #1029: P2, GitHub-first: the report quantifies ranking bias, but its supplied captures are historical evidence, not fresh live validation. |
| `8hatb` | task | open | Medium / W | GitHub #1026: Existing GH1026 match; review PR1027 first; generic webhook remains separate scope. |
| `rtst2.13` | task | open | Medium / W | GitHub #1018: P2, GitHub-first: explicitly excluded from merged PR #1020/#1016; it is still implementation work, not covered by the daily-rollover fix. |
| `rtst2.14` | task | open | Medium / W | GitHub #1015: Verification/reconciliation only unless revalidation finds a regression. GitHub PR #1020 merged 2026-09-20 and explicitly closes the daily-rollover scope; queue lifecycle and #1018 were excluded. No GitHub closure or live validation performed by this intake. |
| `rtst2.15` | task | open | Medium / W | GitHub #1013: Verification/closure candidate: PR #1019 and original #1014 are MERGED, but GitHub #1013 remains OPEN. No fresh runtime or deployment verification claimed. |
| `rtst2.16` | task | open | Medium / W | GitHub #1011: Verification/scope reconciliation, not a new implementation bug: PR #1012 MERGED 2026-09-20. Planned-mode limitation is intentional in that PR and must not be reported as fixed end to end. |
| `rtst2.17` | task | open | Medium / W | GitHub #1005: Verification/closure candidate: PR #1006 MERGED 2026-09-20. No reimplementation or GitHub closure requested during intake. |
| `4ysp0` | task | open | High / Q | Keep P2: copy polish overlaps persistence discoverability/data-loss advisory scope. |
| `jwtl2` | task | open | Medium / Q | Keep P2: broader theme/contrast/accessibility scope exceeds accepted G19 slice. |
| `lw04j` | task | open | Medium / Q | Keep P2: undefined tokens have hidden controls; canonical theme lint beyond G38. |
| `04c0u.12` | task | open | High / S | PR894 partial delivery; auth banner, disposable DR and confirmation proof remain. |
| `04c0u.8` | bug | open | High / S | PR889 merged; exact-tip proof and dedicated-volume adoption, not reimplementation. |
| `0m06f.3.3` | bug | in_progress | High / S | PR1045 binding candidate; protected delivery pending; local-write prerequisite. |
| `10tln` | bug | in_progress | High / S | PR1046 composition budgets; authenticated exhaustion hardening. |
| `293n8` | task | blocked | High / S | PR1043 design only; PO storage/ownership/retention/recovery decisions needed. |
| `5oa58` | task | in_progress | High / S | PR1042; parser candidate exists; distinguish inherited CA/build failures. |
| `6ayl0` | bug | in_progress | High / S | PR1043 assessment; no demonstrated local encoder path or fork authorization. |
| `9sc07` | bug | blocked | High / S | Musl candidate exists; unscored advisory is not a waiver. |
| `d7c7f` | bug | blocked | High / S | Installer-removal candidate exists; aggregate PR1040 delivery pending. |
| `fykpz` | task | in_progress | High / S | SDK/multidict fixes shipped; reconcile remaining image findings with owners. |
| `g4pnh` | task | open | High / S | Upgrade-only capability-gate incompatibility; current pin fails safely. |
| `h1186` | bug | blocked | High / S | Mako candidate exists; retain platform-applicability distinction. |
| `jnw1v` | bug | in_progress | High / S | PR1047; retain prior nonblocking acceptance and CodeQL1877 obligation. |
| `oaoel` | bug | in_progress | High / S | PR1047; retain prior nonblocking acceptance and CodeQL1812 obligation. |
| `uzt5e` | task | open | High / S | Define unsupported SBOM grammar policy; no demonstrated publication bypass. |
| `w8vei` | task | open | High / S | Verify scanner false positive before narrow disposition; security check policy. |
| `wfz8z` | task | blocked | High / S | Reconcile existing immutable-image review receipts before repeating OCR audit. |
| `wgf0b` | bug | in_progress | High / S | PR1044 candidate; development-tool exposure, not shipped-runtime exposure. |
| `ws5q0` | task | open | High / S | Reconcile automation policy; do not re-enable intentionally disabled Dependabot PRs. |
| `ydbiv` | bug | blocked | High / S | PR1041; inherited image gates and live publication proof remain. |
| `0m06f.6.1` | bug | deferred | Medium / U | Explicitly deferred cold Expand All gap; preserve lazy-loading boundaries. |
| `0m06f.8.4` | bug | deferred | Medium / U | Explicitly deferred MCP all-groups gap; group-ID workaround remains. |
| `0r0w7` | task | open | Medium / U | Docs half of k9e4y shared-warning delivery; keep endpoint/MCP tool. |
| `1vm5r` | task | open | Medium / U | Unlock implemented backup-retention controls in UI; not merely changelog polish. |
| `d2kdg` | task | open | Medium / U | Expose existing journal batch-filter capability in the UI. |
| `hne7k` | task | blocked | Medium / U | Upstream refresh identity/completion contract required; research is not implementation. |
| `k9e4y` | task | open | Medium / U | UI half of 0r0w7 shared-warning delivery; backend already exists; count once. |
| `ne0gf` | task | blocked | Medium / U | Upstream blocked; config-only disabled/untrusted upsert, no plugin installation workaround. |
| `ue130` | task | open | Medium / U | Settled policy: four Settings sections always render empty states; no policy reopening. |
| `xkk54` | task | open | Medium / U | Tool-catalog heading/anchor; preserve curated-summary policy, verify current counts. |
| `0by9g` | task | open | High / W | Journal omitted starts honestly; shared residual with oypsc; retain PO deferral. |
| `381gu` | task | open | Medium / W | Fix false empty state and shared predicate; actual prerequisite for 6qvts. |
| `3p7jg` | task | open | Medium / W | Refresh/cache visibility gap; historical 25-minute duration not fully attributed. |
| `62ja6` | task | open | High / W | Staged revert decision supersedes earlier split proposal; retain PO sequencing. |
| `63lik` | task | open | Medium / W | Fresh-install tutorial no-ops; do not silently change Built-in/Custom seed policy. |
| `6gwju` | task | open | Medium / W | Interpreter override/health mismatch can block MCP startup; container proof needed. |
| `6qvts` | task | open | Medium / W | Apply settled custom-account filtering after/with 381gu; retain name-key tradeoff. |
| `75j5q` | task | open | High / W | Show degraded backup categories while retaining cloud failure severity. |
| `7iblv` | task | open | Medium / W | QNAP persistence-first recovery; share generic docs with mevqr, retain field commands. |
| `bv8n7` | task | open | Medium / W | Real list/menu disruption; retain PO channel-editing theme deferral. |
| `bvic4` | task | open | High / W | False persistence reassurance risks data loss; needs integrated container proof. |
| `du3pj` | task | open | Medium / W | History/details still confuse predicted versus realized mutations. |
| `ftidn` | task | open | High / W | Channels slice shipped; remaining journal residue follows settled policy; PO-deferred. |
| `gk4d0` | task | open | Medium / W | Coordinate onboarding screenshot capture with qguc7; approved stacked wordmark. |
| `h2noa` | task | open | Medium / W | Fixture-first candidate-generation diagnosis; no synthetic-only matcher broadening. |
| `hmr0e` | task | open | Medium / W | Quantity-bearing refresh completion; historical prerequisite beads already closed. |
| `j28qs` | task | open | Medium / W | Ordinary names can silently lose Telegram alerts; verify PR1027 overlap first. |
| `jjjnh` | task | open | High / W | Server-side stale renumber safety; held candidate is not accepted CAS; PO-deferred. |
| `l5plc` | task | open | Medium / W | Indicator versus working-as-designed decision; null-URL premise superseded. |
| `mevqr` | task | open | Medium / W | Recovery/startup contract; held draft needs current command/artifact validation. |
| `nz8q4` | task | open | Medium / W | CONFIG_DIR ownership/TLS mismatch; held candidate scope needs reconciliation. |
| `oypsc` | task | open | High / W | Ordering shipped in PR928; omitted-start residual shares 0by9g delivery. |
| `qguc7` | task | open | Medium / W | Screenshot subset of gk4d0 plus distinct Event Sync prose; one coordinated capture. |
| `qq0na` | task | open | Medium / W | Remaining navigation/developer targets; retain main-first/dev-fallback policy. |
| `rtst2.9` | bug | blocked | Medium / W | GH972 implementation merged; reporter build/deployment confirmation remains. |
| `t683u` | task | blocked | Medium / W | Blocked payload-contract survey before allowlisting; not a proven auth bypass. |
| `v3wr1` | task | open | High / W | Collision/rename staging; held candidate needs submit fence and gesture/readback proof. |
| `v7d37` | task | open | Medium / W | Acceptance-first XC guide/channel links and blank-source recovery; no new stripping proof. |
| `v7n0d` | task | open | Medium / W | Wrong inherited version identity; held runtime candidate needs integrated verification. |
| `ybr3u` | task | open | Medium / W | Required-field repair shipped; optional-sibling contract still needs clarification. |
| `ys384` | task | open | Medium / W | In-product connection setup guidance still wrong; documentation half delivered. |
| `04c0u` | epic | open | Container / C | Security hardening: ECM and ECM MCP; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f` | epic | open | Container / C | ECM user-value portfolio; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.1` | epic | open | Container / C | Protect operators and their secrets; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.2` | epic | open | Container / C | Deliver reliable ECM releases; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.3` | epic | open | Container / C | Preserve and recover operator data; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.4` | epic | open | Container / C | Replicate ECM instances faithfully; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.5` | epic | open | Container / C | Automate channel operations predictably; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.6` | epic | open | Container / C | Build accurate and manageable lineups; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.7` | epic | open | Container / C | Keep operators informed; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.8` | epic | open | Container / C | Make ECM efficient and accessible to operate; aggregate only, complete/verify children rather than reimplementing. |
| `0m06f.9` | epic | open | Container / C | Provide trustworthy guidance and onboarding; aggregate only, complete/verify children rather than reimplementing. |
| `1a0i5` | epic | open | Container / C | EPG matching and assignment; aggregate only, complete/verify children rather than reimplementing. |
| `2896r` | epic | open | Container / C | Reorganize ECM into a cohesive operator workspace; aggregate only, complete/verify children rather than reimplementing. |
| `70u0r` | epic | open | Container / C | Settings IA: group the 18-destination flat list and relocate what does not belong; aggregate only, complete/verify children rather than reimplementing. |
| `71a2o` | task | blocked | Container / C | Deliver the nine selected P2 hardening and reliability items; aggregate only, complete/verify children rather than reimplementing. Task-typed coordinator; do not double-count. |
| `f5a5j` | epic | open | Container / C | Cross-instance sync produces a structurally correct replica that has lost its guide data and its branding; aggregate only, complete/verify children rather than reimplementing. |
| `gflo5` | task | open | Container / C | [EPIC] Dependency upgrade blockers; aggregate only, complete/verify children rather than reimplementing. Task-typed coordinator; do not double-count. |
| `l3und` | epic | open | Container / C | CSS and design system; aggregate only, complete/verify children rather than reimplementing. |
| `lqfds` | epic | open | Container / C | DBAS backup and restore hardening; aggregate only, complete/verify children rather than reimplementing. |
| `nq7cl` | epic | open | Container / C | M3U Manager; aggregate only, complete/verify children rather than reimplementing. |
| `ojnxm` | epic | open | Container / C | Notifications and alerting; aggregate only, complete/verify children rather than reimplementing. |
| `oztt9` | epic | open | Container / C | Container runtime and entrypoint; aggregate only, complete/verify children rather than reimplementing. |
| `rtst2` | epic | open | Container / C | Channel Pipeline and Event Sync; aggregate only, complete/verify children rather than reimplementing. |
| `z5qgx` | epic | open | Container / C | Operator documentation correctness; aggregate only, complete/verify children rather than reimplementing. |

#### P3 — 42 records

| Bead | Type | Status | Importance / lane | Remaining work / disposition |
|---|---|---|---|---|
| `0m06f.6.6` | feature | open | Medium / U | GitHub #1022: P3, lower urgency than playback, automation and data-safety failures; GitHub-first within the polish/capability lane. |
| `8gmk8.5` | bug | open | Medium / W | GitHub #1025: P3 pending support/scope decision, not P1 solely from the issue-template severity. Keep reported defect visible without silently overriding the existing mobile exclusion. |
| `rtst2.18` | task | open | Medium / W | GitHub #1007: P3 verification/product-decision item: PR #1008 MERGED 2026-09-20; it explicitly does not relax curated-master support. |
| `0la8g` | task | open | Low / Q | Debug-bundle/analyzer shape mismatch; direct analyze-body path unaffected. |
| `0y596` | task | open | Low / Q | Only section-rail label stale; stable anchor already repaired. |
| `2vv9i` | task | open | Low / Q | Measure actual table-width geometry; horizontal overflow already exists. |
| `3gzsl` | task | open | Low / Q | Comment/test placement only; preserve formatter credential regression assertions. |
| `3ie14` | task | open | Low / Q | Reflow conformance decision first; no implicit mobile-support expansion. |
| `3vjk2` | task | open | Low / Q | Base-selector refactor; route-wide visual measurement prerequisite remains. |
| `4ba6m` | task | open | Low / Q | Optional major assessments; preserve isolated major PRs and actual need. |
| `5imvc` | task | open | Low / Q | Bounded disposable fault-injection enabler; smoke-test pass-through and injected failures. |
| `63yri` | task | open | Low / Q | Changelog bead traceability only; no shipped behavior defect. |
| `70u0r.5` | task | open | Low / Q | Notification naming/cross-reference polish; independent of Settings IA. |
| `9vyuc` | task | open | Low / Q | Trivial singular/plural copy; schedule after functional defects. |
| `bkhf7` | bug | open | Low / Q | Lifecycle/teardown attribution is pipeline execution, not unrelated Toast tests. |
| `gezpf` | task | open | Low / Q | Four-pixel metric-role mismatch; measure shared consumers before changing. |
| `j33xq` | task | open | Low / Q | Existing typing-import lint finding; runtime unaffected. |
| `ke9lo` | task | open | Low / Q | Coordinate source-citation cleanup with vgbwm without losing distinct requirements. |
| `lngo5` | task | open | Low / Q | Description says P3; selection state unreachable from shipping callers. |
| `nczsy` | task | open | Low / Q | External tooling ownership/cwd-prefix diagnosis; no digits-only or history rewrite fix. |
| `o742t` | task | open | Low / Q | Cosmetic count beside explicit failure/rollback, not silent corruption. |
| `otahr` | task | open | Low / Q | Developer-reference correctness; screenshot work owned by existing capture beads. |
| `rid8w` | task | open | Low / Q | Load-sensitive keyboard test; bounded-worker rerun passed without weaker assertions. |
| `rtst2.2` | task | open | Low / Q | Missing analyzer finding guidance and documentation drift prevention. |
| `vgbwm` | task | open | Low / Q | Guide policy cleanup; retain approved automation/rescue/operator exceptions. |
| `wd2av` | task | open | Low / Q | Both verification procedures work; simplify while preserving external-upload safety. |
| `wiezs` | task | open | Low / Q | Optional SDK2 migration, distinct from already-shipped SDK1 security repair. |
| `wwhhl` | task | open | Low / Q | Decorative exemptions ratified; remaining deterministic guard seeding is internal. |
| `y5hvo` | task | open | Low / Q | Actual no-op settings writes/counters; distinct from zcnl5 stream reuse. |
| `y6j4g` | task | open | Low / Q | Development Alembic cap; real-schema autogenerate proof before lifting. |
| `ybzmh` | task | open | Low / Q | GH646/PR716 API-field reference follow-up; retain deferred polish scope. |
| `zcnl5` | task | in_progress | Low / Q | PR1049 match-only stream-counter candidate; settings no-op writes remain y5hvo. |
| `3ot61` | task | open | Medium / W | Investigate association guard/copy; do not remove deletion protection from a hypothesis. |
| `62rgr` | task | open | Medium / W | Fixture-first; one current ID space, stale candidate plausible, dual-ID fix unsupported. |
| `c7ll3` | task | in_progress | Medium / W | PR1049 mode-reporting candidate; already-failed apply was not false success. |
| `cruhd` | task | in_progress | Medium / W | PR1049 summary candidate; disposable live A-to-B acceptance remains. |
| `e0mbg` | task | open | Medium / W | GH646/PR716 focus/review/accessibility follow-up; retain deferred batch. |
| `ooli5` | task | open | Medium / W | Residual safe error mapping; held conflicts/gaps; do not propagate raw bodies. |
| `sumq7` | task | blocked | Medium / W | PR1048 stacked on PR1045; delivery/retargeting/protected CI remain. |
| `0m06f.10` | epic | open | Container / C | Implementation complete — verification and acceptance; aggregate only, complete/verify children rather than reimplementing. Historical roster is not current outstanding implementation. |
| `8gmk8` | epic | open | Container / C | Operator UX long tail; aggregate only, complete/verify children rather than reimplementing. |
| `cg0og` | task | blocked | Container / C | Deliver selected P3 restore-cleanup and sync-reporting fixes; aggregate only, complete/verify children rather than reimplementing. Task-typed coordinator; do not double-count. |


## Dependencies, overlap and delivery readiness

- Actual active blocking edges: `6qvts` waits on `381gu`; `sumq7` waits on
  `0m06f.3.3` (PR1045). Do not interpret `related`/`tracks` links as blockers.
  Historical edges from `l5z6r` to `7rsy7` and `v7d37` to `msqf7` point to closed
  records and do not explain their current remaining work.
- PR1040 is blocked by authenticated ARM vendor inputs. Other image candidates
  also encounter CA/cold-build provider constraints; those are not automatically
  regressions in each candidate. `293n8` requires PO design decisions;
  `ne0gf`/`hne7k` require upstream contracts.
- Shared deliveries: `0r0w7` + `k9e4y`; `gk4d0` + `qguc7` (the latter also owns
  prose); `0by9g` + the omitted-start residual of `oypsc`. Count their common
  implementation once, retaining each distinct acceptance obligation.
- `jnw1v`/`oaoel` share PR1047 but cover distinct response boundaries.
  `c7ll3`/`cruhd`/`zcnl5` share PR1049 but cover mode, prediction, and stream-count
  contracts. `y5hvo` remains separate actual no-op settings writes.
- Nineteen pre-existing leaf records already have candidates or assessments
  across PR1040–1049. Review, integration, design approval or acceptance is next;
  their existence is not evidence of shipment. `rtst2.9` and `04c0u.8` likewise
  need verification/adoption, not a second implementation.
- Preserve channel-editing/journal theme deferrals and the two explicitly parked
  functional gaps. Existing priority changes do not authorize their execution.
- Some newer leaves/coordinators remain unparented despite the portfolio's
  hierarchy goal. No speculative reparenting or stale-store import was performed.

## Follow-up and verification

- No existing work was closed merely because a PR is merged or a receipt claims
  tests passed. Newly created verification beads own the unresolved GitHub
  disposition and scope checks.
- Existing status, assignee, deferral, parent and blocking decisions were
  preserved. New records have non-closed parents. Related links do not invent
  new blocking dependencies.
- Intake validation checked unique GitHub external references, acceptance
  criteria on each canonical intake record, new-record counts, original-state
  preservation, aggregate counts and dependency cycles. No application code was
  changed, so no application test/build result is claimed.
- Query GitHub work with `bd list --label github-first --limit 0 --sort priority`;
  add `--ready` for work without active blockers. Stored `in_progress` work may
  require review or live acceptance rather than more implementation.
