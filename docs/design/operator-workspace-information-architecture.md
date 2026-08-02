# ECM Operator Workspace Information Architecture

Status: implementation contract for `enhancedchannelmanager-2896r.1`

Visual reference: [ECM Operator Workspace mockup v16](https://ecm-operator-workspace.curt347145.chatgpt.site/?view=channels), source commit `48353d703b9c1b6ba2e33ea2b9f9bce05cf6412c`

Target viewports: 1920×1080 and 1280×720

Scope: navigation, shell, page hierarchy, dashboard inventory, route compatibility, shared states, and the approved Channel Manager standard

This document is normative for the new UI. The mockup establishes layout, hierarchy, density, and visibility. Current ECM APIs, permissions, and working actions remain authoritative whenever the mockup and application behavior differ. No backend capability may be inferred from a visual.

## Evidence and constraints

- Primary navigation and its current labels/icons are defined in [`frontend/src/TabNavigation.tsx`](../../frontend/src/TabNavigation.tsx) (`TabId`, `TABS`).
- Hash parsing, canonical settings URLs, legacy aliases, `pushState`, and back/forward handling are defined in [`frontend/src/hooks/useHashRoute.ts`](../../frontend/src/hooks/useHashRoute.ts).
- Current route-to-page composition and Channel Manager data/action wiring are in [`frontend/src/App.tsx`](../../frontend/src/App.tsx), especially the `activeTab` branches and `ChannelManagerTab` props.
- Settings destinations, labels, and admin visibility are in [`frontend/src/components/settingsSections.ts`](../../frontend/src/components/settingsSections.ts); the rendered sections remain in [`frontend/src/components/tabs/SettingsTab.tsx`](../../frontend/src/components/tabs/SettingsTab.tsx) under the `activePage` branches.
- Existing read APIs available to a dashboard are in [`frontend/src/services/api.ts`](../../frontend/src/services/api.ts): `getHealth`, `getChannels`, `getStreams`, `getM3UAccounts`, `getM3UChangesSummary`, `getTasks`, `getJournalStats`, and `getChannelStats`.
- Existing authentication and route protection originate in [`frontend/src/main.tsx`](../../frontend/src/main.tsx), [`frontend/src/components/ProtectedRoute.tsx`](../../frontend/src/components/ProtectedRoute.tsx), and [`frontend/src/hooks/useAuth.tsx`](../../frontend/src/hooks/useAuth.tsx).
- The detailed Channel Manager contract is tracked by beads `enhancedchannelmanager-2896r.10` through `.14`. The epic notes designate mockup v16/`48353d7` as the canonical visual reference.

## Functional user type

### Operator

- **Role:** Runs a self-hosted ECM instance and maintains the Dispatcharr channel lineup, sources, guide data, automation, integrations, and recovery configuration.
- **Goals:** Keep the lineup usable; notice failures quickly; make safe bulk changes; understand what automation did; recover from configuration or source failures.
- **Pain points addressed:** Equally weighted top-level tabs, weak task grouping, narrow dense workspaces, repeated page titles, configuration separated from the task that depends on it, and unclear status/error recovery.
- **Technical comfort:** Intermediate to power user. The operator may understand M3U, EPG, probing, containers, and logs, but should not need to remember ECM’s internal page organization.
- **Usage pattern:** Desktop browser, often at 1920×1080; 1280×720 is a required compact operational viewport, not a degraded afterthought.
- **Top tasks, in priority order:**
  1. Inspect and repair channel/assigned-stream health.
  2. Add, organize, search, and update source streams and channels.
  3. Configure or diagnose M3U and EPG inputs.
  4. Run, review, and troubleshoot automation and scheduled work.
  5. Check current activity, history, and changes.
  6. Configure integrations, access, appearance, and backup/recovery.

An authenticated non-admin operator sees all non-administration destinations. Admin-only Settings destinations remain absent for non-admin users; an inaccessible deep link must render a permission state and must not disclose protected data. This is not a new role model.

## Approved shell

### Desktop geometry

- Expanded sidebar: exactly `244px`.
- Collapsed sidebar: exactly `68px`.
- Main content uses the remaining inline size and must clear the sidebar; it never renders beneath it.
- Shell and pane children use `min-width: 0`/`minmax(0, …)` as needed. `document.documentElement.scrollWidth` must not exceed `clientWidth` at either target viewport.
- The collapse preference is stored locally per browser. Until storage is read, render the safe expanded geometry without an animated width transition. At 1280×720 the operator may collapse it, but ECM must not silently choose a different navigation state after hydration.

### Expanded grouping, labels, and order

Every primary destination appears exactly once:

1. **Overview**
   - Dashboard: `dashboard`
2. **Operations**
   - M3U Manager: `m3u-manager`
   - EPG Manager: `epg-manager`
   - Logo Manager: `logo-manager`
   - Channel Manager: `channel-manager`
3. **Automation**
   - Channel Pipeline: `channel-pipeline`
4. **Insights**
   - Guide: `guide`
   - Stats: `stats`
   - M3U Changes: `m3u-changes`
   - Journal: `journal`
5. **System**
   - Settings: `settings`

Operations is ordered along the operator's setup path: sources, then programme
data, then artwork, then the Channel Manager that consumes all three. Insights
collects the read-mostly surfaces: Guide and M3U Changes sit here rather than in
Operations/Automation because neither changes configuration.

The product name/logo precedes the groups; account/notification controls remain available without displacing primary destinations. Group labels describe operator intent and are not links.

The logo **is** the collapse control: a `<button>` wrapping the mark, first in the sidebar's focus order, with no separate Collapse button at the foot of the sidebar. Exactly one control may carry the `Collapse navigation`/`Expand navigation` accessible name: Playwright's `getByRole` name option matches substrings, so a second control sharing that phrase makes every query for the toggle ambiguous.

### Application header

The header spans the main column and carries, in order: the workspace context
label, the service status indicator, the update notice when one applies, the
User Guide and repository links, notifications, and the account menu.

- The service indicator is a single `role="status"` region reading
  `<dot> <state> · v<version>`. States are **Online** (health reports a healthy
  status), the reported status verbatim when it is anything else, **Offline**
  when the API errored, and **Connecting** before the first health response.
- The coloured dot is reinforcement only. State is always stated in text, and
  the label uses `--text-primary` rather than a tinted colour so it stays well
  clear of the 4.5:1 floor repaired in `enhancedchannelmanager-rv84z`.
- The build version is part of the indicator rather than a separate element.
  There is no application footer; the shell grid is header + work area only, and
  the bottom bound of the work area is the viewport.
- The update notice is a link to the release, shown only when an update is
  available, and its pulse animation is disabled under
  `prefers-reduced-motion: reduce`.

### Collapsed semantics

- The `68px` rail is icon-only. Product wordmark text, group headings (`OVERVIEW`, `OPERATIONS`, `AUTOMATION`, `INSIGHTS`, `SYSTEM`), and visible destination labels occupy zero layout space; they are not clipped or ellipsized fragments.
- Each destination remains a semantic link/button with a programmatic accessible name, current-state indication, and hover/focus tooltip. Tooltip text is supplementary; it is not the accessible name.
- Icons remain centered and practical pointer targets. The sidebar’s `scrollWidth` equals its `clientWidth`.
- The collapse control exposes `aria-expanded`, announces “Collapse navigation”/“Expand navigation,” retains visible focus, and does not move focus after activation.

### Navigation interaction

- Use a `<nav aria-label="Primary">` containing grouped lists and route links. The current destination uses `aria-current="page"` and a non-color-only visual treatment.
- Tab follows DOM order; Enter/Space activates button controls and Enter activates links. Do not introduce a custom arrow-key menu model for ordinary navigation links.
- Route activation uses the existing history behavior: user navigation pushes one history entry; initial canonicalization replaces rather than pushes; browser Back/Forward restores route and page.
- A skip link is the first focusable element and targets `<main id="main-content" tabindex="-1">`. On route change, move focus to the page `h1` (except browser-history restoration where preserving the browser’s expected focus is preferable) and update the document title.
- If a route is disabled during a destructive or staged operation, explain the reason in accessible text and preserve the existing navigation-away guard. Do not make an unlabeled disabled icon.
- Sidebar width changes are immediate under `prefers-reduced-motion: reduce`; otherwise any transition is at most 160ms and must not animate content position independently of the rail.

## Route inventory and compatibility map

The new shell changes discoverability, not URLs. Hashes remain the public route contract. The proposed Dashboard adds one route; it must not become the default until the PO decision in “Decisions” is resolved.

### Primary routes

| Existing/deep-link input | Canonical URL after activation | New group / label | Permission | Compatibility behavior |
|---|---|---|---|---|
| empty hash | `#channel-manager` (current behavior) | Operations / Channel Manager | authenticated | Preserve until Dashboard default is explicitly approved |
| `#channel-manager` | `#channel-manager` | Operations / Channel Manager | authenticated | Same page/actions |
| `#guide` | `#guide` | Insights / Guide | authenticated | Same page/actions |
| `#m3u-manager` | `#m3u-manager` | Operations / M3U Manager | authenticated | Same page/actions |
| `#epg-manager` | `#epg-manager` | Operations / EPG Manager | authenticated | Same page/actions |
| `#logo-manager` | `#logo-manager` | Operations / Logo Manager | authenticated | Same page/actions |
| `#channel-pipeline` | `#channel-pipeline` | Automation / Channel Pipeline | authenticated | Canonical route already rendered by `App.tsx` |
| `#auto-creation` | `#channel-pipeline` | Automation / Channel Pipeline | authenticated | Preserve legacy alias; replace/canonicalize without losing Back behavior |
| `#m3u-changes` | `#m3u-changes` | Insights / M3U Changes | authenticated | Same page/actions |
| `#stats` | `#stats` | Insights / Stats | authenticated; panels may be admin-gated | Keep panel-level permission behavior |
| `#journal` | `#journal` | Insights / Journal | authenticated | Same page/actions |
| `#settings` | `#settings` | System / Settings / General | authenticated | Same as General; no duplicate destination |
| proposed `#dashboard` | `#dashboard` | Overview / Dashboard | authenticated | New client route; no invented write behavior |
| unknown primary hash | `#channel-manager` | Operations / Channel Manager | authenticated | Preserve current fallback |

The current `TabNavigation.tsx` still declares `auto-creation`, while `useHashRoute.ts` and `App.tsx` use `channel-pipeline`. Implementation must remove that source-level mismatch without removing the legacy `#auto-creation` alias.

### Settings drill-in

Selecting **Settings** replaces the sidebar's groups with a `<nav aria-label="Settings sections">` listing every Settings destination. There is no in-page settings section list; the sidebar is the single control, and the page keeps the full content width.

The destinations are grouped, in this order:

| # | Group | Destinations | Why these belong together |
|---|---|---|---|
| 1 | **Connections** | General, Integrations | Both answer *"what other systems does ECM talk to?"*: the Dispatcharr connection ECM is a client of, and the Emby/Plex/Jellyfin servers it reads back from. |
| 2 | **Channel Processing** | Channel Defaults, Channel Normalization, Tags, Channel Pipeline | The path a stream takes to become a channel: defaults for creation, rules for cleaning the name, the vocabularies those rules match against, and the automation that runs it. |
| 3 | **Notifications & Reports** | Notification Settings, M3U Digest | Notification Settings is delivery *transport* (SMTP, Discord, Telegram, Alert Methods); M3U Digest is a *report* that rides it. Adjacent, not merged. |
| 4 | **Upkeep** | Scheduled Tasks, Maintenance, Backup & Restore | *"How do I keep this instance healthy?"*: the recurring work, the diagnostic and cleanup tools, and the recovery path. |
| 5 | **Workspace** | Appearance, Linked Accounts | The operator-facing surface rather than instance behaviour: how ECM presents itself, and which identities sign in to it. |
| 6 | **Administration** | Authentication, User Management, TLS Certificates, MCP Integration | Administrators only. |

Group order follows the operator's path, mirroring the rationale this document already applies to the primary sidebar.

- Grouping is a property of the data, not of the sidebar component: `group` on each entry of [`settingsSections.ts`](../../frontend/src/components/settingsSections.ts), rendered through the same `<section class="navigation-group"><h2>` markup the primary nav uses. Moving a destination between groups is a data change.
- **`Upkeep`, not `System` or `Maintenance`.** The primary nav already uses `System` for the group that *contains* Settings, so reusing it would make one word denote two levels of the same tree; `Maintenance` is the name of a destination *inside* this group. Both alternatives collide.
- **Administration is derived from `adminOnly`, not from the group label.** All four of its destinations carry `adminOnly`, so for a non-admin the group is left empty and dropped wholesale by the empty-group filter. A non-admin never sees an Administration heading over an empty list. `routeHierarchy.test.ts` asserts the group and the flag agree.
- **Channel Processing is four destinations, not five.** Lookup Tables briefly sat here provisionally: it served the dummy EPG template engine rather than channel processing and had no coherent home in this grouping, which was the finding, not a gap. Bead `enhancedchannelmanager-70u0r.1` retired the whole feature (PO decision D2), so the group is four. `#settings/lookup-tables` survives as a compatibility alias only.
- A **Back** control heads the drill-in. Its accessible name is `Back to main navigation`, which contains the visible `Back` label so WCAG 2.5.3 holds while the collapsed rail hides the label entirely. It is **pinned** (`position: sticky`, opaque); see the measured cost below.
- Back restores the groups without changing the route: the operator stays on the Settings page they were reading. Reaching another destination therefore takes Back first, which is the accepted cost of the drill-in.
- Entering Settings by any means (sidebar, contextual link, or deep link) opens the drill-in. Re-selecting Settings while already there reopens it after a Back.
- Section links are real `#settings/<page>` anchors carrying `aria-current="page"`, and honour the same guarded-navigation disable contract as primary destinations.

#### Measured vertical cost: the rail scrolls, and Back is pinned

Measured by bead `enhancedchannelmanager-70u0r.4` against a build of the source,
in all three themes (identical in each; no theme changes the rail's height):

| State | Viewport | Rail content | Rail budget | Overflow |
|---|---|---:|---:|---:|
| Expanded | 1280×720 | 1099px | 675px | 424px |
| Expanded | 1920×1080 | 1099px | 1035px | 64px |
| Collapsed | 1280×720 | 896px | 675px | 221px |
| Collapsed | 1920×1080 | 1035px | 1035px | 0px |

Each group costs 38.7px (a 30.7px heading plus 8px of separation), so going from
two groups to six added ~155px. Three things follow, and the middle one is the
reason Back is pinned:

- **The expanded rail already overflowed at 1280×720 before grouping**: 18
  destinations at 45px each do not fit 675px whatever the headings do. Grouping
  made an existing overflow worse; it did not create it. The collapsed rail
  overflows at 1280×720 for the same reason, and grouping contributes *nothing*
  there, since the headings are `display: none` on the 68px rail.
- **At 1920×1080 the overflow is new.** Ungrouped, the rail was ~944px against a
  1035px budget; grouping pushes it to 1099px. The spacious viewport no longer
  shows the whole list either.
- **So the rail genuinely scrolls, and `.primary-navigation` is a scroll
  container whose first child was the Back control.** An operator scrolling down
  to Administration took the only exit from Settings off screen with them. Back
  is therefore `position: sticky` with an opaque background, pinned flush to the
  top of the scroll box: destinations scroll *behind* it. This is the treatment
  the Settings IA proposal nominated in advance for exactly this outcome
  (sidebar-internal scrolling with the Back control kept in view), chosen over
  reducing the group count, which would contradict the approved assignment.

`e2e/settings-nav-groups.spec.ts` asserts the overflow *contract* rather than
asserting the rail fits: where it overflows, the rail must scroll, Back must
stay inside the visible box and stay opaque, and the last destination must be
reachable. Reducing the destination count would change the numbers above without
changing what has to hold.

### Settings page heading

Settings sections do not render their own heading block. The route header carries a third crumb (`SYSTEM / SETTINGS / <SECTION>`) with the section's descriptive line beneath it, returning that vertical space to the content pane.

- Crumb text and description come from [`settingsSections.ts`](../../frontend/src/components/settingsSections.ts), carried verbatim from the `.settings-page-header` blocks they replaced. A section's crumb may therefore differ from its shorter sidebar label (`General` → `GENERAL SETTINGS`, `TLS Certificates` → `TLS/SSL CERTIFICATE MANAGEMENT`).
- Sections that never had their own description (`scheduled-tasks`, `backup-restore`) fall back to the Settings route purpose so the header keeps a constant height across sections.
- The route `h1` is the only rendering of a section's name, so every section must resolve a non-empty crumb; `routeHierarchy.test.ts` enforces that.
- **The crumb stays three parts. The sidebar group is not a fourth crumb.** Grouping the drill-in makes `SYSTEM / SETTINGS / CHANNEL PROCESSING / TAGS` the obvious move, and it is rejected: at 1280×720 a four-part crumb competes with the header's status indicator, update notice and links, and the group is already visible in the sidebar with `aria-current` marking the active destination, so the fourth part adds no orientation the operator lacks. `App.tsx` therefore needs no change for the grouping.
- Permission and empty states keep their own in-pane messaging. The MCP section's non-admin denial block is not a page heading and is retained.

### Settings section navigation

`StickySectionNav` takes a `placement` prop. Settings passes `rail`; Stats keeps the default `top` bar.

Settings passes the selector `.settings-section` for every page rather than an allow-list of audited-long pages. As a rail the nav costs no vertical space, so the only reason to withhold it is having too little to navigate, and the component already renders nothing below two sections. `auditedLongSettingsPages` still exists and still gates `supportsPageSave`: that is a separate contract and must not be conflated with section navigation.

- `rail` renders a sticky right-hand column inside the `.settings-content` scroll container, so the section list consumes no vertical space in the content pane.
- Below 1100px the rail returns to the stacked bar above the content, because the column would otherwise crowd the settings themselves.
- Only the `top` bar overlays content from above, so only it bounds the focus-visibility logic. Treating a full-height rail as a top boundary would scroll focused controls far past the viewport.

### Settings routes

Each Settings route appears once inside Settings. The global sidebar does not expand all Settings pages into primary destinations.

Listed in navigation order. The `SettingsPage` union, the router's set of accepted
sub-page ids, and this inventory are all derived from the one declaration in
[`settingsSections.ts`](../../frontend/src/components/settingsSections.ts):
adding or retiring a destination is a single entry, not three parallel lists.

| Canonical URL | Group | Settings label | Visibility |
|---|---|---|---|
| `#settings` | Connections | General | authenticated |
| `#settings/integrations` | Connections | Integrations | authenticated |
| `#settings/channel-defaults` | Channel Processing | Channel Defaults | authenticated |
| `#settings/normalization` | Channel Processing | Channel Normalization | authenticated |
| `#settings/tag-engine` | Channel Processing | Tags | authenticated |
| `#settings/channel-pipeline` | Channel Processing | Channel Pipeline | authenticated |
| `#settings/email` | Notifications & Reports | Notification Settings | authenticated |
| `#settings/m3u-digest` | Notifications & Reports | M3U Digest | authenticated |
| `#settings/scheduled-tasks` | Upkeep | Scheduled Tasks | authenticated |
| `#settings/maintenance` | Upkeep | Maintenance | authenticated |
| `#settings/backup-restore` | Upkeep | Backup & Restore | authenticated; current section applies its existing admin rules |
| `#settings/appearance` | Workspace | Appearance | authenticated |
| `#settings/linked-accounts` | Workspace | Linked Accounts | authenticated |
| `#settings/auth-settings` | Administration | Authentication | admin only |
| `#settings/user-management` | Administration | User Management | admin only |
| `#settings/tls-settings` | Administration | TLS Certificates | admin only |
| `#settings/mcp-settings` | Administration | MCP Integration | admin only |

Compatibility aliases:

- `#settings/general` canonicalizes to `#settings`.
- `#settings/auto-creation` resolves to `#settings/channel-pipeline`.
- `#settings/security` resolves to `#settings/backup-restore`.
- `#settings/lookup-tables` resolves to `#settings` (General): the Lookup Tables feature was
  removed outright by bead `enhancedchannelmanager-70u0r.1`, so it has no successor page. The
  alias exists so a bookmarked URL resolves explicitly rather than via the unknown-page
  fallback.
- Unknown `#settings/<page>` falls back to General without exposing an admin page.

Contextual links must target these hashes directly. If preserving return context is implemented, use a non-sensitive `returnTo` value in client history state, not a query/hash format that invalidates existing parsing.

## Page hierarchy

Every primary page has exactly one semantic `h1` in this order:

1. Breadcrumb-like task context and page name in the same `h1`, for example `OPERATIONS / CHANNEL MANAGER`.
2. One-sentence purpose directly beneath.
3. One primary action, when the page has one. Additional actions enter the standard toolbar/overflow.
4. Freshness or status, only when backed by real data and timestamp semantics.
5. Filters and view controls.
6. Page content with ordered `h2` pane/section headings and `h3` subsections.

Do not render an eyebrow plus a repeated title. For Channel Manager, `OPERATIONS / CHANNEL MANAGER` is the only `h1`; “Channels” and “Streams” are `h2`s. Settings uses `SYSTEM / SETTINGS` as the page `h1` and the selected settings page as `h2`.

Primary action order follows DOM/focus order. At 1280×720, retain the title, purpose, primary action, critical status, search, and active filters above secondary view controls. Secondary actions move into a correctly sized iconized overflow menu; required actions never disappear.

Long surfaces requiring sticky orientation/save treatment are Settings (all subsection pages that exceed the viewport) and Stats. Channel Manager uses independent pane scrolling and its Edit Mode selection bar, not the Settings sticky-save pattern. Sticky regions must not cover focused elements, create nested keyboard traps, or convert existing explicit saves to autosave.

## Compact Dashboard

The Dashboard is a status-and-routing surface, not a second management UI. It contains no mutation controls. Cards use existing endpoints/cache and link to the page where the operator can act.

### Approved v1 card inventory

| Card | Value/status | Existing source | Destination | Support assessment |
|---|---|---|---|---|
| ECM service | `status`, version/release information | `GET /api/health` via `getHealth()` | Settings / General or troubleshooting context | Supported. The API does not provide a measured “uptime percentage”; do not show one. |
| Lineup inventory | channel total and stream total as separately labelled values | `GET /api/channels?page_size=1`, `GET /api/streams?page_size=1` via paginated totals | `#channel-manager` | Supported, but two calls. Reuse already-loaded App data/cache where valid. Do not label counts as health. |
| Source accounts | configured M3U account count; explicit empty prompt | `GET /api/m3u/accounts` via `getM3UAccounts()` | `#m3u-manager` | Supported. A generic “source healthy” value is **unsupported** unless account data exposes a documented current health field. |
| Recent M3U changes | existing summary counts and its source timestamp if returned | `GET /api/m3u/changes/summary` via `getM3UChangesSummary()` | `#m3u-changes` | Supported to the exact fields returned. Do not infer provider freshness from request time. |
| Scheduled work | enabled/running/failed counts derived from returned task statuses; last-run timestamp only where returned | `GET /api/tasks` via `getTasks()` | `#settings/scheduled-tasks` | Supported by one response, subject to actual `TaskStatus` fields. “Automation success rate” is **unsupported**. |
| Recent journal | total/recent/category counts exactly as returned | `GET /api/journal/stats` via `getJournalStats()` | `#journal` | Supported. “Errors requiring action” is **unsupported** unless the response has an explicit actionable/error dimension. |

`GET /api/stats/channels` may power an optional “Now playing” card only after confirming its empty semantics and permission posture. It is not required for v1 and must not block Dashboard rendering.

### Fan-out and freshness

- Dashboard performs no more than one request per distinct source on initial load. Reuse resolved App/session data when its age and provenance are known.
- Cards settle independently; one failure does not replace the whole dashboard.
- Display “Updated <time>” only for a server-provided source timestamp. When only the fetch time is known, label it “Checked <time>.”
- Stale means a documented source timestamp exceeds that source’s existing expected cadence. No universal stale threshold is approved. Without a source timestamp and cadence, omit stale classification.
- Manual Retry retries only the failed card. A page-level Refresh may refresh all cards once, with an in-progress state preventing duplicate requests.

### Card states

- **Loading:** stable skeleton matching the card geometry; card heading remains available to assistive technology; do not announce each animation.
- **Empty:** distinguish configured-but-zero (“No recent M3U changes”) from not configured (“No M3U accounts configured” with a link).
- **Error:** identify the source/action (“Couldn’t load scheduled tasks”), keep last successful value only if visibly labelled stale, and offer Retry.
- **Stale:** retain the value, show text plus icon and last known source time; never use color alone.
- **Permission:** “You don’t have permission to view this summary” without leaked counts; link only to a destination the user may access.

## Channel Manager standard

Beads `.10`–`.14` are the implementation and test decomposition of this section.

### Composition (`.10`)

- Left pane: Channels, with assigned streams inline beneath their channel.
- Right pane: all-provider Streams inventory.
- Both pane headers, searches, filters, counts, group controls, and iconized kebab menus remain reachable.
- Long names/URLs truncate within their own flexible text columns. Fixed artwork, number, status, and action columns do not compress text to zero or reorder content.

### Channel identity and status (`.11`)

- Channel number is a fixed/aligned field without `#`; channel name is an adjacent separate field.
- Use channel artwork. Show the Material image placeholder only when the channel has no logo; hide broken artwork.
- Guide subtitle: `<EPG Provider> – <tvg-name>`. Do not repeat “TVG Name.”
- One compact channel-level stream indicator contains total stream count and the highest-priority icon: no streams, failed probe, stale, black screen, low FPS, then healthy.
- Do not add duplicate text such as “No streams,” “N stale,” or “Low FPS” under the indicator.
- Resolution/quality badges and channel-level probe/status information appear only in the Channels pane.

### Stream rows (`.12`)

- Assigned-stream rows may show existing probe/quality/stale/timeout/strike details. A probe-timeout row retains usable name, URL, provider, warning, and action widths.
- Streams inventory order is artwork slot → flexible identity/URL/provider → fixed actions. Nothing renders to the right of or beneath actions.
- Inventory uses the stream’s own `logo_url`; an absent logo reserves alignment without inventing a fallback icon.
- Streams inventory shows no probe counts, probe warning text, quality/status badges, or channel-level health indicator.
- Preserve current actions such as copy URL, probe, preview, edit, remove, and reset only where current behavior supports them.

### Edit Mode (`.13`)

- All grab handles are absent from the visual layout and DOM outside Edit Mode. They appear only where reordering is supported while Edit Mode is active.
- Bottom selection bar appears only in Edit Mode with at least one selected channel.
- Primary order/icons: Delete (`delete`), Probe (`speed`, `sync` while loading), Find Duplicates (`manage_search`), Renumber (`tag`), Assign EPG (`live_tv`, `sync` while loading), Merge (`call_merge`, only for 2+), More (`more_vert`), Clear (`close`).
- More opens upward, uses icons plus normally sized text, closes on Escape, and returns focus.
- Existing confirmations, staged changes, undo/redo, commits, and API mutations remain unchanged.

### Responsive and accessibility gate (`.14`)

- Verify normal/Edit Mode, expanded/collapsed navigation, menus, long data, empty/loading/error states, and 0/1/2+ selection at both target viewports.
- Pane widths remain usable; no document-level horizontal overflow or content beneath the sidebar.
- Icon-only controls have accessible names and tooltips. Status names include text for screen readers and do not rely on color.
- Pointer and keyboard paths must expose the same operations.

## Shared state and recovery contract

These behaviors apply to primary pages, Settings subsections, panes, tables, and cards unless an existing stronger component contract applies:

- **Loading:** retain the page `h1` and controls that are safe before data arrives; use stable skeletons or a labelled busy region. Do not clear a populated screen during background refresh.
- **Empty:** say what is absent and offer the next valid action or contextual Settings link. Empty is not an error and is not represented as `0%`.
- **Error:** identify the failed operation/source, preserve unaffected regions, expose a scoped Retry, and retain diagnostic detail in accessible expandable content where safe.
- **Stale:** show the last successful content with a “Stale” label, source time, and retry. Never fabricate freshness from client render time.
- **Permission:** render a page/section-level explanation, not a blank screen or endless loader. Hide prohibited actions and data. A direct admin Settings hash for a non-admin must not momentarily render protected content.
- **Mutation:** disable only the submitted operation, communicate progress, confirm success, and leave errors recoverable with the operator’s input intact. Destructive operations retain current confirmation behavior.
- **Offline/network interruption:** treat as a recoverable request error; do not navigate away or discard staged edits.

Use `role="status"`/polite live announcements for non-urgent progress and completion, and `role="alert"` only for errors needing immediate attention. Avoid repeated toast storms for polling failures.

## Keyboard, focus, landmarks, and motion

Required landmark order:

1. Skip link.
2. Application header/banner if retained.
3. Primary navigation.
4. Main content.
5. Complementary/status region only when it has an accessible label.
6. Footer/contentinfo if retained.

Page and pane headings establish the outline; visual uppercase does not replace semantics. All focus indicators meet WCAG 2.2 AA contrast/area expectations and are not clipped by overflow containers.

Menus use trigger → Arrow Up/Down or Enter/Space to open, roving focus among items, Home/End where implemented consistently, Escape to close/return focus, and outside click to dismiss without stealing subsequent focus. Dialogs trap focus, close via Escape unless a destructive operation cannot safely be interrupted, and return focus to the opener.

Drag-and-drop has a keyboard path using the existing dnd-kit keyboard sensor where applicable, with instructions, picked-up/moved/dropped announcements, and a cancel path. Reordering cannot be pointer-only.

At 200% zoom the 1280 layout may reflow, but every destination and required action remains reachable without two-dimensional document scrolling. Reduced motion disables sidebar, menu, reorder, skeleton shimmer, and scroll animations that are not essential to understanding state.

## Viewport priorities

### 1920×1080

- Default expanded sidebar and full labels.
- Channel Manager retains two useful panes with assigned-stream detail.
- Page purpose, primary action, status, and filters remain visible without excessive empty chrome.
- Long Settings/Stats content may scroll vertically; sticky orientation remains within the main region.

### 1280×720

- Expanded `244px` and collapsed `68px` modes must both work; collapse is the operator’s space-recovery control.
- Preserve, in order: route identity, primary action, critical status/error, search, active filters, row identity, and row actions.
- Secondary actions move into iconized overflow; explanatory copy may shorten, but accessible names and full tooltip/help remain.
- Channel Manager remains two-pane. Do not solve pressure by changing it to a single pane, hiding assigned streams, shrinking text below the established ECM scale, or allowing content behind the sidebar.
- Vertical space goes first to working data. Footer/status chrome may compact, but required API/error feedback remains reachable.

### Implemented layout budget

The shared shell enforces the following measurable budget for every primary
route:

| Constraint | 1280×720 | 1920×1080 |
|---|---:|---:|
| Route header maximum height | 260px | 290px |
| Visible working-surface height above the fold | at least 160px | at least 260px |
| Navigation width | 244px expanded / 68px collapsed | 244px expanded / 68px collapsed |
| Document and main-region horizontal overflow | none | none |

At the compact height, the purpose statement is one ellipsized line with its
complete text retained in the DOM and native tooltip. The title scale is not
reduced. Primary actions, source-backed status, controls, and contextual links
retain DOM/focus order and wrap when necessary. Dense-screen secondary actions
use their labelled overflow menus; no required task action is removed.

Route-header padding compacts at 1280×720, and Dashboard outer
padding reduces to return height to task content. At 1920×1080 the spacious
padding and untruncated purpose statement remain. Channel Manager is an
intentional exception to generic content flow: its two `minmax(0)` panes and
independent pane scrolling remain, with fixed artwork, number, status/warning,
and action allocations. No responsive font shrinking or single-pane fallback
is permitted.

Vertical scroll ownership is explicit. Channel Manager owns two task-local,
sibling pane scrollers (Channels and Streams); neither pane is nested inside a
same-axis main scroller. Settings owns a content scroller and an independently
scrollable sibling navigation rail. Stats owns its long `.stats-content`
surface. Other primary routes use one route task scroller at most. The exact
browser audit reports every overflow container and rejects an ancestor/child
same-axis pair unless it is one of these documented task-local arrangements.

At 200% zoom, wrapping is preferred to hidden controls. The shell may scroll
vertically, but must not require two-dimensional document scrolling. Sticky
regions reserve focus clearance, and browser tests focus each route's exact
primary control to verify it remains within the visible main region.

### Rendered state coverage

The exact-build `operator-shell.spec.ts` suite enumerates the primary-route
inventory rather than sampling it:

| Route | Populated task surface | Relevant alternate rendered state |
|---|---|---|
| Dashboard | six-card system summary | deterministic independent source errors |
| Channel Manager | channels, assigned streams, source inventory | empty panes |
| Guide | populated guide row | explicit empty guide |
| M3U Manager | configured account row | empty accounts |
| EPG Manager | configured source row | empty sources |
| Logo Manager | populated logo row | empty inventory |
| Channel Pipeline | populated rule row and status summary | empty rules |
| M3U Changes | populated retained history | deterministic request error and Retry |
| Stats | populated summary panels | independently unavailable metrics |
| Journal | populated retained entry | deterministic request error and Retry |
| Settings | populated General configuration | N/A in the shared alternate-state loop; reload/save failure with retained edits has a dedicated journey |

Every populated row uses intercepted, deterministic API fixtures and runs
through the shared 1280×720 and 1920×1080 hierarchy/geometry loop. The loop
asserts a route-specific task element (not merely a page shell), a truly focused
primary or first task control clear of header/sticky regions and the viewport
bottom, visible
source-backed status where the route defines it, and horizontal/scroll
ownership. The alternate-state loop uses deterministic empty or failure
fixtures and verifies geometry and the applicable recovery control at both
viewports. Permission, loading, and stale behavior is tested only on routes
whose APIs define those states (Logo Manager and the retained history routes);
it is not inferred for routes without that contract.

### Rendered release matrix

Bead `enhancedchannelmanager-2896r.14` is enforced by:

```bash
npm run test:e2e:operator-workspace-release
```

This exact-build Chromium serial/no-retry gate selects tests tagged
`release:operator-workspace`. At exactly 1280×720 and 1920×1080 it covers
populated normal and Edit Mode with both 244px expanded and 68px collapsed
navigation, plus applicable empty and request-error states at both navigation
widths. Empty data is also exercised in Edit Mode at both navigation widths.
It renders the complete non-color health indicator matrix and artwork fallback
with expanded and collapsed navigation, assigned-stream timeout geometry,
inventory DOM order, Edit Mode-only handles, focus-return menus,
landmarks/headings, typography, icon density, and horizontal containment.
Real Tab and Shift+Tab traversal proves the DOM focus order across the route
header, both pane toolbars, and channel/stream rows; each traversed target must
have visible focus geometry and a non-color outline or box-shadow.

Named full-page evidence is written to one deterministic directory:

```text
test-results/operator-workspace-release/operator-workspace--<width>x<height>--<state>.png
```

The post-test manifest is part of the command and fails unless exactly 26
unique, non-empty PNGs exist: every combination of 1280×720 and 1920×1080 with
the following 13 state names:

- `populated-normal-expanded`
- `populated-normal-collapsed`
- `populated-edit-expanded`
- `populated-edit-collapsed`
- `populated-edit-selection-menu`
- `empty-expanded`
- `empty-collapsed`
- `empty-edit-expanded`
- `empty-edit-collapsed`
- `error-expanded`
- `error-collapsed`
- `health-and-artwork-matrix-expanded`
- `health-and-artwork-matrix-collapsed`

Material Icons is self-hosted through `@fontsource/material-icons`; canonical
captures do not depend on Google Fonts or another network request. Before each
PNG, the gate waits for `document.fonts.ready`, loads and checks the exact
`Material Icons` face, audits every visible icon for icon-sized geometry and
raw ligature overflow, then polls the navigation transition until its rendered
border box is 68px collapsed or 244px expanded and client/scroll widths match
(67px and 243px respectively with the one-pixel border).
Each PNG has a matching `.icons.json` record. The manifest command requires
all 26 metadata records and rejects an unavailable font, invalid icon,
raw-ligature rendering, wrong sidebar class/border-box/client width, or
unsettled rail overflow.

The selection-menu capture waits for Probe Started/Complete toasts to be
dismissed. PNGs are run artifacts, not source-controlled baselines. The CI job
explicitly enables GitHub and HTML Playwright reporters, then uploads the
complete `test-results/` and `playwright-report/` trees as the
`operator-workspace-release-matrix` artifact for 14 days, including successful
runs.

## Testable hypotheses

These are hypotheses to measure in moderated task-path review or instrument only with explicit telemetry approval; they are not claims of observed improvement.

1. Grouped sidebar navigation lets an operator locate Channel Pipeline, Scheduled Tasks, and Backup & Restore without scanning all primary destinations more often than the current equal-weight top navigation.
2. A compact Dashboard lets an operator answer “is ECM/source automation requiring attention?” without opening Stats, Journal, and Settings in sequence.
3. One page `h1` plus stable action/status/filter order reduces orientation errors when moving among manager pages.
4. At 1280×720, a `68px` collapsed rail preserves enough Channel Manager width for channel identity, assigned-stream warnings, inventory identity, and actions without document overflow.
5. Channel-level icon + total count communicates the highest-priority health condition faster than count plus repeated status text, provided its accessible name and tooltip state the condition.
6. Keeping probe and quality data out of the Streams inventory improves source scanning without reducing diagnosis capability because detailed probe state remains with assigned streams in Channels.

Evaluation records should capture task completion, wrong-route entries, backtracks, unrecoverable clipping/overflow, keyboard completion, and qualitative confusion. Do not invent percentage improvements or “usability scores” without collected observations.

## PO approvals and decisions

Already approved through the mockup review and epic/beads:

- Grouped left sidebar instead of retaining the top menu.
- `244px` expanded and `68px` truly icon-only collapsed navigation.
- Channel Manager remains a two-pane admin/operator workspace.
- Single `OPERATIONS / CHANNEL MANAGER` `h1`.
- Channel number and name are separate.
- Channel health uses one priority icon plus total stream count; badges/status stay out of Streams inventory.
- Edit Mode-only grab handles and the iconized bottom selection toolbar.
- Mockup v16/commit `48353d7` is the visual reference; current ECM function/API behavior wins.

Open product decisions (not blockers for shell/Channel Manager implementation):

1. **Dashboard landing behavior:** keep empty hash/default at Channel Manager for compatibility, or change the post-login/default route to `#dashboard`. Recommendation: ship Dashboard as an explicit destination first, retain Channel Manager as default, and change the default only after operators validate the Dashboard’s value.
2. **Dashboard optional Now playing card:** include only if `getChannelStats()` permission and zero-state behavior are verified and the card remains above-the-fold at 1280×720. Recommendation: defer from v1.
3. **Dashboard stale thresholds:** source-specific cadences are not yet approved. Recommendation: show server timestamps/“Checked” times without stale classification until each source has a defensible cadence.

## Definition of ready for implementation

- Route and Settings inventories remain synchronized with `TabId`, `VALID_TABS`, `SettingsPage`, and the Settings sidebar.
- Every Dashboard card has a confirmed response-field contract before UI work; unsupported labels are excluded.
- Shell, primary pages, and Channel Manager have component tests plus rendered browser journeys at both target viewports.
- E2E assertions include `scrollWidth <= clientWidth`, sidebar `scrollWidth === clientWidth`, main-content clearance, required action visibility, accessible names, focus return, Back/Forward restoration, direct hash entry, admin/non-admin routes, and reduced-motion behavior.
- Any intentional deviation from this specification records the PO decision and updates the relevant `2896r` bead before implementation.
