# User Guide — Contributing & Architecture

> Information architecture and authoring conventions for `docs/user_guide/`. Read this before adding a new article or restructuring a section.

This README is **not** a user-facing document. It explains how the user guide is organised and how to add to it. End users land on `docs/user_guide/index.md`.

## Why this exists

ECM has rich developer-facing documentation (`docs/architecture.md`, `docs/api.md`, `docs/normalization.md` developer reference, the `docs/runbooks/` set, `docs/sre/slos.md`, etc.) but no consolidated **user-facing** documentation tree. Operators and end users have historically pieced features together from in-app text, release notes, and the dual-audience halves of the dev docs.

`docs/user_guide/` fills that gap. It is task-oriented documentation for the people who **use** ECM, not the people who build it.

Scaffolded in bd-f1wnt; first user-facing feature it unblocks is bd-gb5r5.3 (DBAS / Backup & Restore end-user docs).

## Audience model

We design the IA around two distinct user types. Every article should declare which audience it is for in its frontmatter / opening sentence.

| Audience | Who they are | What they need from docs |
|-|-|-|
| **Operator** | The person who installed ECM, manages the Dispatcharr connection, configures rules, runs backups, troubleshoots failures. Often the same person who runs Dispatcharr. Comfortable with Docker, log inspection, and YAML-ish config. | Setup, configuration, recovery, and "how does this feature work end-to-end" reference. Can handle terminology like *task engine*, *normalization policy*, *idempotent*. |
| **End user** | The household member or downstream consumer watching the streams ECM produces. Rarely opens the ECM UI. Cares about "the channel I watch is gone" or "the EPG is wrong." | Almost nothing — but a small surface (e.g., the public stats page, if one is exposed) needs plain-language framing. Most end-user concerns are surfaced through the operator. |

Today the user guide is **operator-first**. End-user content is rare and clearly labelled when it appears. If you're not sure which audience an article serves, ask the Tech Writer in standup before drafting.

