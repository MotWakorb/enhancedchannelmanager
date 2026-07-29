# CSS Guidelines

> This document is **authoritative for CSS** — naming, layer architecture,
> shared classes, modal patterns, theme variables. The general
> `docs/style_guide.md` summarises the rules that intersect with broader
> code style and points back here for the full catalog. If the two
> disagree, this document wins; please file a PR against the style guide
> so they are reconciled.

## Architecture Overview

CSS is organized in layers. Always use the highest-level shared class available before creating component-specific styles.

| Layer | File | Purpose |
|-------|------|---------|
| Design Tokens | `index.css` | CSS variables: spacing, radius, font-size, shadow, color |
| Common | `shared/common.css` | Buttons, forms, loading/error/empty states, badges, animations |
| Tab Loading | `App.css` | `.tab-loading` — full-page centered loading for tab-level screens |
| Settings | `SettingsTab.css` | `.settings-page-header`, `.settings-section`, `.checkbox-label` |
| Modals | `ModalBase.css` | `.modal-overlay`, `.modal-container`, `.modal-header/body/footer` |
| Component | `ComponentName.css` | Component-specific styles only |

## Golden Rule

**Never duplicate a style that already exists in common.css.** Before writing new CSS, check if a shared class already covers it.

## Typography

The content pane has a shared type scale, expressed as role tokens in
`frontend/src/index.css` (second `:root` block, group "Typography Roles").
A role names the *kind* of text, not its size, so a rule picks a role and
the number lives in one place.

| Role | Token prefix | Size | Weight | Line-height | Other | Used for |
|-|-|-|-|-|-|-|
| Page title | `--type-page-title-*` | 20px (`--text-3xl`) | 700 | 1.3 | uppercase (from the string, not CSS) | the route title, e.g. `OPERATIONS / M3U MANAGER` |
| Metric | `--type-metric-*` | 20px (`--text-3xl`) | 600 | 1.2 | — | the headline number on a stat/summary tile |
| Section | `--type-section-*` | 15px | 600 | 1.3 | — | a heading inside a page |
| Body | `--type-body-*` | 13px | 400 | 1.5 | — | running text, buttons, inputs, page descriptions |
| Item title | `--type-item-title-*` | 13px | 600 | 1.4 | — | the name of a row in a list |
| Meta | `--type-meta-*` | 11px (`--text-xs`) | 400 | 1.5 | — | supporting detail under an item title: type, URL, counts, timestamps |
| Micro | `--type-micro-*` | 10px (`--text-2xs`) | 700 | — | uppercase, tracking `0.08em` | column headers, the status word under a glyph |
| Badge | `--type-badge-*` | 10px (`--text-2xs`) | 500 | 1.4 | — | text inside a chip or pill |
| Label | `--type-label-*` | 13px | 500 | 1.4 | — | the caption on a form control |

Each role token points at the `--text-*` primitive that already carries its
number. 15px and 13px have no primitive, so those two are written literally
rather than adding primitives nothing else consumes.

Micro deliberately defines no line-height: micro labels take the line box of
whatever they sit in.

Page title and metric are both 20px. They are separate roles because they
separate on weight and character: the title is 700 and uppercase, the metric
is 600 and numeric, and the two never sit on the same line — the title is the
first line of the route header, the metric is inside a tile in the pane below.
Sizing them from one role would tie a later change to one of them to the
other.

Label and item title are both 13px for the same reason: a field caption reads
as part of its control, so it takes the button weight (500), while a list-row
name is the heaviest thing in its row (600). Bead
`enhancedchannelmanager-6z299` added the label role because `.form-group
label` — the one shared form treatment — had no role that fitted it.

### Icon sizes

| Token | Value | Used for |
|-|-|-|
| `--icon-status` | 18px | the status glyph in a list row, and inline notice icons |
| `--icon-action` | 16px | row action buttons, small inline indicators |
| `--icon-badge` | 14px | a glyph inside a chip or pill |
| `--icon-empty` | 64px | the illustration glyph in an empty or loading state |

Rail icons are chrome, not content, and keep their own 20px.

