# Settings Information Architecture — Proposal

**Status:** Adjudicated. PO decisions **D1–D5** were taken on 2026-07-29 and are recorded in the
comments on epic `enhancedchannelmanager-70u0r`. This document is preserved as the analysis that
produced them — it is **not** updated to describe the shipped state. Where a decision went against
the recommendation, the resolution is noted inline (see §7 for D2). For the shipped Settings
grouping, read [`operator-workspace-information-architecture.md`](./operator-workspace-information-architecture.md)
and `frontend/src/components/settingsSections.ts`.
**Epic:** `enhancedchannelmanager-70u0r`
**Builds on:** [`operator-workspace-information-architecture.md`](./operator-workspace-information-architecture.md) (approved) and the in-flight, uncommitted beads `2896r.16` / `2896r.17` (the Settings drill-in and the heading→breadcrumb hoist, including the untracked `frontend/src/components/settingsSections.ts`).
**Author:** UX Designer

---

## 1. What this document is for

The PO's framing: *"the settings navigation is just one giant blob of things."* The approved
operator-workspace IA already split the drill-in into **Settings** (14) and **Administration** (4),
so the blob is specifically the 14 non-admin destinations. This proposal recommends a grouping for
those, adjudicates six proposed relocations against measured evidence, checks the result against
every commitment the approved IA makes, and decomposes the work into beads.

**Three of the six proposed relocations are not supported by the evidence and I recommend dropping
them.** The reasoning is in §6. One of them — Appearance as a per-viewer preference — rests on a
premise that is false in the current implementation, and acting on it would ship a UX lie.

---

## 2. Method and evidence base

Everything asserted below was read from source at `/home/lecaptainc/ecm/wt-newui` (branch `newui`)
or measured against the running instance (`ecm-ecm-1`, `ECM_VERSION=0.18.1-0000`,
`GIT_COMMIT=cbabdc17`). Where I could not verify something, I say so.

- Section contents: counted `.settings-section` blocks and their `<h3>` headings in
  `frontend/src/components/tabs/SettingsTab.tsx` and every `frontend/src/components/settings/*.tsx`.
- Persistence: traced each control's state variable to its write in the `api.saveSettings({…})`
  payload (`SettingsTab.tsx:1391–1470`).
- Live data: queried the application database read-only
  (`sqlite:////config/journal.db`, `mode=ro`) inside the container, plus the ECM MCP surface.
- Route/crumb/nav contracts: `useHashRoute.ts`, `routeHierarchy.ts`, `routeHierarchy.test.ts`,
  `TabNavigation.tsx`, `App.tsx:2537–2554`, `e2e/operator-shell.spec.ts`.

---

## 3. Evidence: what is actually in the 18 destinations

### 3.1 The nine pages rendered inline in `SettingsTab.tsx`

The epic states eight. It is **nine** — `normalization` is a hybrid that the count missed: it renders
two inline `.settings-section` blocks *and* `<NormalizationEngineSection />`.

| Route | Inline lines | Sections | Contents | Save mechanism |
|---|---:|---:|---|---|
| `general` | 212 | 3 | Dispatcharr Connection, Stats Polling, Logging | shared `handleSave` + sticky pending bar |
| `appearance` | 338 | 6 | Theme, Date Format, Display Options, VLC Integration, Stream Preview, Notifications *(toast history)* | shared `handleSave` + sticky pending bar |
| `channel-defaults` | 372 | 6 | Channel Naming, Timezone Preference, Channel Profiles, EPG Matching, Stream Deduplication, Smart Sort Priority | shared `handleSave` + sticky pending bar |
| `normalization` | 193 | 2 inline + `NormalizationEngineSection` (2,579 lines) | Default Behavior, Country Prefix Format, + the whole normalization engine | shared `handleSave` via an **inline** Save button; **not** in `auditedLongSettingsPages`, so no sticky pending bar |
| `channel-pipeline` | 215 | 4 inline + `EventSyncTeamAliasesSection` | Stream Name Exclusion, M3U Group Exclusion, Auto-Sync Group Exclusion, Runaway Safety Cap, + Event Sync Team Aliases | shared `handleSave` + sticky pending bar |
| `email` *(“Notification Settings”)* | 366 | 5 inline + `AlertMethodsSection` | SMTP Configuration, Test Connection, Email Alert Recipients, Discord Webhook, Telegram Bot, + Alert Methods | shared `handleSave` + sticky pending bar |
| `integrations` | 427 | 4 | Emby, Plex, Jellyfin, Trusted Media Networks | shared `handleSave` + sticky pending bar |
| `m3u-digest` | 443 | 8 | Digest Notifications, Frequency, Content Filters, Account Filter, Exclude Patterns, Email Recipients, Discord Notification, Last Digest | its **own** `handleSaveDigestSettings`; not in `auditedLongSettingsPages` |
| `maintenance` | **1,146** | ~10 | Stream Probing, Reset Probe State, Probe History, Orphaned Channel Groups, Strike Rule, Struck Out Streams, Stale Streams, Auto-Created Channels, Channel Groups Diagnostic, Channel Groups With Streams | shared `handleSave` + sticky pending bar |

`maintenance` is the single largest inline page by a factor of two and a half. Any narrative that
treats the nine as interchangeable understates it.

### 3.2 The component-backed pages