## Boundaries — what belongs here vs. elsewhere

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
- **Open with the audience and the outcome.** First sentence: who this article is for and what they will be able to do when they finish it.
- **Use the in-UI label, exactly.** If the destination is **Channel Pipeline** in the navigation, write *Channel Pipeline*, not *auto-creation* or *Auto-Create*. Terminology drift between docs and UI is a usability bug — the Tech Writer and UX Designer own consistency jointly. The DBAS feature is labelled **Backup & Restore** in the UI, per UX grooming, and should be called Backup & Restore in user-facing docs (the acronym DBAS only appears in dev docs and the threat model).
- **Cross-link, don't duplicate.** If the developer reference for a feature already exists (e.g., `docs/normalization.md#developer-reference`, `docs/template_engine.md`), link to it from a "Going deeper" section rather than copying material.
- **Screenshots live in `docs/images/user_guide/<section>/`.** See [Screenshot conventions](#screenshot-conventions) below for the full spec (viewport, theme, filenames, placement).
- **Show the result.** Where a workflow has a verifiable end state (a new channel exists, a backup file appears, a setting takes effect), say what the user will see. "It works" is not a verification step — see `docs/_shared/engineering-discipline.md` style "Verification of Completion."
- **Stub before article.** Every section in this scaffold ships as a stub (purpose, audience, placeholder TOC). The actual articles are filed as separate beads and written in their own PRs. This keeps user-facing content reviewable in small chunks and lets each article be evaluated by both UX (for the user model) and Tech Writer (for clarity).

## Per-destination tutorial template

Every ECM primary destination gets a "Common tasks" tutorial article that follows the
authoring conventions above, formalized into one literal, copyable skeleton.
It distills the pattern already in production in
[`backup-restore/index.md`](backup-restore/index.md) (the "Start here"
task-router) and [`integrations/index.md`](integrations/index.md) /
[`integrations/emby.md`](integrations/emby.md) (goal-first setup steps with
an explicit end state) — read those two before writing a new destination tutorial.

```markdown
# <Destination Name>

> **Audience:** <one sentence — who reads this and what they walk away able to do>

<Destination Name> is for <2 sentences max — what this page is for, no more>.

## Common tasks

### <Operator's goal, phrased as an action — "Merge two duplicate channels", not "Merge Duplicates feature">

1. <Step>
2. <Step>
3. <Step>

**Result:** <what the operator sees when it worked — the verifiable end state>

### <Next goal>

1. <Step>
2. <Step>

**Result:** <what the operator sees when it worked>

## Going deeper

- [<link text>](<relative path>) — <why an operator would follow this>
```

Notes on filling in the skeleton:

- **Title** — the exact in-UI destination label (see [Authoring conventions](#authoring-conventions) — "Use the in-UI label, exactly").
- **Audience blockquote** — one sentence, same voice as `backup-restore/index.md`'s `> **Audience:**` line. Don't restate the destination name; say who the reader is and what they need.
- **"What this page is for"** — two sentences maximum, no more. This is orientation, not documentation — the `## Common tasks` walkthroughs carry the actual content.
- **`## Common tasks`** — one `###` subsection per goal. Head each subsection with the goal phrased as the operator's action ("Find and merge duplicate channels"), never as a UI-element name ("The Find Duplicates Button"). Steps are numbered. Every walkthrough ends with a **Result:** line — this is the "Show the result" rule made literal and mandatory for tutorial articles specifically (existing non-tutorial articles, like the DBAS reference pages, apply "Show the result" more loosely).
- **`## Going deeper`** — cross-links only, per "Cross-link, don't duplicate." Link to the developer reference, the API doc section, or a sibling article — never re-explain what's already written elsewhere.

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
  window size — resize to 1280×720 first.
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
  screenshot dump at the end of the article — a screenshot belongs directly
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
groups, streams, and rules — not an empty instance) so the screenshot
shows what an operator's screen actually looks like. Capture during the
same writing pass as the tutorial bead that needs the image, not as a
separate follow-up pass — an article and its screenshots ship in the same
PR.

## Information architecture

The top-level structure follows the user's growth curve from "first run" to "power user," not the application's internal module layout. A new operator should be able to read top-to-bottom and onboard themselves; a returning operator should be able to jump directly to a section.

```
docs/user_guide/
├── README.md                     ← you are here (contributors only)
├── index.md                      ← landing page + nav for users
├── getting-started/              ← first-run, install, Dispatcharr connect
├── channels-streams/             ← day-to-day channel & stream management
├── channel-pipeline/             ← rule authoring, conditions/actions, bulk ops
├── normalization/                ← naming patterns, apply-to-channels flow
├── epg/                          ← EPG sources, dummy EPG templates
├── notifications/                ← SMTP/Discord/Telegram scheduled-task alerts
├── stats/                        ← Stats page (Stats v2, v0.17.0)
├── integrations/                 ← Emby/Plex/Jellyfin + MCP connection reference
├── backup-restore/               ← Backup & Restore (bd-0i2vt epic)
└── troubleshooting/              ← common issues, log inspection, support
```

This tree reflects the actual current filesystem under `docs/user_guide/` —
every directory listed above exists and has its own `index.md` (section
landing); each accumulates per-article files as downstream beads ship.

### Planned sections (bd-gsnw0)

The `gsnw0` per-destination tutorial epic scopes six additional tutorials that
have not been scaffolded yet — no directory exists for them today. Listed
here rather than in the tree above so the tree stays an accurate map of what
currently exists on disk. Naming and status match `index.md`'s
["By workspace destination"](index.md#by-workspace-destination) table:

- `m3u-manager/` — M3U Manager tutorials — **Planned**
- `guide/` — Guide tutorials — **Planned**
- `logo-manager/` — Logo Manager tutorials — **Planned**
- `m3u-changes/` — M3U Changes tutorials — **Planned**
- `journal/` — Journal tutorials — **Planned**
- `settings/` — Settings tutorials — **Planned**

Once a bead scaffolds one of these, move it from this list into the tree
above.

### Why this order

1. **Getting started** — nobody can do anything else until ECM can talk to Dispatcharr.
2. **Channels & streams** — the core entity model. Everything else mutates these.
3. **Channel Pipeline** — the first power feature an operator graduates into.
4. **Normalization** — typically discovered when the Channel Pipeline produces names you don't like.
5. **EPG** — needed once channels exist, but not blocking initial setup.
6. **Stats** — observability of what ECM is doing. Useful but not on the critical path.
7. **Backup & Restore** — disaster recovery. Critical, but read once and rarely.
8. **Troubleshooting** — referenced from every other section when things go wrong.

## Adding a new article

1. Create or claim a bead with a clear, task-oriented title (e.g., "Document how to clone a Channel Pipeline rule").
2. Drop the new file under the relevant section directory. Filename matches the article title in kebab-case: `clone-a-channel-pipeline-rule.md`.
3. Update that section's `index.md` to link the new article and place it in the appropriate sub-section of the section TOC.
4. If the article introduces a new screenshot, save it under `docs/images/user_guide/<section>/` and reference with a relative path.
5. Open a PR. Request review from both the Tech Writer (clarity, structure, terminology consistency) and the UX Designer (does the article match the user's mental model and the in-UI labels?).
6. Update `docs/user_guide/index.md` only if your article changes the section landing — individual article links live in section indexes, not the top-level index.

## Cross-references

Existing docs that complement (and are linked from) the user guide:

| User guide section | Complements / links to |
|-|-|
| getting-started | `README.md` (project root), `docs/architecture.md` (system overview, optional reading) |
| channels-streams | `docs/api.md` (when an operator wants the API behind a UI action) |
| channel-pipeline | `docs/api.md` (Channel Pipeline router), eventual `analyze-rules` skill output |
| normalization | `docs/normalization.md` (the existing dual-audience guide — user guide section is a thinner, task-first wrapper that defers to the deep reference) |
| epg | `docs/template_engine.md` (dummy EPG template syntax) |
| stats | `docs/sre/slos.md` (operators curious about the SLO framing of what they see) |
| backup-restore | `docs/security/threat_model_dbas_import.md` (operators evaluating restore safety; deliberately surfaced because import is a high-impact operation) |
| troubleshooting | `docs/runbooks/` (when an operator's troubleshooting escalates into an on-call scenario, point them at the runbook) |

## Out of scope for this scaffolding PR

- Writing the actual articles. Each section ships as a stub. Articles are individual downstream beads.
- Building a docs site / static-site generator. ECM docs are read from the repo today; if a docs site is ever introduced, the IA here is the authoritative source for nav structure.
- Localising the docs. English-only for now; if i18n is added later, the file structure will accommodate it without restructuring the IA.