### Colour

Bind meta and micro text to `--text-secondary`. Do **not** use `--text-muted`
for them: in the light theme `--text-muted` measures 2.61:1 against
`--bg-secondary`, below the WCAG AA 4.5:1 floor.

`.micro-label` itself sets no colour — a micro label takes the colour of the
thing it labels (a status word is green or red; a column header is
`--text-secondary`, set on `.list-header`).

Two deliberate exemptions. `::placeholder` rules keep `--text-muted` —
placeholder contrast is a separate question and was not bundled into the type
sweep. Purely decorative non-text glyphs keep it too: `.empty-state
.material-icons` is a 64px illustration, not text, so the 4.5:1 text floor
does not apply to it.

### What is *not* in this scale

The rail and the top band are chrome and are frozen outside it: rail nav
label 14px / rail icon 20px / rail width 244px, header band 45px with 28px
controls.

The route page title used to be listed here too, frozen at 24px / 700. Bead
`enhancedchannelmanager-tygwm` moved it onto the scale at 20px — it sits
inside the content column, above the pane it names, and at 24px it was larger
than anything it introduced.

### Load order

Three facts about how these stylesheets reach the browser. They decide which
rule actually wins, and none of them is visible from the source alone.

1. **`shared/common.css` is emitted last inside the eager bundle.** Against
   another eager stylesheet — `index.css`, `App.css`, `ChannelsPane.css`,
   `StreamsPane.css`, `ChannelManagerTab.css`, `ModalBase.css` and the ~30
   modals — it wins at equal specificity. That is why `.pane-header h2`
   renders at the shared 10px and not at the 1.1rem `ChannelsPane.css` and
   `StreamsPane.css` used to declare: those two rules were dead.
2. **Every lazily imported tab chunk is appended after the eager bundle, and
   is never removed.** One visit permanently installs that tab's stylesheet
   for the rest of the session. So `common.css` *loses* to any bare rule in
   any tab you have visited — permanently, and on every other page too.
3. **Channel Manager is eager, so it is a one-way donor.** Its bare classes
   apply to every page from first paint, and it can never win against a
   visited tab.

The operational consequence: **moving a class into `common.css` does nothing
until every page copy of it is deleted.** A half-finished extraction is worse
than none — the shared rule is dead on the pages that still declare their
own, live everywhere else, and the diff looks finished. When you delete a
page copy, leave a comment in its place naming what owns it now, so the next
person does not "helpfully" put it back.

The same three facts make a *bare* class name in a page stylesheet a bug in
its own right, independent of typography: two pages that happen to pick the
same name (`.section-title`, `.stat-item`, `.filter-select`, `.header-stat`)
silently swap styles depending on which one you opened first. Scope
page-owned rules to a page ancestor. Four production defects have come from
this exact shape (`qlc4h`, `f4yc7`, `sccol`, `.action-btn`), and the
`e2e/css-smoke` suite cannot see any of them, because each of its tests
visits exactly one route in a fresh browser context.

### Adopting a role

Two rules, derived from the `enhancedchannelmanager-f4yc7` pilot and applied
sweep-wide. They are house style now.

1. **Plain text takes the role's full triplet** — `font-size`, `font-weight`,
   `line-height`. **Interactive controls take the role's SIZE token only and
   keep the weight they were authored with.** A button, an input, a select or
   a chip has a weight that belongs to the control's affordance, not to the
   text role; overwriting it makes the control read as body copy. The pilot
   established this on `.priority-input` and the M3U action buttons — both
   moved to `--type-body-size` and kept their own `font-weight`.
2. **Icons are chosen by role, not by nearest pixel.** A glyph in a chip or
   beside meta text → `--icon-badge`. A glyph in a button, or beside body text
   or an item title → `--icon-action`. A leading, status or panel-header glyph
   → `--icon-status`. An empty-state or loading illustration → `--icon-empty`.
   Do not pick the token whose value is closest to what the glyph renders at
   today; pick the one that names what the glyph is doing.

### Partial redeclaration