| Route | Component | Lines | Notes |
|---|---|---:|---|
| `normalization` (part) | `settings/NormalizationEngineSection.tsx` | 2,579 | Larger than most whole routes. |
| `scheduled-tasks` | `ScheduledTasksSection.tsx` | 725 | Lives in `components/`, not `components/settings/`. |
| `tls-settings` | `settings/TLSSettingsSection.tsx` | 721 | Admin only. |
| `tag-engine` | `settings/TagEngineSection.tsx` | 708 | |
| `backup-restore` | `settings/BackupRestoreSection.tsx` | 560 | Plus 4 children: `BackupScheduleBanner`, `OutboundPolicyCard`, `SyncTargetsCard`, `CloudTargetsCard`. |
| `lookup-tables` | `settings/LookupTableSection.tsx` | 375 | See §7. |
| `mcp-settings` | `settings/MCPSettingsSection.tsx` | 367 | Admin only. |
| `linked-accounts` | `settings/LinkedAccountsSection.tsx` | 342 | |
| `user-management` | `settings/UserManagementSection.tsx` | 317 | Admin only. |
| `auth-settings` | `settings/AuthSettingsSection.tsx` | 218 | Admin only. |

### 3.3 Structural findings that shape everything downstream

**F1 — The inline pages are one form, not nine.**
`SettingsTab.tsx` holds **179 `useState` calls**, **47 handlers**, and a **single 66-key
`api.saveSettings({…})` payload**. `savePayloadSignature` is computed across the whole payload, so
an unsaved edit made on Appearance raises the pending-changes state on General as well.
`handleSave` validates the *General* page's `url` field — `if (!url) { notifications.error('URL is
required'); return; }` — regardless of which page the operator saved from. These pages are not
independent components waiting to be lifted out; they are one form with nine views.

**F2 — Three hand-maintained lists declare the same 18 ids.**
The `SettingsPage` union (`useHashRoute.ts:4`), `VALID_SETTINGS_PAGES` (`useHashRoute.ts:12`), and
`SETTINGS_SECTIONS` (`settingsSections.ts:24`). They agree today. `settingsSections.ts` imports
`SettingsPage` *from* `useHashRoute`, so the dependency runs the wrong way to fix this by having
`useHashRoute` read the registry. Any add/remove touches three files.

**F3 — The drill-in already renders groups.**
`TabNavigation.tsx:77–80` builds `settingsGroups` as a hardcoded two-way `adminOnly` split and
renders each as `<section className="navigation-group"><h2>{label}</h2><ul>…` — the *same* markup
the primary nav uses for `OVERVIEW`/`OPERATIONS`/`AUTOMATION`/`INSIGHTS`/`SYSTEM`. **The grouping
mechanism already exists and already ships.** What is missing is a `group` field on the registry and
a derivation that reads it instead of hardcoding two. This is the single most important finding in
this document: the operator-visible win is cheap.

**F4 — Contextual feature→config links already exist.**
`routeHierarchy.ts` declares `settingsLinks` on three routes: Channel Manager → `#settings/channel-defaults`,
Channel Pipeline → `#settings/channel-pipeline`, M3U Changes → `#settings/m3u-digest`. The
"config lives apart from its feature" complaint is already mitigated in the feature→config direction
for exactly the two routes the draft wanted to relocate.

**F5 — Two save affordances coexist.** Eight inline `onClick={handleSave}` "Save Settings" buttons
plus the sticky `.settings-pending-actions` bar. On the seven `auditedLongSettingsPages` both are
present simultaneously. Nielsen #4 (consistency) — **Minor**. Backlog candidate, not this epic.

**F6 — A naming collision already exists.** The `appearance` page contains a section titled
**Notifications** (toast history and clearing), while a separate destination is titled
**Notification Settings**. Its own copy points elsewhere again: *"Configure alert methods in
Settings → Alert Methods"* — a destination that does not exist under that name. **Major** for
recognition-over-recall.

---

## 4. Recommended grouping

Groups are ordered along the operator's path, mirroring the rationale the approved IA already
applies to the primary sidebar.

| # | Group | Destinations | Why these belong together |
|---|---|---|---|
| 1 | **Connections** | General, Integrations | Both answer *"what other systems does ECM talk to?"* General owns the Dispatcharr connection ECM is a client of; Integrations owns the Emby/Plex/Jellyfin servers ECM reads back from for Stats attribution. |
| 2 | **Channel Processing** | Channel Defaults, Channel Normalization, Tags, Channel Pipeline | The path a stream takes to become a channel: defaults for creation, rules for cleaning the name, the vocabularies those rules match against, and the automation that runs it. Tags belongs here on measured evidence — `TagGroup` is consumed only by `normalization_engine.py`, `routers/normalization.py`, and `normalization_migration.py` (plus its own CRUD router and backup export). |
| 3 | **Notifications & Reports** | Notification Settings, M3U Digest | Notification Settings is *delivery transport* (SMTP, Discord, Telegram, Alert Methods). M3U Digest is a *report* that rides that transport. Adjacent, not merged — see R4. |
| 4 | **Upkeep** | Scheduled Tasks, Maintenance, Backup & Restore | *"How do I keep this instance healthy?"* — the recurring work, the diagnostic and cleanup tools, and the recovery path. |
| 5 | **Workspace** | Appearance, Linked Accounts | The operator-facing surface rather than instance behaviour: how ECM presents itself, and which identities sign in to it. |
| 6 | **Administration** | Authentication, User Management, TLS Certificates, MCP Integration | Unchanged from the approved IA. Admin-only; `adminOnly` already drives it. |

Thirteen non-admin destinations are placed. **The fourteenth, Lookup Tables, has no coherent home in
any of these groups** — which is not a gap in the grouping, it is the finding. See §7.

### Why not the draft's grouping

