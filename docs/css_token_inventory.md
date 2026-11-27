# CSS Custom Property Inventory

Revision: `origin/dev` `18af2df0397e71908a02a843d9131f680bdcc822`, plus the
fixes listed under "Fixed". Method: `frontend/src/cssAudits/cssTokenResolution.ts`
run over every non-test `.css`, `.ts` and `.tsx` file under `frontend/src`
(407 files, 132 of them CSS), comments stripped first, every `var(--x ...)`
occurrence counted (not lines). The contract is in
`docs/css_guidelines.md` ("Custom property resolution contract"). Earlier
counts (48/223/63, 45/210/59) were not reused.

## Counts at this revision

| Measure | Before the fixes | After |
|-|-|-|
| `var()` occurrences | 8813 (8654 CSS, 159 TSX) | 8813 |
| Distinct tokens referenced | 181 incl. fallback-nested | 168 |
| Distinct tokens with an unresolved reference | 46 | 33 |
| References unresolved in some theme | 241 | 160 |
| of which no fallback (declaration dropped) | 81 | 0 |
| of which fallback-masked | 160 | 160 |
| Tokens declared at root scope | 134, every one in all three themes | same |
| Tokens declared only on a component selector or inline | 28 | same |

Per-theme result: every one of the 134 root-scope tokens is defined in dark,
light and high-contrast. No token is defined in only some themes, so there is
currently no theme-only definition. All 241 unresolved references were
unresolved in all three themes (the token is declared in none). The theme-only
case is covered by the guard's fixtures, not by a live instance.

## Classification

Classes: **mistaken** (the intended token exists), **needed definition** (no
existing token carries the meaning), **unused declaration**. Undefined tokens
cannot be unused declarations; the third class applies to declared tokens and
is reported separately below.

### Fixed this change: 16 tokens, 81 no-fallback references, all mistaken

Render and cascade usage was read at each site (what property, which
element, which theme owns the replacement). Every replacement is defined in
`:root` and in both theme overrides.

| Token | Refs | Replaced by | Visual effect |
|-|-|-|-|
| `--border-color` | 46 | `--border-primary` | Border or divider that was absent now draws (shorthand `border` was dropped whole) |
| `--card-bg` | 15 | `--bg-secondary` | Card/section background that was transparent is now opaque `--bg-secondary` |
| `--text-tertiary` | 3 | `--text-muted` | Text that inherited its parent colour is now `--text-muted` |
| `--accent-primary-transparent` | 3 | `--bg-selected` | Selected/hover rule background that was transparent now tints |
| `--button-primary` | 2 | `--button-primary-bg` | Active log-filter chip gets a background and border |
| `--accent` | 2 | `--accent-primary` | Group-clear text button colour; scheduled-task group checkbox accent |
| `--status-success-text` | 1 | `--success` | Matched-preview glyph colour |
| `--status-error-text`, `--status-error-bg` | 1 + 1 | `--danger-text`, `--danger-bg` | Danger icon-button hover gains bg and colour |
| `--danger` | 1 | `--danger-text` | Probe-on-sort error hint colour |
| `--color-primary`, `--color-primary-text` | 1 + 1 | `--button-primary-bg`, `--button-primary-text` | Active top-watched toggle gains fill and text colour |
| `--button-secondary-bg` | 1 | `--button-bg` | Channel-pane toggle button background |
| `--border-hover` | 1 | `--border-secondary` | Conflict item hover border |
| `--accent-success`, `--accent-error` | 1 + 1 | `--success`, `--danger-text` | Integration test result colours |

### Fallback-masked, deferred: 33 tokens, 160 references

Each resolves today to its literal fallback in every theme, so the colour
does not follow the theme (light and high-contrast show the dark-era literal).
Changing them changes light and high-contrast output across 91 file and token pairs, so
they are baselined in `cssTokenFallbackBaseline.ts` (91 file/token entries)
and not rewritten here. Recommended remedy per token:

