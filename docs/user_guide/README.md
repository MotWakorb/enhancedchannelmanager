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
| "How do I do X in the UI?" / "What does this rule type do?" / "Why did the auto-creation skip this stream?" | `docs/user_guide/` |
| HTTP method, path, request/response schema, error codes for an API endpoint | `docs/api.md` (the API reference, generated/maintained alongside the OpenAPI spec) |
| "How do I deploy ECM in a container?" / "How does the request flow work?" | `docs/architecture.md`, `docs/project_architecture.md`, `docs/backend_architecture.md` |
| "I got paged at 3 AM, what do I do?" | `docs/runbooks/` (operator-adjacent but written for the on-call responder under pressure, not the configuring operator) |
| Rule authoring + the developer reference for the engine | `docs/normalization.md` is a deliberately dual-audience document and stays that way; the user guide cross-links to it rather than duplicating |
| Database migrations, pytest conventions, frontend lint policy | The existing `docs/*.md` files referenced from CLAUDE.md |

The rule of thumb: if the audience is "someone trying to **use** ECM to manage their channels," it goes here. If the audience is "someone trying to **build, integrate with, deploy, or recover** ECM," it goes in the dev-facing tree.

## Authoring conventions

- **Task-oriented titles.** "Connect ECM to Dispatcharr" beats "Dispatcharr Connection Settings." Verb-first. The reader is trying to do something.
- **Open with the audience and the outcome.** First sentence: who this article is for and what they will be able to do when they finish it.
- **Use the in-UI label, exactly.** If the tab is "Auto Creation" in the navigation, write *Auto Creation*, not *auto-creation* or *Auto-Create*. Terminology drift between docs and UI is a usability bug — the Tech Writer and UX Designer own consistency jointly. The DBAS feature is labelled **Backup & Restore** in the UI, per UX grooming, and should be called Backup & Restore in user-facing docs (the acronym DBAS only appears in dev docs and the threat model).
- **Cross-link, don't duplicate.** If the developer reference for a feature already exists (e.g., `docs/normalization.md#developer-reference`, `docs/template_engine.md`), link to it from a "Going deeper" section rather than copying material.
- **Screenshots live in `docs/images/user_guide/<section>/`.** Match the existing convention used by `docs/images/normalization/`. Refer to `docs/css_guidelines.md` if you need to take screenshots of UI surfaces with custom theming.
- **Show the result.** Where a workflow has a verifiable end state (a new channel exists, a backup file appears, a setting takes effect), say what the user will see. "It works" is not a verification step — see `docs/_shared/engineering-discipline.md` style "Verification of Completion."
- **Stub before article.** Every section in this scaffold ships as a stub (purpose, audience, placeholder TOC). The actual articles are filed as separate beads and written in their own PRs. This keeps user-facing content reviewable in small chunks and lets each article be evaluated by both UX (for the user model) and Tech Writer (for clarity).

## Information architecture

The top-level structure follows the user's growth curve from "first run" to "power user," not the application's internal module layout. A new operator should be able to read top-to-bottom and onboard themselves; a returning operator should be able to jump directly to a section.

```
docs/user_guide/
├── README.md                     ← you are here (contributors only)
├── index.md                      ← landing page + nav for users
├── getting-started/              ← first-run, install, Dispatcharr connect
├── channels-streams/             ← day-to-day channel & stream management
├── auto-creation/                ← rule authoring, conditions/actions, bulk ops
├── normalization/                ← naming patterns, apply-to-channels flow
├── epg/                          ← EPG sources, dummy EPG templates
├── stats/                        ← Stats tab (placeholder; bd-skqln.9)
├── backup-restore/               ← Backup & Restore (placeholder; bd-0i2vt epic)
└── troubleshooting/              ← common issues, log inspection, support
```

Each subdirectory has its own `index.md` (section landing) and will accumulate per-article files as downstream beads ship.

### Why this order

1. **Getting started** — nobody can do anything else until ECM can talk to Dispatcharr.
2. **Channels & streams** — the core entity model. Everything else mutates these.
3. **Auto-creation** — the first power feature an operator graduates into.
4. **Normalization** — typically discovered when auto-creation produces names you don't like.
5. **EPG** — needed once channels exist, but not blocking initial setup.
6. **Stats** — observability of what ECM is doing. Useful but not on the critical path.
7. **Backup & Restore** — disaster recovery. Critical, but read once and rarely.
8. **Troubleshooting** — referenced from every other section when things go wrong.

## Adding a new article

1. Create or claim a bead with a clear, task-oriented title (e.g., "Document how to clone an auto-creation rule").
2. Drop the new file under the relevant section directory. Filename matches the article title in kebab-case: `clone-an-auto-creation-rule.md`.
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
| auto-creation | `docs/api.md` (auto-creation router), eventual `analyze-rules` skill output |
| normalization | `docs/normalization.md` (the existing dual-audience guide — user guide section is a thinner, task-first wrapper that defers to the deep reference) |
| epg | `docs/template_engine.md` (dummy EPG template syntax) |
| stats | `docs/sre/slos.md` (operators curious about the SLO framing of what they see) |
| backup-restore | `docs/security/threat_model_dbas_import.md` (operators evaluating restore safety; deliberately surfaced because import is a high-impact operation) |
| troubleshooting | `docs/runbooks/` (when an operator's troubleshooting escalates into an on-call scenario, point them at the runbook) |

## Out of scope for this scaffolding PR

- Writing the actual articles. Each section ships as a stub. Articles are individual downstream beads.
- Building a docs site / static-site generator. ECM docs are read from the repo today; if a docs site is ever introduced, the IA here is the authoritative source for nav structure.
- Localising the docs. English-only for now; if i18n is added later, the file structure will accommodate it without restructuring the IA.