- **"Connection & Sources" = General + Linked Accounts + Integrations.** Rejected. Linked Accounts
  is not a source connection. Reading the component: *"Allows users to view and manage their linked
  authentication identities. Users can link multiple providers (Local, Dispatcharr, OIDC, SAML,
  LDAP)…"* It is a per-user authentication surface. Joining it to General and Integrations groups
  three unrelated things under the loose word *connection* — a name defined by breadth rather than
  by what the members are (CLAUDE.md naming discipline).
- **"Automation & Alerts" = Scheduled Tasks + Notifications + M3U Digest.** Rejected. It pairs a
  *scheduler* with two *notification* surfaces on the strength of "both happen without me." Scheduled
  Tasks is upkeep — EPG refresh, M3U refresh, database cleanup — and it reads better beside
  Maintenance and Backup & Restore, which is where an operator goes when something needs tending.
- **"System" as a group label.** Rejected on naming grounds. The approved primary nav already uses
  `SYSTEM` as the group that *contains* Settings. Reusing it for a group *inside* Settings means one
  word denoting two different levels of the same tree. `Upkeep` is proposed instead; see **D1**.

### Rendered cost — must be measured, not assumed

The expanded drill-in is `244px` wide and currently renders Back + **2** group headings + 18 links.
The proposal takes it to **6** headings. At the approved `1280×720` priority viewport this adds
roughly 120px of vertical extent to a rail that is already close to the viewport bound. The approved
IA makes `1280×720` a gate, so **bead B3 below is a measurement, not a formality**: if the drill-in
overflows, the correct answer is sidebar-internal scrolling with the Back control kept in view — not
shrinking the grouping. I have deliberately not guessed the outcome; the drill-in is uncommitted and
not on the running instance, so I could not measure it.

---

## 5. Wireframe — expanded drill-in, `244px`

```
┌──────────────────────────────┐
│ [◧ logo]  ECM                │  ← logo is the collapse control (unchanged)
├──────────────────────────────┤
│ ← Back                       │  ← accessible name "Back to main navigation" (unchanged)
│                              │
│ CONNECTIONS                  │  ← <section class="navigation-group"><h2>
│   ⚙  General                 │     <a href="#settings"> aria-current="page"
│   🧩 Integrations            │
│                              │
│ CHANNEL PROCESSING           │
│   📺 Channel Defaults        │
│   ✨ Channel Normalization   │
│   🏷  Tags                    │
│   ⚡ Channel Pipeline         │
│                              │
│ NOTIFICATIONS & REPORTS      │
│   🔔 Notification Settings   │
│   ✉  M3U Digest              │
│                              │
│ UPKEEP                       │
│   ⏰ Scheduled Tasks          │
│   🔧 Maintenance             │
│   💾 Backup & Restore        │
│                              │
│ WORKSPACE                    │
│   🎨 Appearance              │
│   🔗 Linked Accounts         │
│                              │
│ ADMINISTRATION               │  ← rendered only when isAdmin
│   🔒 Authentication          │
│   👥 User Management         │
│   🔐 TLS Certificates        │
│   🤖 MCP Integration         │
└──────────────────────────────┘
```

No new API. The grouping is a static property of `SETTINGS_SECTIONS`; the drill-in already fetches
nothing.

Collapsed (`68px`): group headings collapse to zero box exactly as the primary nav's already do —
they reuse `.navigation-group h2`, which the e2e already asserts is `display:none` with a zero-size
rect when collapsed. That assertion must be extended to the drill-in (B3).

---

## 6. The six proposed relocations, adjudicated

### R1 — Fold **Tags** into **Channel Normalization**

**Case for.** Measured coupling is real and total: `TagGroup` appears in `normalization_engine.py`,
`normalization_migration.py`, `routers/normalization.py`, `routers/tags.py` (its own CRUD), and
`routers/backup.py` (export). No other consumer. The registry description says so too: *"Manage tag
vocabularies used by normalization rules for pattern matching."*

**Case against.** `NormalizationEngineSection.tsx` is 2,579 lines and the `normalization` route
already carries two inline sections on top of it. Adding `TagEngineSection` (708 lines) produces the
largest single surface in ECM at roughly 3,300 lines — on a route the approved IA already lists among
those "requiring sticky orientation/save treatment."

**Deep-link / muscle-memory cost.** Retires `#settings/tag-engine`. One documentation reference to
update. Requires an entry in `LEGACY_SETTINGS_PAGE_ALIASES` so the bookmark resolves rather than
silently falling back to General.

**Recommendation: group adjacency, not merge.** Placing Tags directly beneath Channel Normalization
inside **Channel Processing** delivers the entire discoverability benefit at zero route cost, zero
deep-link breakage, and zero page-size cost. The evidence supports the *relationship*; it does not
support the *merge*.

---

### R2 — **Lookup Tables**

See §7. It is the only relocation with a data-lifecycle question attached, and the only one whose
premise the PO and orchestrator currently disagree about.

---

### R3 — **Appearance** is a per-viewer preference → move to the user menu

**The premise is false as implemented, and I recommend dropping this relocation.**

The brief states Appearance was verified to be theme selection. It is six sections — Theme, Date
Format, Display Options, VLC Integration, Stream Preview, Notifications — and **every one of them
writes to the single global settings blob**:

```
SettingsTab.tsx:1391–1470  api.saveSettings({
    …, theme: theme, date_format: dateFormat, show_stream_urls: showStreamUrls,
    vlc_open_behavior: vlcOpenBehavior, stream_preview_mode: streamPreviewMode, … })
```

