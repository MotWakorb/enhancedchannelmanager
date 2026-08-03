# User Guide: Contributing & Architecture

> Information architecture and authoring conventions for `docs/user_guide/`. Read this before adding a new article or restructuring a section.

This README is **not** a user-facing document. It explains how the user guide is organised and how to add to it. End users land on `docs/user_guide/index.md`.

## Why this exists

ECM has rich developer-facing documentation (`docs/architecture.md`, `docs/api.md`, `docs/normalization.md` developer reference, the `docs/runbooks/` set, `docs/sre/slos.md`, etc.) but no consolidated **user-facing** documentation tree. Operators and end users have historically pieced features together from in-app text, release notes, and the dual-audience halves of the dev docs.

`docs/user_guide/` fills that gap. It is task-oriented documentation for the people who **use** ECM, not the people who build it.

Scaffolded in bd-f1wnt; first user-facing feature it unblocks is bd-gb5r5.3 (DBAS / Backup & Restore end-user docs).

## Audience model

We design the IA around two distinct user types. The guide is **operator-first by default**. Most articles need no audience statement at all, because "operator" is the assumed reader unless the article says otherwise.

| Audience | Who they are | What they need from docs |
|-|-|-|
| **Operator** | The person who installed ECM, manages the Dispatcharr connection, configures rules, runs backups, troubleshoots failures. Often the same person who runs Dispatcharr. Comfortable with Docker, log inspection, and YAML-ish config. | Setup, configuration, recovery, and "how does this feature work end-to-end" reference. Can handle terminology like *task engine*, *normalization policy*, *idempotent*. |
| **End user** | The household member or downstream consumer watching the streams ECM produces. Rarely opens the ECM UI. Cares about "the channel I watch is gone" or "the EPG is wrong." | Almost nothing, but a small surface (e.g., the public stats page, if one is exposed) needs plain-language framing. Most end-user concerns are surfaced through the operator. |

An article only needs to say who it's for, in prose in the opening paragraph, when it departs from that default:

- **Rare end-user content**: say so plainly; don't assume the reader knows this article breaks the operator-first pattern.
- **A destination with an access requirement**: e.g., a Settings page that only renders for admins. State the requirement in a plain sentence ("Requires an admin account.") where the reader will see it before following steps they can't complete. This is a fact about the destination, not an audience-declaration ritual. Don't add a blockquote for it.