| Token | Refs | Files | Class | Intended token / action |
|-|-|-|-|-|
| `--border-color` | 49 | 23 | mistaken | `--border-primary` |
| `--error-color` | 25 | 8 | mistaken | `--danger-text` (text/border), `--danger-bg` or `--error` for fills |
| `--error-text` | 10 | 5 | mistaken | `--danger-text` |
| `--status-error` | 8 | 2 | mistaken | `--danger-text` |
| `--status-warning` | 7 | 2 | mistaken | `--warning-text` / `--warning` |
| `--accent-color` | 5 | 3 | mistaken | `--accent-primary` (not for backgrounds, see guidelines) |
| `--accent-primary-rgb` | 4 | 3 | needed definition | `rgba(var(--x), a)` has no existing token; define per theme, or use `--accent-N` |
| `--accent` | 4 | 4 | mistaken | `--accent-primary` |
| `--status-success` | 4 | 2 | mistaken | `--success` |
| `--border-warning` | 4 | 3 | needed definition | no semantic border tokens exist; add `--warning-border` family |
| `--text-tertiary` | 3 | 3 | mistaken | `--text-secondary` (fallback value) or `--text-muted` |
| `--info` | 3 | 3 | mistaken | `--info-text` |
| `--warning-border` | 3 | 3 | needed definition | as `--border-warning` (same meaning, two spellings) |
| `--error-border` | 3 | 3 | needed definition | semantic border family |
| `--bg-elevated` | 2 | 2 | needed definition | no elevated surface token |
| `--warning-color` | 2 | 2 | mistaken | `--warning` / `--warning-text` |
| `--danger-border` | 2 | 2 | needed definition | semantic border family |
| `--button-primary-hover-bg` | 2 | 1 | needed definition | `--button-primary-bg` has no hover pair |
| `--font-mono` | 2 | 2 | needed definition | no monospace family token |
| `--text-warning` | 2 | 2 | mistaken | `--warning-text` |
| `--border-subtle` | 2 | 1 | mistaken | `--border-primary` |
| `--accent-green` | 2 | 1 | mistaken | `--success` |
| `--color-warning` | 2 | 1 | mistaken | `--warning` |
| `--info-border`, `--success-border` | 1 + 1 | 1 + 1 | needed definition | semantic border family |
| `--color-info` | 1 | 1 | mistaken | `--info-text` |
| `--success-color` | 1 | 1 | mistaken | `--success` |
| `--bg-warning-subtle` | 1 | 1 | mistaken | `--warning-bg` |
| `--bg-input` | 1 | 1 | mistaken | `--input-bg` |
| `--text-success` | 1 | 1 | mistaken | `--success` |
| `--border-success` | 1 | 1 | needed definition | semantic border family |
| `--bg-subtle` | 1 | 1 | mistaken | `--bg-tertiary` |
| `--accent-warning` | 1 | 1 | mistaken | `--warning` |

The "intended token" column is a recommendation from reading the fallback and
the property, not verified against the rendered result.

### Declared but not referenced through `var()`: 27, none removed

`--border-accent --input-border --input-focus-border --scrollbar-track
--scrollbar-thumb --scrollbar-thumb-hover --space-5 --space-6 --space-8
--space-10 --space-12 --space-16 --text-sm --text-base-sm --text-base
--text-md --text-lg --text-2xl --text-4xl --shadow-sm --z-sticky
--duration-fast --duration-normal --duration-slow --ease-default --ease-out
--ease-in-out`

No `var()` reference under `frontend/src`. That does not prove them dead:
consumers outside this scan (e2e specs, `getPropertyValue`, `backend/`
templates, scale tokens kept whole) were not checked. None is deleted.

### Element-local tokens: 28, accepted as seams

`--modal-*` (ModalBase theming seam), `--header-*`, `--split-pane-left`,
`--section-nav-available-height`, `--guide-channel-width`,
`--dashboard-accent`. Declared on a component selector or inline; the guard
cannot decide ancestry and accepts them.