`theme` included. There is no per-user preference store anywhere in the model layer. Two operators
signed into the same instance share one theme, one date format, one stream-preview mode.

**Case against relocating.** Moving Appearance to the account menu communicates *"this is yours"* to
a control that is instance-wide. The next operator to change the theme changes it for everyone, with
the placement having told them the opposite. That is a Nielsen #2 violation created by the fix —
**Major**, because it is silent and shared-state.

**Recommendation: drop the relocation.** Keep Appearance as a Settings destination, grouped under
**Workspace**. If per-viewer preferences are genuinely wanted, that is a backend feature (a per-user
settings table plus a read/write path that falls back to the instance value) sized **Large**, and the
IA move follows it rather than leading it. Filed as a backlog candidate in §10, not as part of this
epic.

**Related, and cheap: fix F6.** Rename the Appearance page's `Notifications` section to
**Toast Notifications**, and correct its body copy — it currently directs the operator to
*"Settings → Alert Methods"*, which is not a destination. Sized **Small**; bead B4.

**The genuinely per-user surface is Linked Accounts, not Appearance.** It manages *the signed-in
user's own* authentication identities. If any Settings destination has a case for the account menu,
it is that one. I am not recommending the move now (see **D4**) — grouping it under **Workspace**
makes it findable, and the account menu is the cheaper follow-up once the grouping has shipped.

---

### R4 — **M3U Digest** sits under **Notifications**

**Case for.** M3U Digest is a scheduled report delivered over email and Discord — the same transports
Notification Settings configures. An operator looking for "how do I get told about playlist changes"
could plausibly look in either.

**Case against.** Notification Settings is 6 sections; M3U Digest is 8. The merged page is 14
sections with **two different save paths** — the shared `handleSave` on one half and
`handleSaveDigestSettings` (`/api/m3u-digest/settings`) on the other. Half the page would obey the
sticky pending-changes bar and half would not, on the same screen. That is a save-safety hazard on a
page the approved IA already flags for sticky-save treatment.

Also, per **F4**, `routeHierarchy.ts` already links M3U Changes → `#settings/m3u-digest`. The
feature→config direction is solved.

**Deep-link cost.** A merge retires `#settings/m3u-digest`, which is a live `settingsLinks` target
in `routeHierarchy.ts` and appears in the approved IA's route table.

**Recommendation: group adjacency, not merge.** Notification Settings and M3U Digest sit together in
**Notifications & Reports**, in that order — transport first, then the report that uses it.

---

### R5 — **Channel Pipeline** settings move to the Channel Pipeline route

**Two of the brief's premises do not hold.**

1. The page is not "the stream-name exclusion list." It is five sections: Stream Name Exclusion,
   M3U Group Exclusion, Auto-Sync Group Exclusion, **Runaway Safety Cap**, and
   **Event Sync Team Aliases** (`<EventSyncTeamAliasesSection />`, `SettingsTab.tsx:3436`).
2. The contextual link the IA prescribes already exists and already ships:
   `ROUTE_HIERARCHY['channel-pipeline'].settingsLinks = [{ href: '#settings/channel-pipeline',
   label: 'Channel Pipeline settings' }]`.

**Case against relocating.** The four exclusion/cap fields
(`auto_creation_excluded_terms`, `auto_creation_excluded_groups`,
`auto_creation_exclude_auto_sync_groups`, and the admin-gated safety-cap value) are keys in the
shared 66-key settings payload. Relocating the UI means either carving those keys out of the shared
save or giving `ChannelPipelineTab.tsx` — already 2,389 lines, with no section or panel scaffold to
receive them — a slice of `SettingsTab`'s form state. The safety-cap field carries a backend
field-level admin gate whose non-admin echo behaviour is explicitly commented in the payload; moving
it is a correctness risk, not a layout change.

**Recommendation: do not relocate.** Keep `#settings/channel-pipeline`, grouped under
**Channel Processing** directly beneath Channel Normalization. Backlog candidate (§10): surface the
Runaway Safety Cap's *current value*, read-only, on the Channel Pipeline page — an operator running
rules wants to see the limit without leaving; that is a status affordance, not a settings move.

---

### R6 — Extract the inline pages into `settings/*.tsx`

**The direction is right. The count and the sizing in the brief are both wrong.**

- It is **nine** pages, not eight (`normalization` is the hybrid the count missed).
- "No UI change; makes everything else cheaper" holds only *after* the shared form state is lifted.
  Per **F1**: 179 `useState`, 47 handlers, one 66-key payload, one cross-page
  `savePayloadSignature`, and a `handleSave` that validates General's `url` from every page. Nine
  naive extractions would either duplicate that state nine times or thread ~60 props per component.

**Recommendation: do it, in two stages, and after the grouping ships.**
1. Lift the shared settings form into a `useSettingsForm` hook/context — the payload, the baseline
   signature, `hasPendingChanges`, `supportsPageSave`, and `handleSave` — with zero user-visible
   change. **Large.**
2. Extract the nine pages against that context, one bead each, ordered least-coupled first
   (`m3u-digest` has its own save; then `integrations`, `channel-pipeline`, `appearance`,
   `channel-defaults`, `normalization`, `email`, `general`) and `maintenance` last (1,146 lines,
   ~10 sections). **Large**, decomposed into nine **Small** children.

The grouping does not depend on either stage. Shipping the grouping first delivers the
operator-visible win without waiting on a refactor of the largest file in the frontend.

---

## 7. Lookup Tables