**A rule that redeclares only some of a role's properties silently inherits
the rest from whatever it was meant to replace.** The result is a hybrid that
looks deliberate in the diff and is wrong in the browser. This cost more time
in the P1 sweep than any other single mistake. Four confirmed instances:

| Rule | Declared | Silently kept | Rendered |
|-|-|-|-|
| Guide's `.time-slot-header` | `font-size`, `font-weight` | `letter-spacing`, `text-transform` | 12.8px / 600 uppercase with 0.08em tracking — neither the old look nor the micro role |
| Stats' seven panel `.section-title` rules | size, weight, line-height | `text-transform`, `letter-spacing` from `StatsTab.css`'s bare `.section-title` | 15px / 600 ALL-CAPS |
| `.catchup-badge__icon`, `.edit-mode-banner-icon`, `.edit-mode-icon` | `font-size` | everything — the rule never applied | 24px, from `index.css`'s base `.material-icons` |
| `.pending-merges-candidate-id` | `color`, `font-size` in a later rule | `font-weight`, `word-break` from an earlier combined rule | looked fully shadowed; was not |

The rule when you adopt a role: **declare the role's full property set, or
write a comment naming which properties you are deliberately inheriting and
from where.** "It inherits the rest" is only acceptable when it is written
down. The same applies when you scope a colliding selector — scope every
property the collision covers, not just the ones you came for.

The icon case has its own trap. `index.css` defines the base `.material-icons`
at 24px, and it is emitted near the *end* of the eager bundle. A single-class
override in an eager component stylesheet — `.catchup-badge__icon`,
`.edit-mode-icon` — ties at 0-1-0 and loses on byte order, so the glyph renders
at 24px while the source appears to say otherwise. **An icon override needs two
classes of depth**: `.catchup-badge .catchup-badge__icon`, not
`.catchup-badge__icon`.

### Rollout status

**All ten route pages are remapped.** Dashboard, M3U Manager, EPG Manager,
Logo Manager, Channel Manager, Channel Pipeline, Guide, Stats, M3U Changes,
Journal and Settings all take their content-pane type from the roles above.
(FFmpeg Builder is excluded — deprecated in 0.18.0.) The route header band,
the rail and the top bar remain frozen chrome outside the scale, per § "What
is *not* in this scale".

The two pilot pages came first: **M3U Manager** and **EPG Manager** (bead
`enhancedchannelmanager-f4yc7`). The shared classes those pages depend on —
`.btn-primary` / `.btn-secondary` / `.btn-danger`, `.header-description`,
`.list-header`, `.badge-sm`, `.micro-label` — moved with them and therefore
already apply everywhere. `.header-title h2` — the PageHeader section
heading — joined them on the section role (bead
`enhancedchannelmanager-meh0a`); it too applies everywhere. Its render sites
are EPG Manager's "Dummy EPG Profiles" and "Dummy EPG Sources (Legacy)" plus
the two headings that name the pilot pages' own tables — "EPG Sources" and
"M3U Accounts" (bead `enhancedchannelmanager-7dxx0`). `.page-header
.header-title h1` — the route title — joined on the page-title role (bead
`enhancedchannelmanager-tygwm`) and applies to every route, not just the two
remapped pages.