Do not add a `> **Audience:**` blockquote to new articles. See [Per-destination tutorial template](#per-destination-tutorial-template) below. If you're not sure whether an article needs an audience or access sentence, ask the Tech Writer in standup before drafting.

## Boundaries: what belongs here vs. elsewhere

| If the content is… | It belongs in… |
|-|-|
| "How do I do X in the UI?" / "What does this rule type do?" / "Why did the Channel Pipeline skip this stream?" | `docs/user_guide/` |
| HTTP method, path, request/response schema, error codes for an API endpoint | `docs/api.md` (the API reference, generated/maintained alongside the OpenAPI spec) |
| "How do I deploy ECM in a container?" / "How does the request flow work?" | `docs/architecture.md`, `docs/project_architecture.md`, `docs/backend_architecture.md` |
| "I got paged at 3 AM, what do I do?" | `docs/runbooks/` (operator-adjacent but written for the on-call responder under pressure, not the configuring operator) |
| Rule authoring + the developer reference for the engine | `docs/normalization.md` is a deliberately dual-audience document and stays that way; the user guide cross-links to it rather than duplicating |
| Database migrations, pytest conventions, frontend lint policy | The existing `docs/*.md` files referenced from CLAUDE.md |

The rule of thumb: if the audience is "someone trying to **use** ECM to manage their channels," it goes here. If the audience is "someone trying to **build, integrate with, deploy, or recover** ECM," it goes in the dev-facing tree.

## Authoring conventions

- **Task-oriented titles.** "Connect ECM to Dispatcharr" beats "Dispatcharr Connection Settings." Verb-first. The reader is trying to do something.
- **Open with the outcome.** First sentence: what the reader will be able to do when they finish the article. Audience is operator-by-default and normally goes unsaid. See [Audience model](#audience-model) for the two cases (non-default audience, access requirement) that do need a plain sentence.
- **Use the in-UI label, exactly.** If the destination is **Channel Pipeline** in the navigation, write *Channel Pipeline*, not *auto-creation* or *Auto-Create*. Terminology drift between docs and UI is a usability bug. The Tech Writer and UX Designer own consistency jointly. The DBAS feature is labelled **Backup & Restore** in the UI, per UX grooming, and should be called Backup & Restore in user-facing docs (the acronym DBAS only appears in dev docs and the threat model).
- **Cross-link, don't duplicate.** If the developer reference for a feature already exists (e.g., `docs/normalization.md#developer-reference`, `docs/template_engine.md`), link to it from a "Going deeper" section rather than copying material.
- **Screenshots live in `docs/images/user_guide/<section>/`.** See [Screenshot conventions](#screenshot-conventions) below for the full spec (viewport, theme, filenames, placement).
- **Show the result.** Where a workflow has a verifiable end state (a new channel exists, a backup file appears, a setting takes effect), say what the user will see. "It works" is not a verification step. See `docs/_shared/engineering-discipline.md` style "Verification of Completion."
- **Stub before article.** Every section in this scaffold ships as a stub (purpose, audience, placeholder TOC). The actual articles are filed as separate beads and written in their own PRs. This keeps user-facing content reviewable in small chunks and lets each article be evaluated by both UX (for the user model) and Tech Writer (for clarity).

## What must never appear in published guide pages

These are PO-ruled hard exclusions, not style preferences. If you're drafting or reviewing an article and one of these shows up, cut it before the PR opens.

- **No internal tracking artifacts.** ADR numbers, bead ids, epic ids, and `Status:`/tracking banners never appear in published pages. Coverage tracking lives in this README — see [Planned-article backlog](#planned-article-backlog-moved-from-published-indexes) — not in the article itself.
- **No source files or code symbols.** No `backend/…`/`frontend/…` paths, function/class names, or code constants. State the behavior, not where it lives in the codebase. Dev notes belong in the dev docs, not here.
- **No raw API endpoints as workarounds.** If a capability has no UI, say so plainly ("there is currently no way to do this from the web UI") instead of citing the endpoint or an MCP tool as the escape hatch. The gap statement is intentional — it keeps missing UI visible as a product problem instead of quietly papering over it with a curl command.
- **Explicit keep-exceptions (PO-ruled).** The exclusions above are not absolute. These stay: documented operator verification commands (the health-check curls); URLs operators paste as configuration (feeds, webhooks); log-line examples that show `/api/` paths as illustrative output; operator-invoked CLI scripts and runtime paths (`reset_password.py`, `/config/backups/`, `journal.db`); third-party APIs described for integration context (e.g. Emby's); the MCP integration articles and dedicated MCP tool-name sections that document that shipped feature; genuine automation alternatives where a fully functional UI already exists (error-telemetry's "Via the API"); and safety-critical migration/data-rescue commands that are the only documented procedure (lookup-tables-retired's export curls).
- **Cross-references leave the guide as links to the public GitHub repo** (`blob/main` for files, `tree/main` for directories), never as relative links into excluded docs. Article tables in section indexes use nav titles, never filenames.

## Per-destination tutorial template

Every ECM primary destination gets a "Common tasks" tutorial article that follows the
authoring conventions above, formalized into one literal, copyable skeleton.
It distills the pattern already in production in
[`backup-restore/index.md`](backup-restore/index.md) (the "Start here"
task-router) and [`integrations/index.md`](integrations/index.md) /
[`integrations/emby.md`](integrations/emby.md) (goal-first setup steps with
an explicit end state). Read those two before writing a new destination tutorial.

```markdown
# <Destination Name>

<Destination Name> is for <2 sentences max: what this page is for, no more>. <If the destination has an access requirement, e.g. admin-only, say so here in one plain sentence; otherwise, omit.>

## Common tasks

### <Operator's goal, phrased as an action: "Merge two duplicate channels", not "Merge Duplicates feature">

1. <Step>
2. <Step>
3. <Step>

**Result:** <what the operator sees when it worked: the verifiable end state>

### <Next goal>

1. <Step>
2. <Step>

**Result:** <what the operator sees when it worked>

## Going deeper

- [<link text>](<relative path>): <why an operator would follow this>
```

Notes on filling in the skeleton:

- **Title**: the exact in-UI destination label (see [Authoring conventions](#authoring-conventions): "Use the in-UI label, exactly").
- **"What this page is for"**: two sentences maximum, no more. This is orientation, not documentation. The `## Common tasks` walkthroughs carry the actual content. No audience blockquote. The guide is operator-first by default (see [Audience model](#audience-model)). Only add a plain sentence here if the destination has an access requirement (e.g., admin-only) or serves the rare non-operator audience.
- **`## Common tasks`**: one `###` subsection per goal. Head each subsection with the goal phrased as the operator's action ("Find and merge duplicate channels"), never as a UI-element name ("The Find Duplicates Button"). Steps are numbered. Every walkthrough ends with a **Result:** line. This is the "Show the result" rule made literal and mandatory for tutorial articles specifically (existing non-tutorial articles, like the DBAS reference pages, apply "Show the result" more loosely).
- **`## Going deeper`**: cross-links only, per "Cross-link, don't duplicate." Link to the developer reference, the API doc section, or a sibling article, but never re-explain what's already written elsewhere.

## Screenshot conventions

Screenshots are captured to a fixed spec so they're comparable across
articles and reproducible by whoever writes the next tutorial. This
formalizes the pattern already used by
[`docs/images/event_sync/`](../images/event_sync/) (see the numbered
`1-kind-chooser.png`-style filenames referenced from
[`docs/event_sync.md`](../event_sync.md)) as the repo-wide convention for
`docs/user_guide/` and beyond.

- **Viewport: 1280×720.** The same fixed size Playwright's visual-regression
  suite already pins for deterministic screenshots
  (`e2e/visual-regression.spec.ts`). Don't capture at your browser's ambient
  window size. Resize to 1280×720 first.
- **Theme: dark.** ECM's default theme. Capture in dark unless the article is
  specifically documenting the light/dark toggle itself.
- **Filenames: numbered kebab-case, `<n>-<short-name>.png`.** The number
  reflects capture/appearance order within the article (`1-kind-chooser.png`,
  `2-editor-master-guidance.png`, …), matching the `docs/images/event_sync/`
  set.
- **Location: `docs/images/user_guide/<section>/`.** One image directory per
  user-guide section, matching the section's directory name under
  `docs/user_guide/`.
- **Placement: inline, immediately after the step it illustrates.** Never a
  screenshot dump at the end of the article. A screenshot belongs directly
  under the numbered step (or `###` goal) it shows the result of, the same
  way `docs/event_sync.md` interleaves its `images/event_sync/*.png`
  references between steps.
- **Grandfathering: `docs/images/normalization/` is exempt.** Those two
  images predate this convention and are not being recaptured to match it.
  New normalization-section screenshots follow this spec; the existing two
  are left as-is.

### Capture mechanics

Capture screenshots with Playwright against the dev container
(`ecm-ecm-1`), using representative seeded data (real-shaped channel
groups, streams, and rules, not an empty instance) so the screenshot
shows what an operator's screen actually looks like. Capture during the
same writing pass as the tutorial bead that needs the image, not as a
separate follow-up pass. An article and its screenshots ship in the same
PR.

## Information architecture

The top-level structure follows the user's growth curve from "first run" to "power user," not the application's internal module layout. A new operator should be able to read top-to-bottom and onboard themselves; a returning operator should be able to jump directly to a section.

```
docs/user_guide/
├── README.md                     ← you are here (contributors only)
├── index.md                      ← landing page + nav for users
├── getting-started/              ← first-run, install, Dispatcharr connect
├── channels-streams/             ← day-to-day channel & stream management
├── m3u-manager/                  ← add/refresh/configure provider playlists
├── m3u-changes/                  ← read-only log of provider playlist changes
├── channel-pipeline/             ← rule authoring, conditions/actions, bulk ops
├── normalization/                ← naming patterns, apply-to-channels flow
├── epg/                          ← EPG sources, dummy EPG templates
├── guide/                        ← the TV-guide-style programming grid
├── logo-manager/                 ← channel artwork library
├── notifications/                ← SMTP/Discord/Telegram scheduled-task alerts
├── stats/                        ← Stats page (Stats v2, v0.17.0)
├── journal/                      ← forensic record of channel/EPG/M3U changes
├── integrations/                 ← Emby/Plex/Jellyfin + MCP connection reference
├── backup-restore/               ← Backup & Restore (bd-0i2vt epic)
├── settings/                     ← Settings drill-in navigation, six groups
└── troubleshooting/              ← common issues, log inspection, support
```

This tree reflects the actual current filesystem under `docs/user_guide/`.
Every directory listed above exists and has its own `index.md` (section
landing); each accumulates per-article files as downstream beads ship.

### Planned sections (bd-gsnw0)

The `gsnw0` per-destination tutorial epic scoped six additional tutorials.
All six have since shipped: `m3u-manager/`, `m3u-changes/`, `guide/`,
`logo-manager/`, `journal/`, and `settings/` all now exist in the tree above,
so there is nothing left to list here. Naming matches `index.md`'s ["By
workspace destination"](index.md#by-workspace-destination) table.

The next bead that scopes a section not yet scaffolded should list it here,
then move it into the tree above once its directory lands on disk.

### Why this order

1. **Getting started**: nobody can do anything else until ECM can talk to Dispatcharr.
2. **Channels & streams**: the core entity model. Everything else mutates these.
3. **Channel Pipeline**: the first power feature an operator graduates into.
4. **Normalization**: typically discovered when the Channel Pipeline produces names you don't like.
5. **EPG**: needed once channels exist, but not blocking initial setup.
6. **Stats**: observability of what ECM is doing. Useful but not on the critical path.
7. **Backup & Restore**: disaster recovery. Critical, but read once and rarely.
8. **Troubleshooting**: referenced from every other section when things go wrong.

## Adding a new article

1. Create or claim a bead with a clear, task-oriented title (e.g., "Document how to clone a Channel Pipeline rule").
2. Drop the new file under the relevant section directory. Filename matches the article title in kebab-case: `clone-a-channel-pipeline-rule.md`.
3. Update that section's `index.md` to link the new article and place it in the appropriate sub-section of the section TOC.
4. If the article introduces a new screenshot, save it under `docs/images/user_guide/<section>/` and reference with a relative path.
5. Open a PR. Request review from both the Tech Writer (clarity, structure, terminology consistency) and the UX Designer (does the article match the user's mental model and the in-UI labels?).
6. Update `docs/user_guide/index.md` only if your article changes the section landing: individual article links live in section indexes, not the top-level index.

## Cross-references

Existing docs that complement (and are linked from) the user guide:

| User guide section | Complements / links to |
|-|-|
| getting-started | `README.md` (project root), `docs/architecture.md` (system overview, optional reading) |
| channels-streams | `docs/api.md` (when an operator wants the API behind a UI action) |
| channel-pipeline | `docs/api.md` (Channel Pipeline router), eventual `analyze-rules` skill output |
| normalization | `docs/normalization.md` (the existing dual-audience guide; user guide section is a thinner, task-first wrapper that defers to the deep reference) |
| epg | `docs/template_engine.md` (dummy EPG template syntax) |
| stats | `docs/sre/slos.md` (operators curious about the SLO framing of what they see) |
| backup-restore | `docs/security/threat_model_dbas_import.md` (operators evaluating restore safety; deliberately surfaced because import is a high-impact operation) |
| troubleshooting | `docs/runbooks/` (when an operator's troubleshooting escalates into an on-call scenario, point them at the runbook) |

## Out of scope for this scaffolding PR

- Writing the actual articles. Each section ships as a stub. Articles are individual downstream beads.
- Building a docs site / static-site generator. ECM docs are read from the repo today; if a docs site is ever introduced, the IA here is the authoritative source for nav structure.
- Localising the docs. English-only for now; if i18n is added later, the file structure will accommodate it without restructuring the IA.

## Planned-article backlog (moved from published indexes)

The tables below used to live under a "Planned articles" heading in the
published section indexes. That leaked internal authoring-tracker content to
operators, so they were removed from the public guide (enhancedchannelmanager-qq0na)
and relocated here verbatim. This is the writers' backlog, not
reader-facing content — do not restore these tables to the published
`index.md` files.

**Note:** at the time of this move, every filename below already exists as a
written file on disk in its section directory, but none of them are linked
from their section's index or listed in `mkdocs.yml`'s `nav:`, so they are
unreachable from the published site. That's a separate stale-tracker problem
from the one this move fixes — see the PO decision flagged in the
enhancedchannelmanager-qq0na close-out before wiring any of these in.

### Channels & Streams

| Article | Purpose |
|-|-|
| `streams-overview.md` | The Streams pane: what a stream is, where it came from (M3U source), and how it relates to a channel. |
| `assign-streams-to-channels.md` | The matching workflow: manual assignment, the impact of normalization on auto-matching, what happens when a stream's source moves. |
| `channel-groups-and-tags.md` | When to use channel groups vs. tags, how Dispatcharr consumes them, ordering semantics. |
| `the-journal.md` | The Journal page: what changes ECM records, how to filter by entity, how to find the change that broke something. |
| `logos.md` | The Logo Manager: uploading logos, where they're stored, how Dispatcharr picks them up. |

### Getting Started

| Article | Purpose |
|-|-|
| `verify-healthy-connection.md` | What a healthy connection looks like (channels visible, streams visible, no banner warnings), plus the `/health` endpoint as the operator-friendly readiness check. This is currently covered inline in `connect-dispatcharr.md`'s "Confirm the connection is healthy" section; a dedicated article may be split out later. |

### Normalization

| Article | Purpose |
|-|-|
| `concepts.md` | What normalization is and isn't, in operator language. The three places it runs (Test Rules, Auto Create, Apply to existing channels) and why they must agree. Quick pointer to the parity contract for operators who care. |
| `author-your-first-rule.md` | Walk-through: open Settings → Normalization Rules, add a rule, preview it in Test Rules, save it. Includes the "iterate before saving" discipline. |
| `rule-groups-and-ordering.md` | Why groups exist, group priority vs. rule priority, the pipeline semantics (each rule sees the previous rule's output). |
| `condition-and-action-types.md` | Tour of the available condition types (regex, prefix, contains, etc.) and action types (replace, strip, lowercase, etc.) with short examples. |
| `apply-to-existing-channels.md` | The one-time bulk rewrite flow: when to use it, what gets changed, undo/safety notes, expected duration on a large library. |
| `when-things-look-wrong.md` | "Test Rules and Auto Create disagree": what that means, why it's a bug not a configuration issue, and the path to escalation (link to the canary-divergence runbook). |

### Notifications & Alert Methods

This index covers the workflow end-to-end. As the surface grows, the following deeper articles will be split off:

| Article | Purpose |
|-|-|
| `email-recipients-deep-dive.md` | RFC 5322 edge cases, paste normalization rules, the Alert Methods data model behind the scenes, migrating from older free-text recipient fields. |
| `discord-webhook-customization.md` | Embed formatting, mentioning roles in alerts, channel routing strategies if you outgrow a single shared webhook. |
| `telegram-bot-setup.md` | Step-by-step BotFather walk-through with screenshots, locating chat IDs in groups vs. channels vs. supergroups, bot privacy mode caveats. |
| `alert-routing-patterns.md` | Worked examples: "send only errors to Discord, all severities to email," "info alerts for one task only," etc. |

### Troubleshooting

| Article | Purpose |
|-|-|
| `common-issues.md` | Top failure modes by category (connection, Channel Pipeline, normalization, EPG, restore), with the first-three-things-to-check for each. |
| `read-the-logs.md` | Where ECM logs to, what severity levels mean, how to grep effectively, the `[SAFE_REGEX]` and other tagged messages an operator might encounter. Cross-references the `logs` skill. |
| `ui-banners-and-warnings.md` | Catalogue of the warning banners ECM may surface and what each one means. |
| `gather-support-information.md` | What to capture before asking for help: version (`docs/versioning.md` for context), recent journal entries, relevant log slice, Dispatcharr version, browser if it's a UI bug. Focused on making the support loop short. |
| `escalation-paths.md` | Where to ask for help: Discord, GitHub issues, and (for self-hosted operators with on-call) the runbooks tree. |
| `recovery-patterns.md` | "I made a change I want to undo": the journal, undo/redo, restore from backup, when to use which. |