> ### Outcome — D2: the PO chose **R2-D**, not the recommended R2-B
>
> Full removal, pipe included. Implemented in bead
> `enhancedchannelmanager-70u0r.1`: the Settings destination, `LookupTableSection.tsx`/`.css`, its
> devHarness catalog entry and renderer, the five API client functions, `routers/lookup_tables.py`,
> the `LookupTable` model, `_resolve_lookups`, the `inline_lookups` / `global_lookup_ids` preview
> request fields, the `|lookup:` pipe in **both** template engines, and the `lookup_tables` table
> (destructive Alembic revision `0041`). Operator-facing upgrade note:
> [`user_guide/epg/lookup-tables-retired.md`](../user_guide/epg/lookup-tables-retired.md).
> `#settings/lookup-tables` was added to `LEGACY_SETTINGS_PAGE_ALIASES` → `general`, as §6 required.
>
> **One claim in §7.4 below is wrong and the correction is why D2 is defensible.** §7.4 treats
> removing the pipe as having "an output-changing blast radius." It does not. Measured against the
> engine directly: `generate_xmltv()` calls `generate_channel_xml` with six positional args, leaving
> `lookups` at its `None` default, so `TemplateEngine.render` raised "unknown lookup table" and
> `render_template`'s fallback emitted the **raw template text** as the programme `<title>`/`<desc>`.
> After removal the same template raises "unknown transform" and takes the same fallback — byte-for-byte
> identical generated XMLTV. The only behaviour that changes is **preview**, which stops contradicting
> the guide. §7.4's "under R2-D it would be a genuine migration with an export step" stands and is
> honoured by the upgrade note; "output-changing" does not.
>
> **§7.2's zero-rows evidence was also too narrow to carry the migration.** Zero on this instance is
> not zero everywhere: the Lookup Tables page was a reachable Settings destination in every release
> from **v0.16.0 (2026-05-12) through v0.18.0 (2026-07-26)**, so populated instances cannot be ruled
> out. That is why revision `0041` dumps every row to JSON beside the database before dropping, and
> why the upgrade note leads with an export step.

### 7.1 The unresolved premise, stated plainly

The PO believes *"that B1G advanced EPG should be dead."* The orchestrator reported Dummy EPG as
alive. **This is unresolved and I have not resolved it — only the PO can.** What I can do is show
that the Lookup Tables recommendation does not depend on the answer.

**What I verified independently, from source and from the live instance:**

1. **Dummy EPG Profiles is a live, supported feature at HEAD.** `routers/dummy_epg.py` is imported
   and registered (`routers/__init__.py:27, 61`). `<DummyEPGManagerSection />` renders
   unconditionally in `EPGManagerTab.tsx:1367`.
2. **Commit `93679fcf` (bead `09x38.4`, 2026-07-17) removed the *legacy Dummy EPG Sources* path, not
   Profiles.** Its message records the PO's own Option-B decision: *"Dummy EPG Profiles is the
   supported path (Event Sync integration, live B1G profile, live preview + richer templates); the
   legacy `source_type=dummy` section is deprecated."* It also states *"Zero data deletion, no
   migration, no backend change… UI-level fold only."* Nothing the lookup pipe depends on was
   removed by it.
3. **"B1G Advanced EPG" is an operator-chosen *profile name*, not a feature name.** It is one row:
   `dummy_epg_profiles` id=1, `enabled=1`. Deleting that row is an instance data operation. It does
   not retire a capability, and nothing in this proposal depends on whether the PO deletes it.
4. **Zero lookup tables.** `select count(*) from lookup_tables` → `0`. The table exists; it is empty.
   (Queried directly against `/config/journal.db` in read-only mode — the orchestrator's
   `/api/lookup-tables` → `[]` is confirmed.)
5. **No profile references the pipe, and structurally none can.** `dummy_epg_profiles` has 33
   columns; **none of them stores lookup-table ids**. I scanned every string column of the one
   existing profile for the substring `lookup`: zero hits. A profile cannot bind a lookup table.

**And one finding neither the PO nor the orchestrator had:**

6. **The lookup pipe is unreachable from real EPG generation.** `_resolve_lookups` is called from
   exactly two places — `POST /api/dummy-epg/preview` and `POST /api/dummy-epg/preview/batch`
   (`dummy_epg.py:459, 480`). The generation path does not:
   `GET /api/dummy-epg/xmltv` → `generate_xmltv(profile_data, channel_map)` →
   `generate_channel_xml(ch_id, ch_name, ch_number, tvg_id, profile, streams)`
   (`dummy_epg_engine.py:672–673`) — **no `lookups` argument at any hop**. It is always `None`.
   A template containing `{x|lookup:t}` emits the *literal raw template text* into the XMLTV, because
   `render_template` catches `TemplateSyntaxError` and falls back to the raw string
   (`dummy_epg_engine.py:180–182`).
7. **The only UI that ever binds a lookup table is the deprecated one.**
   `DummyEPGSourceModal.tsx` (legacy sources) is the sole consumer of `global_lookup_ids` and
   `inline_lookups`. `DummyEPGProfileModal.tsx` contains **zero** occurrences of `lookup`.
8. **The docs say so already.** `docs/template_engine.md:63–66`: global tables are *"attached to a
   **source** by ID via `global_lookup_ids`"* — the source path that `93679fcf` deprecated, not the
   profile path that survived it.

### 7.2 The honest framing

Lookup Tables is **not** "a live feature with zero adoption." It is a feature wired only to a
deprecated surface, structurally unable to influence generated output, with zero data on this
instance. That is materially cheaper to retire than the epic assumed.