A section heading is rendered by `PageHeader`, never hand-rolled. When the
header carries nothing but its heading — naming the list directly beneath it
— pass `className="page-header-heading-only"`, which trades the default
1.5rem bottom margin for 0.5rem so the heading sits closer to the list it
labels (24px above / 12px below, against the tabs' 1.5rem padding) than to
the route header above it.

**Shared layer consolidated (bead `enhancedchannelmanager-6z299`, wave 0).**
Before the remaining eight pages could be remapped, the shared layer had to
actually own the shared classes. That pass moved these onto the roles and
deleted every page copy of them (see § Load order for why the deletions are
the load-bearing half):

- `.btn-cancel`, `.btn-test`, `.btn-small`, `.btn-icon`, `.separator-btn`,
  `.action-btn` (now the canonical 32x32 box), `.form-group label`,
  `.form-group input/select`, `.form-input`, `.form-hint`, `.search-input`,
  `.search-box input`, `.filter-dropdown-button`, `.filter-dropdown-option`,
  `.empty-state h3` / `p`, `.error-banner`, `.error-message`,
  `.success-message`, `.warning-message`, `.test-result`, `.loading`,
  `.status-disabled`, `.pane-header h2`, `.badge`, `.type-badge`,
  `.detail-section h4`, `.visually-hidden`.
- New shared sections: § 25 Pagination Strip, § 26 Field Messages,
  § 27 Group Rows, plus `.empty-inline` and the `.file-info` / `.file-name` /
  `.file-size` chip.
- All 16 raw icon sizes in `common.css` now use the icon tokens.
- Card and panel titles moved onto the section role wherever they were
  spelled out per-page (14/15/16/18px before): `.settings-section-header h3`,
  `.backup-card-header h3`, `.auth-provider-header h4`, `.tls-status-card h3`,
  `.tls-config-card h3`, `.tag-group-title h4`, `.link-account-section h4`,
  `.profile-list-header h3`, `.norm-engine-test-header h4`, Channel
  Pipeline's `.section-header h3`, `.event-sync-review-header h3` and
  `.event-sync-exclusions-header h3` (both of which declared no font-size at
  all and rendered at the UA 16px), and the six Stats panels'
  `.section-title`.

**Never write a bare `font-size` in a content-pane rule.** That is a rule now,
not an aspiration — every content-pane page has been through the sweep, so a
new literal size is a new divergence rather than one of many. Pick a role. If
no role fits, that is a conversation about the scale, not a licence to write a
number.

## Common CSS Classes (shared/common.css)

### Buttons
- `.btn-primary` — main action button (uses `--button-primary-bg/text`)
- `.btn-secondary` — secondary action (uses `--border-primary` bg)
- `.btn-danger` — destructive action (red)
- `.btn-cancel` — cancel/dismiss action
- `.btn-test` — primary-styled button that reports its own result
- `.btn-small` — **size modifier**, worn alongside `.btn-primary`/
  `.btn-secondary`; not a standalone button
- `.btn-icon` — icon-only button; `.btn-icon-danger` / `.btn-icon.delete` for
  the destructive variant
- `.separator-btn` — segmented single-character choice

### Forms
- `.form-group` — wrapper: `label` + `input/select` with consistent spacing
- `.form-group label` — block label on the label role (13px / 500)
- `.form-hint` — small helper text below inputs
- `.field-hint` / `.field-error` — the hint and validation error under a form
  control (§ 26)
- `.form-input` / `.form-select` — standalone inputs outside `.form-group`

### Loading States
- `.loading-state` — **sub-panel** loading: 200px height, centered, 48px icon
- `.spinning` — animation class: `spin 1s linear infinite reverse`
- Use with: `<span className="material-icons spinning">sync</span>`

### Tab Loading (App.css)
- `.tab-loading` — **full-page** tab loading: flex:1, centered, 2rem icon
- Used for top-level tab early returns when data is loading
- All tabs MUST use this for consistency

### Empty States
- `.empty-state` — centered, dashed border, `--icon-empty` glyph, h3 + p
- `.empty-inline` — the one-line "nothing here yet" string inside a list or
  card, as opposed to the full-page block

### Error/Warning/Success Banners
- `.error-banner` — red banner with icon + dismiss button
- `.error-message` — inline red notice (no dismiss button)
- `.success-message` — green banner with slide-in animation
- `.warning-message` — yellow/amber banner

### Badges
- `.badge` — neutral default (bg-tertiary, text-secondary)
- `.badge-success` / `.badge-error` / `.badge-warning` / `.badge-info` — semantic colors
- `.badge-sm` / `.badge-lg` — size variants
- `.badge-pill` — rounded pill shape
- `.badge-outline` — transparent with border
- `.badge-uppercase` — uppercase with letter-spacing

### Status Indicators
- `.status-success` / `.status-error` / `.status-pending` / `.status-disabled` / `.status-idle`

### Micro Labels
- `.micro-label` — the tracked uppercase label idiom (column headers, the status
  word under a glyph). Sets size/weight/case/tracking from the micro role and
  nothing else; supply the colour from context. `.list-header` picks up the same
  rule, so a list header needs no extra class.

### Pagination (§ 25)
- `.pagination` — the list footer strip; carries the body role, its parts
  inherit it
- `.pagination-left` / `-center` / `-right` — its three columns
- `.page-info` / `.page-indicator` — "Page 3 of 12"
- `.entries-count`, `.page-size-label`, `.page-size-select`

### Group Rows (§ 27)
- `.group-name` — item-title role, truncating
- `.group-count` — meta role; draw your own pill around it if you want one

### Updated Timestamp (§ 29)
- `.updated-label` / `.updated-time` — the "Updated: &lt;when&gt;" pair in a list
  row. Colour only; the size and line-height come from the page's own wrapper
  (`.source-updated` on EPG Manager, `.account-updated` on M3U Manager), which
  sets the meta role on the pair. Hoisted from those two pages, which declared
  the same colour bare in two different lazy chunks
  (bead `enhancedchannelmanager-mktnb`).

### Other
- `.visually-hidden` — WCAG screen-reader-only utility (§ 2). `.sr-only` is an
  alias of it, declared on the same rule: the two were property-for-property
  identical, and `.sr-only`'s only copy sat in the Channel Pipeline lazy chunk
  while the Dashboard and Settings `aria-live` regions rendered it, so those
  announcements were visible text until that tab was opened
  (bead `enhancedchannelmanager-zncyv`). Use either name; prefer
  `.visually-hidden` in new markup.
- `.file-info` / `.file-name` / `.file-size` — the picked-file chip (§ 17)
- `.search-box` — icon + input search field
- `.action-btn` — icon-only row action button, 32x32
- `.drag-handle` — drag handle with grab cursor
- `.checkbox-group` / `.checkbox-option` — checkbox lists
- `.filter-dropdown` — multi-select filter dropdown

## Header / toolbar overflow policy

**Every header/toolbar row that pairs a title with an action cluster MUST have
a defined overflow behavior. The policy is: the row wraps.** This is one
reusable idiom, not a route-local judgment call — headers were repeatedly shipping
with no overflow strategy, so a wide-enough toolbar either squeezed the title
to min-content (M3U title wrapped one word per line at 1280px) or overflowed an
`overflow: hidden` ancestor and clipped a button out of reach (Guide "Print
Guide"). Bead 09x38.2 (build 0115) is the umbrella fix.

### The rule (wrap-to-second-row)

The header row is `display: flex` with `flex-wrap: wrap` and a `row-gap`. When
the action cluster no longer fits beside the title, it drops to a second row
instead of fighting the title for width. The title column grows
(`flex: 1 1 auto; min-width: 0`); the action cluster keeps its natural size
(`flex-shrink: 0`) and itself wraps (`flex-wrap: wrap; justify-content:
flex-end`) so its buttons reflow at extreme widths. This matches the
already-well-behaved Channel Pipeline header, which wraps cleanly at 1024px.

`flex-wrap` is deliberately preferred over any "collapse past N buttons"
threshold: there is no magic N to tune, so it can't silently regress when a
button is added (the exact fragility called out in this bead's acceptance
criteria).

Shared consumers get this for free:

- **`.page-header`** (via the `PageHeader` component — `PageHeader.css`)
- **`.tab-header`** and **`.header-actions`** (`shared/common.css` § TAB HEADERS)

Prefer the `PageHeader` component for new manager-tab headers. A bespoke header
row (one carrying live content that can't move into `PageHeader` — e.g. Stats'
provider tiles, the Guide timeline controls, the Normalization drag-drop list)
must still apply the same `flex-wrap: wrap; row-gap` idiom to its row and to
its action cluster, with a comment pointing back to this section.

### Collapse-to-kebab (when a row has too many actions to wrap nicely)

When wrapping alone still leaves an unwieldy row (M3U Manager had 7 buttons),
move the **setup/admin** actions into the shared **`OverflowMenu`** kebab
(`OverflowMenu.tsx`) and keep only the primary actions as buttons. The kebab is
the generalized form of ChannelsPane's `PaneToolbarMenu`; pass it an
`OverflowMenuItem[]` (`label`, `icon`, `onClick`, optional `disabled`/`title`).
This is decluttering on top of — not instead of — the wrap policy: the row
still wraps as its safety net.

## Settings Page Patterns (SettingsTab.css)

### Page Header
```tsx
<div className="settings-page-header">
  <h2>Page Title</h2>
  <p>Description text.</p>
</div>
```
Always use `<h2>` (1.5rem/600 weight). Never use `<h3>` for settings headers.

### Settings Section
```tsx
<div className="settings-section">
  <div className="settings-section-header">
    <span className="material-icons">icon_name</span>
    <h3>Section Title</h3>
  </div>
  {/* content */}
</div>
```

### Checkbox Label
```tsx
<label className="checkbox-label">
  <input type="checkbox" checked={value} onChange={handler} />
  <span>Label text</span>
</label>
```
Uses 18px checkbox, 0.5rem gap, accent-primary color.

## Modal Patterns (ModalBase.css)

```tsx
import '../ModalBase.css';  // MUST import in every modal component

<ModalOverlay onClose={handleClose}>
  <div className="modal-container modal-lg">
    <div className="modal-header">
      <h2>Title</h2>
      <button className="modal-close-btn" onClick={onClose}>
        <span className="material-icons">close</span>
      </button>
    </div>
    <div className="modal-body">
      <div className="modal-form-group">
        <label>Field Name</label>
        <input type="text" value={val} onChange={...} />
      </div>
      <label className="modal-checkbox-label">
        <input type="checkbox" checked={val} onChange={...} />
        Checkbox label
      </label>
    </div>
    <div className="modal-footer">
      <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
      <button className="modal-btn modal-btn-primary" onClick={onSave}>Save</button>
    </div>
  </div>
</ModalOverlay>
```

Key modal classes:
- **Form groups**: `modal-form-group` (not custom `form-row` / `form-group`)
- **Buttons**: `modal-btn modal-btn-primary` / `modal-btn-secondary` / `modal-btn-danger`
- **Checkboxes**: `modal-checkbox-label`
- **Close button**: `modal-close-btn` (not `modal-close`)
- **Hints**: `form-hint` inside `modal-form-group`
- **Required marks**: `modal-required`
- **Section titles**: `modal-section-title`

Size classes: `modal-sm` (400px), `modal-md` (550px), `modal-lg` (700px), `modal-xl` (900px), `modal-xxl` (1000px), `modal-full` (95vw)

## Theme Variables — Critical Rules

The `--accent-*` variables flip between dark/light mode:
- Dark: `--accent-primary` = white, `--accent-secondary` = light gray
- Light: `--accent-primary` = indigo, `--accent-secondary` = lighter indigo

**NEVER use `--accent-primary` or `--accent-secondary` for backgrounds or badge colors.** They cause contrast issues.

**Safe for backgrounds:** `--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--input-bg`, `--button-primary-bg`

**Safe for text:** `--text-primary`, `--text-secondary`, `--text-muted`, `--button-primary-text`

## Component CSS File Header Convention

Add a comment at the top of each component CSS listing which shared classes it uses:
```css
/**
 * ComponentName styles
 *
 * Uses common.css for: .btn-primary, .btn-secondary, .loading-state, .spinning
 * Uses SettingsTab.css for: .settings-page-header, .checkbox-label
 * Uses ModalBase.css for: .modal-overlay, .modal-container
 */
```

## Checklist Before Writing New CSS

1. Is there a shared class in `common.css` that does this? Use it.
2. Is this a settings page pattern? Check `SettingsTab.css`.
3. Is this a modal? Use `ModalBase.css` patterns.
4. Am I duplicating `@keyframes spin`? Use `.spinning` from common.css.
5. Am I creating a custom loading/error/empty state? Use `.loading-state` / `.error-banner` / `.empty-state`.
6. Am I using `--accent-primary` for a background? Stop — use `--bg-tertiary` or `--button-primary-bg`.