**Both branches of the unresolved Dummy EPG question lead to the same recommendation:**

- **Branch A — Dummy EPG Profiles stays** (what the evidence supports, and what the PO themself
  approved in `09x38.4` twelve days ago). The pipe's only reachable consumer is the legacy source
  modal, which folds away entirely on a zero-legacy instance — including this one. The management UI
  does not earn a top-level Settings destination.
- **Branch B — Dummy EPG Profiles goes away.** Lookup Tables goes with it, unconditionally.

**Therefore decision D2 is not blocked on the Dummy EPG question.** That is the useful thing here.
What *would* change by branch is scope: Branch B turns this into a much larger Dummy EPG deprecation
in which Lookup Tables is a footnote.

### 7.3 Options

| Option | What it does | Cost | Verdict |
|---|---|---|---|
| **R2-A** Keep as-is | Lookup Tables stays a top-level Settings destination | Permanent orphan — no group it belongs to, a destination whose feature cannot affect output | Not recommended |
| **R2-B** Retire the destination, keep the pipe | Remove from `SETTINGS_SECTIONS`, delete `LookupTableSection.tsx`/`.css`, its devHarness entry, and the four API client functions. **Keep** `routers/lookup_tables.py`, the `LookupTable` model, `_resolve_lookups`, and `\|lookup:` in both template engines | **Small.** No data migration on any instance | **Recommended** |
| **R2-C** Relocate into the Dummy EPG surface | Move the editor into `DummyEPGManagerSection` or the legacy source modal | Puts a management UI on the surface `93679fcf` deliberately folded away; unreachable on a zero-legacy instance | Rejected — moves the orphan |
| **R2-D** Full removal, pipe included | Also drop the pipe from both engines, the router, the model; destructive migration dropping `lookup_tables` | **Medium** + a blocking upgrade note | Not now — revisit only if the PO also retires the legacy source path |

### 7.4 The three questions the brief asked, answered

**Does the template pipe survive without its management UI?**
Yes, and it should. `|lookup:<table>` is documented DSL on both sides (`docs/template_engine.md:42`),
is covered by unit tests in `backend/tests/unit/test_template_engine.py` and the TS suite, and
`inline_lookups` on a legacy source continues to work with no global-table UI at all. Removing the
pipe is a separate deprecation with an output-changing blast radius; it should not ride along on an
IA change.

**What happens to an instance that *does* have lookup tables?**
Under R2-B, **nothing breaks and no migration is required.** The table, the model, the CRUD router
and the pipe all survive. Existing tables stay attached to their legacy sources, stay served by
`GET /api/lookup-tables`, and keep resolving in preview. The instance loses only the ability to
*edit* them from the UI. That is a behaviour change and belongs in the release notes; `docs/api.md`
must keep documenting the endpoints. (Under R2-D it would be a genuine migration with an export
step and a blocking upgrade note — a further argument for R2-B.)

**Is there a cheaper option than removal?**
R2-B *is* the cheap option: it removes a top-level destination and a 375-line component while
leaving every backend contract intact. R2-C is the one that looks cheaper and is not.

### 7.5 Precedent for the mechanism

`bead 09x38.12` did exactly this for the Administration → Security page: removed the destination,
relocated its one setting, and added `security: 'backup-restore'` to `LEGACY_SETTINGS_PAGE_ALIASES`
with a comment explaining why. Follow that pattern. Lookup Tables has no successor page, so the alias
should resolve to `general`; the alias exists so a bookmarked URL canonicalizes deliberately rather
than falling through the invalid-subpage branch.

---

## 8. Compatibility with the approved IA — point by point

| Approved commitment | Effect of this proposal | Action required |
|---|---|---|
| **Back control, accessible name `Back to main navigation`, containing the visible `Back`** (IA §Settings drill-in) | **None.** The Back button (`TabNavigation.tsx:123–136`) is a sibling *above* `settingsGroups.map(…)`. Its name, `title`, WCAG 2.5.3 relationship, and the "Back does not change the route" behaviour are all independent of group count. | Assert unchanged in B3. |
| **Section links are real `#settings/<page>` anchors carrying `aria-current="page"`** | **None** — provided no destination is removed. Grouping changes only which `<section>` a link nests in. `href`, `aria-label`, `title`, `aria-current`, `aria-disabled`, `aria-describedby` and the guarded-navigation handlers are all per-link. | If **R2-B** is approved, `#settings/lookup-tables` must be added to `LEGACY_SETTINGS_PAGE_ALIASES`, not left to the invalid-subpage fallback. |
| **`SYSTEM / SETTINGS / <SECTION>` third crumb** (IA §Settings page heading) | **This is where grouping bites.** The obvious move is a fourth crumb: `SYSTEM / SETTINGS / CHANNEL PROCESSING / TAGS`. **Recommend against.** The IA commits to a third crumb, and to *"Do not render an eyebrow plus a repeated title."* At `1280×720` a four-part crumb competes with the header's status indicator, update notice and links. The group is already visible in the sidebar with `aria-current` marking the active item; the crumb adds no orientation the operator lacks. | **PO decision D3.** If "no", `App.tsx:2542–2549` needs **no change at all**. |
| **`routeHierarchy.test.ts` enforces a non-empty crumb per section** | **Preserved.** The test iterates `SETTINGS_SECTIONS` and asserts `settingsSectionHeading(id).title` is truthy; a new `group` field does not touch it. | **Extend it** with the grouping's analogue: every section resolves a non-empty `group`; every declared group has ≥1 visible section for an admin; the non-admin view yields no empty group. This is the "enforcement code tests itself" pattern applied to the new invariant. If R2-B is approved, add: every retired settings id resolves through the alias table to a live section. |
| **`StickySectionNav` takes `placement="rail"` for Settings** (IA §Settings section navigation) | **None.** That rail lists `.settings-section` anchors *within* a page — a different axis from the sidebar's destination grouping. Grouping the sidebar does not change what the rail lists, and does not touch `auditedLongSettingsPages`/`supportsPageSave`, which the IA is explicit must not be conflated with section navigation. | Verify in B3 that the sidebar drill-in and the content rail coexist at ≥1100px without horizontal overflow. |
| **Collapsed `68px` rail: group headings occupy zero layout space** (IA §Collapsed semantics) | **Inherited.** The drill-in reuses `.navigation-group h2`, which the e2e already asserts is `display:none` with a zero-size rect for the primary nav's five headings. Four more headings inherit the same rule. | Assert it **for the drill-in** in B3 — currently only the primary nav is covered. |
| **Every primary destination appears exactly once; the global sidebar does not expand Settings pages into primary destinations** (IA §Settings routes) | **Preserved.** Grouping is internal to the drill-in. No relocation in the recommended set promotes a Settings page to a primary destination. | — |
| **Route inventory / compatibility map** (IA §Settings routes, 18 rows) | **Unchanged** under the recommended set. R1, R4 and R5 are all resolved as *adjacency, not merge*, so no route retires. **Only R2-B retires a row** (`#settings/lookup-tables`). | If R2-B is approved, update the IA route table and its "Compatibility aliases" list in the same PR. |
| **`e2e/operator-shell.spec.ts:1810` — `expect(page.locator('.navigation-group h2')).toHaveCount(5)`** | **Not broken.** That assertion runs with the drill-in closed, where only the primary nav's five headings exist. | Any new e2e that *enters* Settings must scope its locator to `nav[aria-label="Settings sections"]`, not reuse the unscoped one. State this in B3's brief. |
| **`1280×720` viewport priority** (IA §Viewport priorities) | **At risk.** Four extra group headings grow the expanded drill-in by roughly 120px in a rail already close to the viewport bound. Not measurable from source; the drill-in is uncommitted and not deployed. | **B3 is a measurement gate, not a formality.** If it overflows, add sidebar-internal scrolling with Back pinned — do not shrink the grouping. |
| **In-flight `2896r.16`/`.17`** | The proposal is **additive** to both: one new optional-then-required field on `SettingsSection`, and one derivation in `TabNavigation.tsx` replacing a hardcoded two-way split. It contradicts neither and duplicates neither. | **Every bead below is blocked on `2896r.16`/`.17` landing first** — they own `settingsSections.ts` and `TabNavigation.tsx` uncommitted. |

---

## 9. Decomposed plan — proposed beads under epic `70u0r`

I cannot create beads. Listed in dependency order for the orchestrator to create.

| # | Title | Size | Depends on |
|---|---|---|---|
| **B1** | Add a `group` field to `SettingsSection` and derive the drill-in's groups from it | **Small** | `2896r.16`/`.17` merged |
| **B2** | Apply the approved Settings group assignment, names, and order | **Small** | B1, **D1** |
| **B3** | Render-verify the grouped drill-in at 1280×720 and 1920×1080, expanded and collapsed | **Small** | B2 |
| **B4** | Rename Appearance's `Notifications` section to `Toast Notifications` and fix its cross-reference copy | **Small** | — |
| **B5** | Retire the Lookup Tables Settings destination (option R2-B) | **Small** | **D2** |
| **B6** | Record the Settings grouping in the approved IA doc and update user-guide navigation references | **Small** | B2, B5 |
| **B7** | Lift the shared settings form state into a `useSettingsForm` hook/context | **Large** | B2 |
| **B8** | Extract the nine inline Settings pages into `components/settings/*.tsx` | **Large** (9 **Small** children) | B7 |
| **B9** | Collapse the three hand-maintained settings-id lists into one source | **Small** | B1 |

### B1 — `group` field and group derivation — Small
- `settingsSections.ts`: add `group: SettingsGroup` to the interface and to all 18 entries; export
  `SETTINGS_GROUP_ORDER`.
- `TabNavigation.tsx`: replace the hardcoded two-way `adminOnly` split (lines 77–80) with a
  derivation over `SETTINGS_GROUP_ORDER`, preserving `visibleSettingsSections(isAdmin)` filtering and
  the existing empty-group filter so Administration still disappears for non-admins.
- No change to routes, hrefs, `aria-current`, the crumb, or `App.tsx`.
- Tests: extend `routeHierarchy.test.ts` per §8 (non-empty group per section; no empty groups; no
  Administration group for a non-admin). Extend `TabNavigation.test.tsx` for the rendered group
  headings.
- **Ships behind the existing two-group data** so the mechanism can merge before the naming debate
  closes. That is why B2 is separate.

### B2 — Apply the grouping — Small
Data-only change to `settingsSections.ts` plus the group labels. Split from B1 deliberately: if **D1**
(naming) runs long, B1 still merges.

### B3 — Rendered verification — Small
The approved IA's "UI work renders before it's done" gate for B1+B2.
- e2e: group headings present, in order, scoped to `nav[aria-label="Settings sections"]`; zero-box
  and `display:none` when collapsed; no sidebar x-overflow; `document.documentElement.scrollWidth ≤
  clientWidth` at both viewports.
- Record the sidebar's `scrollHeight` vs `clientHeight` at `1280×720` and assert the overflow
  behaviour (scrollable, Back reachable) rather than assuming it does not overflow.
- Assert the Back control's accessible name and route-preserving behaviour survive.

### B4 — Appearance/Notification naming collision — Small
Independent of everything else; can ship at any point.

### B5 — Retire Lookup Tables — Small — **blocked on D2**
- Remove the entry from `SETTINGS_SECTIONS`, from `VALID_SETTINGS_PAGES`, and from the `SettingsPage`
  union; add `'lookup-tables': 'general'` to `LEGACY_SETTINGS_PAGE_ALIASES` with a comment following
  the `09x38.12` precedent.
- Delete `settings/LookupTableSection.tsx` + `.css`, the `devHarness/dialogCatalog.ts` entry and the
  `dialogRenderers.tsx` import, and the four `LookupTable*` API client functions in `services/api.ts`.
- Keep `routers/lookup_tables.py`, the `LookupTable` model, `_resolve_lookups`, and `|lookup:` in
  both template engines. Keep `docs/api.md` coverage.
- Update `docs/template_engine.md` to say global tables are no longer managed from the UI and are
  API-only.
- Test: assert `#settings/lookup-tables` canonicalizes to `#settings` and does not fall through the
  invalid-subpage branch.
- Release note: management UI removed; data and API retained.

### B7 — `useSettingsForm` — Large
No user-visible change. Must preserve, verifiably: the 66-key payload shape;
`savePayloadSignature`/`hasPendingChanges` semantics *including* their cross-page scope;
the `auditedLongSettingsPages` → `supportsPageSave` contract; the `url`-required validation; and the
admin-gated safety-cap echo behaviour. A regression test proven red-without-fix for the cross-page
pending-state behaviour is the acceptance gate.

### B8 — Extract the nine pages — Large, nine Small children
Order: `m3u-digest`, `integrations`, `channel-pipeline`, `appearance`, `channel-defaults`,
`normalization`, `email`, `general`, `maintenance`. Each child: one component + tests, no UI change,
rendered parity check.

### B9 — One source for settings ids — Small
Move the `SettingsPage` union and the valid-id set into `settingsSections.ts`, derived from
`SETTINGS_SECTIONS`, and have `useHashRoute.ts` import them — reversing the current dependency
direction (**F2**). Removes the three-file add/remove tax that B5 and any future destination change
would otherwise pay.

---

## 10. Backlog candidates — not part of this epic

Findings surfaced during this work. Recorded for grooming; **not** proposed as work now.

1. **Dual save affordance** (**F5**). Eight inline "Save Settings" buttons coexist with the sticky
   pending-actions bar on the seven `auditedLongSettingsPages`. Nielsen #4, **Minor**. **Medium.**
2. **`general` mixes three concerns.** Dispatcharr Connection (a connection), Stats Polling (a
   polling cadence), and Logging (diagnostics). Logging reads better under Maintenance. **Small.**
3. **Runaway Safety Cap not visible where it acts.** Surface its current value read-only on the
   Channel Pipeline page. **Small.**
4. **Per-user preference store.** The prerequisite for Appearance ever being a per-viewer setting
   (**R3**). Backend-led. **Large.**
5. **`NormalizationEngineSection.tsx` is 2,579 lines.** Independent of this epic and untouched by it,
   but it is the largest single component in the frontend. **Large.**

---

## DECISIONS NEEDED

**D1 — Group names.**
Proposed: **Connections / Channel Processing / Notifications & Reports / Upkeep / Workspace /
Administration**. *Upkeep* is chosen over *System* because the approved primary nav already uses
`SYSTEM` for the group that contains Settings, and over *Maintenance* because a destination inside
the group is already named Maintenance. Alternative: *Instance Health*.
**Recommendation: as proposed.**

**D2 — Lookup Tables.**
**Recommendation: R2-B** — retire the Settings destination and the 375-line management UI; keep the
`|lookup:` pipe, the model, the table and the CRUD router. No data migration on any instance; a
release note for the removed UI. This recommendation holds whether or not Dummy EPG Profiles stays,
so it is **not** blocked on the unresolved Dummy EPG question (§7.1). Approving R2-D instead (drop the
pipe too) is **Medium** and needs a blocking upgrade note.

**D3 — Does the breadcrumb gain a fourth crumb for the group?**
e.g. `SYSTEM / SETTINGS / CHANNEL PROCESSING / TAGS`.
**Recommendation: no.** The approved IA commits to a third crumb; the group is already visible in the
sidebar with `aria-current`; a four-part crumb competes with the header at `1280×720`. Saying no
means `App.tsx` needs no change at all.

**D4 — Does Linked Accounts move to the header account menu?**
It is the one genuinely per-user Settings destination.
**Recommendation: not now.** Group it under **Workspace**; revisit once the grouping has shipped and
the account menu has a settings surface to receive it.

**D5 — Sequencing.**
**Recommendation: grouping first (B1–B4, B6), refactor second (B7–B8).** The grouping does not depend
on the extraction, and the extraction is the largest and riskiest work in the epic. Shipping the
operator-visible win first is the cheaper order.

**Also flagged, not a decision I can make:** the PO said *"that B1G advanced EPG should be dead."*
The evidence says that is a **profile row**, not a feature, and that the PO themself approved
Dummy EPG Profiles as the supported path in `09x38.4` on 2026-07-17. If the intent is to delete that
one profile row, that is an instance data operation with no bearing on this proposal. If the intent
is to retire Dummy EPG Profiles as a capability, that is a separate epic — and Lookup Tables becomes
a footnote inside it rather than a decision of its own.
