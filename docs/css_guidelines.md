# CSS Guidelines

> This document is **authoritative for CSS**: naming, layer architecture,
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
| Tab Loading | `App.css` | `.tab-loading`: full-page centered loading for tab-level screens |
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
| Metric | `--type-metric-*` | 20px (`--text-3xl`) | 600 | 1.2 |  | the headline number on a stat/summary tile |
| Modal title | `--type-modal-title-*` | 16px (`--text-xl`) | 600 | 1.3 |  | the title of a dialog |
| Section | `--type-section-*` | 15px | 600 | 1.3 |  | a heading inside a page |
| Body | `--type-body-*` | 13px | 400 | 1.5 |  | running text, buttons, inputs, page descriptions |
| Item title | `--type-item-title-*` | 13px | 600 | 1.4 |  | the name of a row in a list |
| Meta | `--type-meta-*` | 11px (`--text-xs`) | 400 | 1.5 |  | supporting detail under an item title: type, URL, counts, timestamps |
| Micro | `--type-micro-*` | 10px (`--text-2xs`) | 700 |  | uppercase, tracking `0.08em` | column headers, the status word under a glyph |
| Badge | `--type-badge-*` | 10px (`--text-2xs`) | 500 | 1.4 |  | text inside a chip or pill |
| Label | `--type-label-*` | 13px | 500 | 1.4 |  | the caption on a form control |

Each role token points at the `--text-*` primitive that already carries its
number. 15px and 13px have no primitive, so those two are written literally
rather than adding primitives nothing else consumes.

Micro deliberately defines no line-height: micro labels take the line box of
whatever they sit in.

Page title and metric are both 20px. They are separate roles because they
separate on weight and character: the title is 700 and uppercase, the metric
is 600 and numeric, and the two never sit on the same line. The title is the
first line of the route header, the metric is inside a tile in the pane below.
Sizing them from one role would tie a later change to one of them to the
other.

**Modal title is a distinct role, not the section role.** A dialog's title is
the primary heading of its own surface, so it sits closer to a route title than
to a panel heading inside a page. It reads against the modal's own body copy,
not against the route header behind the overlay. PO decision on bead
`enhancedchannelmanager-xhldy`. It was a bespoke `--modal-title-size: 1.1rem`
(17.6px) in `ModalBase.css`; `xhldy` set the role to 18px because 17.6px was
never a chosen number. It is what `1.1rem` computes to. `--modal-title-size`
survives as the per-modal theming seam, now defaulting to the role.

Bead `enhancedchannelmanager-99o0x` moved it **18px → 16px** (`--text-xl`) on
the PO's "the title text is oversized", and kept it a separate role rather than
collapsing it into `--type-section-*`. The reason is hierarchy inside the
dialog, not the number: a modal body's own headings are `.modal-section-title`
at the 15px/600 section role, so a title at 15px/600 would be identical to the
headings *below* it and the dialog would lose its primary heading. A 1px step
reads thin, but by this scale's own standard it is not redundancy: a role
names the *kind* of text, not its size, and body / item title / label already
share 13px **exactly** while page title and metric share 20px. Modal title is
the only role naming "the primary heading of an overlay surface". Weight stays
600: dropping it would put the dialog title *below* its own section headings.

Label and item title are both 13px for the same reason: a field caption reads
as part of its control, so it takes the button weight (500), while a list-row
name is the heaviest thing in its row (600). Bead
`enhancedchannelmanager-6z299` added the label role because `.form-group
label`, the one shared form treatment, had no role that fitted it.

### Icon sizes

| Token | Value | Used for |
|-|-|-|
| `--icon-status` | 18px | the status glyph in a list row, and inline notice icons |
| `--icon-action` | 16px | row action buttons, small inline indicators |
| `--icon-badge` | 14px | a glyph inside a chip or pill |
| `--icon-empty` | 64px | the illustration glyph in an empty or loading state |
| `--control-box-size` | 16px | the box of a checkbox or a radio |

Rail icons are chrome, not content, and keep their own 20px.

**`--control-box-size` is in this scale, not the type scale, because a
checkbox is the same kind of object as `--icon-action`: a glyph-scale mark
sitting inline in a row of 13px body text. It is a length, not a font-size.
A control's *text* is still the body role set by the reset at the top of
`index.css`.

The number's justification is its relationship to the body role, not its
roundness. Measured on the rendered page, a label row carrying 13px text has a
line box of 18.2–18.5px and the text's own em box is 13px. 16px is 1.23em:
above the em box, so the control reads as a control rather than as a
letterform, and below the line box, so it does not drive the height of the row
it sits in. 18px is almost exactly that line box, which is why Settings read
oversized once body text moved to 13px in bead `enhancedchannelmanager-ul2tp`.
At 18px the control fills the row and the label starts to read as a caption to
the box. 13px is 1em, and it is also Chromium's own control size.

Bead `enhancedchannelmanager-7lwe0` introduced it. Before it, 54 rule blocks
across 26 component stylesheets each declared their own literal and 16 more
selected a checkbox without sizing it at all, which rendered as **five** sizes
for one control: 13, 14, 15, 16 and 18px. (The CSS *declared* seven; `1rem`
and `0.875rem` collapse onto 16px and 14px, and the 13px population appears in
no stylesheet at all. It is the user-agent default the unsized blocks
inherited. Counting declarations gets this wrong in both directions.) Those
literals are deleted and every block falls through to one base rule beside the
token.

The size is **not** a pointer target. WCAG 2.5.8 asks for 24×24 CSS px and no
plausible checkbox beside 13px text reaches it; what carries the target is the
`<label>`, which extends it to the whole row. 167 of the 211 controls measured
for `7lwe0` have that association and 44 do not. For those the box *is* the
target, and the remedy is markup, not a bigger box. `e2e/control-box-size.spec.ts`
catalogues them in a ratcheted `ALLOWED_UNLABELLED` and fails on a new one.
Bead `enhancedchannelmanager-m26f8` emptied that list; the target floor itself
is `--control-target-min` below.

### The pointer target: `--control-target-min`

| Token | Value | Used for |
|-|-|-|
| `--control-target-min` | 24px | the minimum height of a checkbox/radio **row** |

`--control-box-size` is what a control looks like; `--control-target-min` is
how big it is to hit. They are a pair and they are deliberately different
numbers. The box cannot carry the target, because a 24px box beside 13px text
would be nearly twice the height of its own label, which is the defect the PO
reported in `7lwe0`. **The height comes from the row.**

Applied by one base rule in `index.css`:

```css
label:has(input[type="checkbox"]),
label:has(input[type="radio"]) {
  min-height: var(--control-target-min);
  align-content: center;
}
```

A **floor, not an increment**, and that is the whole design. Measured before
bead `enhancedchannelmanager-3h2u1`, of 211 rows across routes and dialogs at
both viewports in all three themes: 120 already cleared 24×24 (several at
34–91px, where a card or a description line makes the row tall on its own)
and 91 did not, at 18.19 / 19.5 / 21.19 / 21.5px. **None failed on width**; a
row is as wide as the form it sits in. A padding increment would have to be
sized for the 18px rows, would over-grow the 21px ones, and would grow all 120
that were never in breach. `min-height` moves each short row by exactly what it
lacks and is inert on every row above it, so the vertical rhythm gets *more*
regular: every short checkbox row lands on one number instead of a four-way
spread.

`align-content: center` rides along because a floor without alignment is half a
fix. A row that gains height has to put the extra space somewhere. Where the
label wraps its input, the box and its text are both inside and move together,
so centring preserves their alignment. It is inert on the labels that are
already `display: flex; align-items: center` (a single-line flex container
ignores `align-content`) and inert on any row taller than the floor.

**`:has()` is the whole population CSS can own.** A label extends the target
two ways: by wrapping the input, or by pointing at it with `for`. Only
the first is expressible: `label[for]` cannot ask what it points *at*. 71 of
the 91 short rows wrap their input and the base rule fixes them; the other 20
are the `for` shape in `.settings-section .checkbox-content`, which carries its
own `min-height` with the reason stated at that rule. A new site that wraps its
input inherits the floor; a new `for` site does not, and arm 4 of
`e2e/control-box-size.spec.ts` is what catches it. That arm measures the
**union** of the control's rect and every visible associated label's rect
(the region a pointer can actually hit) and resolves the token through a probe
element rather than repeating 24.

`accent-color` is unchanged by that bead and was re-measured under it, from
rendered pixels in all three themes at both supported viewports: a checked
box's accent fill against its adjacent surface is 8.70–15.15:1 (dark),
3.86–6.29:1 (light) and 11.37–18.44:1 (high contrast); an unchecked box's
border against the same surface is 3.08:1 at worst. All clear the 3:1 non-text
floor in WCAG 1.4.11, the light theme's checked fill and the dark theme's
unchecked border least comfortably.

### Colour

Bind meta and micro text to `--text-secondary`. `--text-muted` used to measure
2.61:1 against `--bg-secondary` in the light theme, below the WCAG AA 4.5:1
floor; bead `enhancedchannelmanager-dlavh` re-toned the light theme's value to
`#686868` and it now measures 5.11:1 there. The preference for
`--text-secondary` on meta and micro text stands anyway: see the next
paragraph for why the two are no longer distinguishable in that theme.

`.micro-label` itself sets no colour. A micro label takes the colour of the
thing it labels (a status word is green or red; a column header is
`--text-secondary`, set on `.list-header`).

Two deliberate exemptions. `::placeholder` rules keep `--text-muted`.
Placeholder contrast is a separate question and was not bundled into the type
sweep. Purely decorative non-text glyphs keep it too: `.empty-state
.material-icons` is a 64px illustration, not text, so the 4.5:1 text floor
does not apply to it.

#### Muted is a dark-theme distinction

AA leaves almost no room below `--text-secondary` on a light surface: 4.5:1
against `--bg-secondary` (#f5f5f5) forbids anything lighter than #707070, and
against the #ddd chip fill anything lighter than #616161. So in the light
theme `--text-muted` (#686868) and `--text-secondary` (#616161) are within
seven RGB units and read as the same colour. The token was not retired,
because the step is real and passing in the other two themes: dark 4.82 vs
6.46 on `--bg-secondary`, high contrast 8.52 vs 15.00. **Do not reach for
`--text-muted` in the light theme expecting it to read as dimmer; it cannot.**

#### Semantic colours have two jobs, and they are not the same colour

`--success`, `--error` and `--warning` are each used both as a foreground
(status text, status glyphs) and as a solid fill that carries text. Those two
jobs pull in opposite directions, and a single token can only serve both when
the theme happens to leave room:

- **Light theme, `--success` and `--error`**: both jobs wanted the value
  *darker*, so bead `dlavh` darkened the tokens in place (`#22c55e` →
  `#137035`, `#ef4444` → `#c21111`) and both roles now pass.
- **Light theme, `--warning`**: the fill carries `#1a1a1a` text at 8.10:1 and
  must stay light, while as a foreground it measures 1.97:1. It is **not**
  darkened. Warning-coloured *text* takes `--warning-text`, which exists for
  exactly that: see `.status-pending` in `shared/common.css`.
- **Dark and high-contrast, `--error`**: irreconcilable, and therefore split.
  As text `#ef4444` measured 4.03:1 and wanted to go *lighter*; as a fill under
  white it measured 3.76:1 (3.41 in high contrast) and wanted to go *darker*.
  Bead `4l51b` resolved it by giving the token one job: see below.

**`--error` is a fill only.** Bead `enhancedchannelmanager-4l51b` moved all 94
`color: var(--error)` call sites, text *and* status glyphs alike, to
`--danger-text`, then retoned `--error` for the fill alone: `#ef4444 → #dc2626`
(dark) and `#ff4444 → #d81b1b` (high contrast); light stays `#c21111`, where
both jobs had already converged. White-on-fill went 3.76 → 4.83 dark and
3.41 → 5.13 high contrast. In the light theme `--danger-text` was retoned to
`#c21111` (the same value as `--error`), so that the migration changed no
rendered light-theme colour at all; the two tokens being identical there is
deliberate, the same shape as `--text-muted` converging on `--text-secondary`.

The general rule when you need a semantic colour: **`--success` and
`--warning` are fills and status glyphs, `--error` is a fill only;
`--danger-text` / `--warning-text` / `--info-text` are the foreground tones,
and `--danger-text` covers red glyphs as well as red text.** `--success-text`
is the odd one out: it means "text drawn ON a `--success` fill", not
"success-coloured text". Reading it the other way put a white glyph on a white
background at 1.09:1 (`.refresh-indicator.active`, bead `wjbwr`).

The asymmetry is real and is not a tidiness bug: `--success` still does both
jobs because in all three themes both jobs wanted the same value. Do not
"harmonise" the three tokens back into one pattern.

Where the two halves of the fix each paid off is worth knowing, because it is
not symmetric across the themes. At the point `4l51b` started, `--danger-text`
and `--error` held the *same* value (`#ff4444`) in the high-contrast theme, so
the call-site migration alone is a no-op there, and every high-contrast gain
comes from retoning `--error`. In the dark theme both halves contributed. In
the light theme neither changes a rendered colour; the work there is purely
making the roles say what they mean.

**One documented exception.**
`.dashboard-card-status.tone-danger .material-icons` in
`components/tabs/OperatorDashboard.css` still reads `color: var(--error)`. It
was not missed: that line arrived with in-flight uncommitted work (bead
`2896r`) that `4l51b` was instructed not to touch, so migrating it would have
meant editing someone else's open hunk. It is a Material Icons glyph on
`--bg-secondary`, so it is measured against the 3:1 non-text floor rather than
4.5:1, and it still clears it after the retone: 3.14:1 dark (down from 4.03),
3.86:1 high contrast (down from 6.16), 5.70:1 light (unchanged). It is the one
place in the app where `color: var(--error)` is not a defect, and it should be
migrated to `--danger-text` when `2896r` lands.

Two constraints on ever re-toning `--error`. White-on-fill and fill-against-
page-background are both monotonic in the fill's luminance and pull opposite
ways, so the AA text floor (4.5:1) and the WCAG 1.4.11 non-text floor (3:1)
between them leave a window of roughly 0.174–0.183 relative luminance in the
dark theme. Change one number and you must re-measure the other. And because
the token no longer carries any foreground, a rule that reaches for
`color: var(--error)` is now a defect, not a style preference.

#### Theme-conditional colour lives beside the rule it corrects

A chip that hardcodes a Tailwind 400-level ink on an alpha fill of the same hue
is a dark-theme palette; on a pale row it collapses to 1.4–2.1:1. The house
pattern is a `[data-theme="light"]` rule immediately after the base rule
changing **only** the ink, so the fill, and therefore the chip's colour
coding, is untouched. `.account-type.hdhr` (bead `sccol`) established it;
`.account-type.xc` / `.std`, the Journal `.category-*` / `.source-*` families
and `.group-auto-sync-badge` follow it (bead `dlavh`). Record the measured
ratio before and after in the comment.

#### `opacity` de-emphasises the group, not the ink

`opacity` is a **group multiplier**. An element with `opacity < 1` renders its
whole subtree (its own background and its text alike) and then fades that
render onto whatever is behind it. It does not lighten a colour against the
surface the element sits on; it lowers the contrast of everything in the
element against the PAGE. So reaching for `opacity` to make text "quieter"
spends contrast, and spends it in a way that no theme token can compensate for,
because the fade is applied after the theme has already resolved.

**The rule.** Use `opacity` only where the contrast floor does not apply:
decorative illustrations, `:disabled` controls (WCAG 1.4.3 and 1.4.11 both
exempt them, and `e2e/contrast-aa.spec.ts` skips `[disabled]` subtrees for that
reason). Where the de-emphasised thing is **text**, or is a control's **only
glyph**, express the de-emphasis with an ink token instead, usually
`--text-secondary`, and record the measured ratio in the rule's comment.

Three sites found in one session, and they did not all resolve the same way,
because WCAG 1.4.11 turns on whether the thing conveys information:

| Site | Shape | Outcome |
|-|-|-|
| `.probe-group-btn .material-icons` | Icon-only control, no adjacent label: the glyph IS the affordance, 3:1 applies | **Fixed** by raising `opacity` `.5` → `.75` (bead `dlavh`): 2.32/2.10/2.82 → 3.55/3.28/5.24 |
| Stats `.no-streams .material-icons` | 64px illustration with "No active streams" set directly beneath it: identifies nothing the words do not | **Deliberately not fixed** (bead `eo7er`). Raising it would undo an intentional de-emphasis to satisfy a floor that does not apply. Carried in the guard's allowlist at 2.68 dark / 2.11 light |
| `.preview-stream-error p` / `.error-details` | Error text the operator has to read | **Fixed** by removing the fade (bead `4pzvg`): the message drops `opacity: .9` (4.14/4.54/4.05 → 4.74/5.01/4.70) and the detail line trades `opacity: .7` for `--text-secondary` (3.09/3.41/2.99 → 5.59/5.00/12.12) |

Ratios are dark / light / high-contrast, composited against the surface the
element actually sits on.

Raising an `opacity` is a legitimate fix and often the least invasive one.
It keeps the de-emphasised intent and the hover step. It is only unavailable
when the value is already 1. Where hierarchy is what you actually wanted, size
and weight cost no contrast at all: that is why the preview-stream error block
needs no fade: its heading is 13px/600, its message 13px/400 and its detail
line 11px.

**Which core measured it.** Any ratio recorded against an `opacity` site before
bead `enhancedchannelmanager-0zq1p` is suspect. The guard's measurement core
accumulated group opacity in the wrong direction and faded the element's own
page background toward the canvas's white, so it under-read every dark and
high-contrast `opacity` site (`.probe-group-btn` read 1.56 where the truth is
2.32). Light was almost unaffected (white leaking into a near-white page is a
near-no-op), which is why it survived so long. A figure quoted for an `opacity`
site with no bead reference should be re-measured, not trusted.

### What is *not* in this scale

The rail and the top band are chrome and are frozen outside it: rail nav
label 14px / rail icon 20px / rail width 244px, header band 45px with 28px
controls.

The route page title used to be listed here too, frozen at 24px / 700. Bead
`enhancedchannelmanager-tygwm` moved it onto the scale at 20px. It sits
inside the content column, above the pane it names, and at 24px it was larger
than anything it introduced.

### Load order

Three facts about how these stylesheets reach the browser. They decide which
rule actually wins, and none of them is visible from the source alone.

1. **`shared/common.css` is emitted last inside the eager bundle.** Against
   another eager stylesheet (`index.css`, `App.css`, `ChannelsPane.css`,
   `StreamsPane.css`, `ChannelManagerTab.css`, `ModalBase.css` and the ~30
   modals) it wins at equal specificity. That is why `.pane-header h2`
   renders at the shared 10px and not at the 1.1rem `ChannelsPane.css` and
   `StreamsPane.css` used to declare: those two rules were dead.
2. **Every lazily imported tab chunk is appended after the eager bundle, and
   is never removed.** One visit permanently installs that tab's stylesheet
   for the rest of the session. So `common.css` *loses* to any bare rule in
   any tab you have visited, permanently, and on every other page too.
3. **Channel Manager is eager, so it is a one-way donor.** Its bare classes
   apply to every page from first paint, and it can never win against a
   visited tab.

The operational consequence: **moving a class into `common.css` does nothing
until every page copy of it is deleted.** A half-finished extraction is worse
than none. The shared rule is dead on the pages that still declare their
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

1. **Plain text takes the role's full triplet**: `font-size`, `font-weight`,
   `line-height`. **Interactive controls take the role's SIZE token only and
   keep the weight they were authored with.** A button, an input, a select or
   a chip has a weight that belongs to the control's affordance, not to the
   text role; overwriting it makes the control read as body copy. The pilot
   established this on `.priority-input` and the M3U action buttons: both
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
| Guide's `.time-slot-header` | `font-size`, `font-weight` | `letter-spacing`, `text-transform` | 12.8px / 600 uppercase with 0.08em tracking: neither the old look nor the micro role |
| Stats' seven panel `.section-title` rules | size, weight, line-height | `text-transform`, `letter-spacing` from `StatsTab.css`'s bare `.section-title` | 15px / 600 ALL-CAPS |
| `.catchup-badge__icon`, `.edit-mode-banner-icon`, `.edit-mode-icon` | `font-size` | everything: the rule never applied | 24px, from `index.css`'s base `.material-icons` |
| `.pending-merges-candidate-id` | `color`, `font-size` in a later rule | `font-weight`, `word-break` from an earlier combined rule | looked fully shadowed; was not |

The rule when you adopt a role: **declare the role's full property set, or
write a comment naming which properties you are deliberately inheriting and
from where.** "It inherits the rest" is only acceptable when it is written
down. The same applies when you scope a colliding selector: scope every
property the collision covers, not just the ones you came for.

The icon case has its own trap. `index.css` defines the base `.material-icons`
at 24px, and it is emitted near the *end* of the eager bundle. A single-class
override in an eager component stylesheet (`.catchup-badge__icon`,
`.edit-mode-icon`) ties at 0-1-0 and loses on byte order, so the glyph renders
at 24px while the source appears to say otherwise. **An icon override needs two
classes of depth**: `.catchup-badge .catchup-badge__icon`, not
`.catchup-badge__icon`.

#### The modal-estate icon sweep, and three ways the trap hides

Bead `enhancedchannelmanager-7bsxj` measured the whole trap across the 82
force-rendered dialogs: **146 elements at 24px in 30 dialogs, now 30**: 116
icons moved onto the tokens. The remaining 30 are the 27 `span.stat-value`
(text, not an icon, tracked separately) and three glyphs that are 24px on
purpose (`.toast-icon .material-icons`, `.logo-thumbnail .placeholder
.material-icons`), both of which already carry two classes of depth and win.

The 116 split into two populations, and the split is the useful part:
**46 elements already had a rule that named the right size and never rendered
it**, and **70 had no rule at all**. The first population is the dangerous one.
The source reads correct, review reads correct, and only a rendered
measurement disagrees. Grep is not a defence here; `getComputedStyle` is.

Three shapes let a size hide even when someone has clearly thought about it:

- **`:where()` de-specifies your own rule out of the fight.** `:where(.tag-
  engine-section) .expand-icon { font-size: var(--icon-status) }` is (0,1,0) by
  construction, which is the point when you are trying not to disturb *other*
  consumers of a colliding class, but it also means it ties the base
  `.material-icons` and loses. The `color` in the same rule was fine, because
  nothing else declared it; only the size was wrong. Keep the weak rule for the
  properties that need to stay weak and restate the *size* in a normally-scoped
  rule beside it.
- **Sizing the wrapper does not size the glyph.** `.execution-no-snapshot`
  carried `font-size: var(--icon-status)` and a comment saying the icon "was
  already 18px, so nothing moves". The wrapper was 18px; the `.material-icons`
  child sets its own size and inherits nothing, so the glyph was 24px. A
  `font-size` on an ancestor is never evidence about a Material Icons child.
- **Paired selector arms drift apart.** `.modal-header h2, .modal-header
  .modal-title` sets the title role for both spellings, but the icon rule under
  it listed only the `.modal-title` arm, so every modal that heads itself with
  an `<h2>` had a 24px glyph beside an 18px title. When one rule enumerates two
  equivalent selectors, the rules that qualify it must enumerate both.

**The base rule was deliberately not restructured.** Wrapping it as
`:where(.material-icons)` would let every single-class override win as written
and would permanently kill the trap. It is the honest fix and it should
happen. It was rejected *for this pass* because the blast radius is measured
and large: **33 single-class `font-size` rules on icon co-classes exist
app-wide**, all currently dormant, and only 11 of them were in this pass's
measured scope. The other 22 would have started rendering silently, at values
authored blind and never once observed (`.drop-icon` 36px,
`.error-boundary-icon` 40px, three banner icons at 1.625rem), on routes this
pass does not measure. Turning a measured 146-element change into an
unmeasured 22-rule change is the same "declared is not rendered" failure
running the other way. Do it as its own pass, behind its own route-wide
measurement.

### Rollout status

**All ten route pages are remapped.** Dashboard, M3U Manager, EPG Manager,
Logo Manager, Channel Manager, Channel Pipeline, Guide, Stats, M3U Changes,
Journal and Settings all take their content-pane type from the roles above.
(FFmpeg Builder is excluded, deprecated in 0.18.0.) The route header band,
the rail and the top bar remain frozen chrome outside the scale, per § "What
is *not* in this scale".

The two pilot pages came first: **M3U Manager** and **EPG Manager** (bead
`enhancedchannelmanager-f4yc7`). The shared classes those pages depend on,
`.btn-primary` / `.btn-secondary` / `.btn-danger`, `.header-description`,
`.list-header`, `.badge-sm`, `.micro-label`, moved with them and therefore
already apply everywhere. `.header-title h2`, the PageHeader section
heading, joined them on the section role (bead
`enhancedchannelmanager-meh0a`); it too applies everywhere. Its render sites
are EPG Manager's "Dummy EPG Profiles" and "Dummy EPG Sources (Legacy)" plus
the two headings that name the pilot pages' own tables: "EPG Sources" and
"M3U Accounts" (bead `enhancedchannelmanager-7dxx0`). `.page-header
.header-title h1`, the route title, joined on the page-title role (bead
`enhancedchannelmanager-tygwm`) and applies to every route, not just the two
remapped pages.

A section heading is rendered by `PageHeader`, never hand-rolled. When the
header carries nothing but its heading, naming the list directly beneath it,
pass `className="page-header-heading-only"`, which trades the default
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

**The modal estate is remapped (bead `enhancedchannelmanager-xhldy`).**
`ModalBase.css` plus the 34 dedicated `*Modal.css` / `*Dialog.css` files, the
seven modal-only Channel Pipeline stylesheets (`RuleBuilder`, `ActionEditor`,
`ConditionEditor`, `EventSyncRuleEditor`, `EventSyncPreviewPanel`,
`EventSyncTestPatternsPanel`, `ProviderScopedGroupPicker`) and the modal ranges
inside seven page stylesheets are on the roles above. Verified against all 82
force-rendered dialogs in `frontend/src/devHarness/` at 1280x720, 1440x900 and
1920x1080: see § Modal Patterns for how to re-run it. `17.6px` is gone from the
estate entirely (101 elements → 0).

Two idiom notes that came out of that pass:

- **Text arrows and carets are not icons.** `▼` in a `.dropdown-arrow`, `→`
  between a before/after pair. These are typographic marks whose size is tuned
  to the glyph, and the icon tokens (18/16/14/64) all mean "a Material Icons
  box". They keep their authored literal with a comment saying so. Only a rule
  whose selector actually reaches a `.material-icons` element takes an icon
  token.
- **The icon scale has no card tier.** `.modal-choice-btn .material-icons` (28px)
  and `.rule-kind-option .material-icons` are large illustrative glyphs inside a
  choice card. `--icon-status` makes the card read as empty and `--icon-empty`
  swamps it, so the first is left at its literal and flagged rather than forced
  onto a token that means something else.

**Never write a bare `font-size` in a content-pane rule.** That is a rule now,
not an aspiration. Every content-pane page has been through the sweep, so a
new literal size is a new divergence rather than one of many. Pick a role. If
no role fits, that is a conversation about the scale, not a licence to write a
number.

## Common CSS Classes (shared/common.css)

### Buttons
- `.btn-primary`: main action button (uses `--button-primary-bg/text`)
- `.btn-secondary`: secondary action (uses `--border-primary` bg)
- `.btn-danger`: destructive action (red)
- `.btn-cancel`: cancel/dismiss action
- `.btn-test`: primary-styled button that reports its own result
- `.btn-small`: **size modifier**, worn alongside `.btn-primary`/
  `.btn-secondary`; not a standalone button
- `.btn-icon`: icon-only button; `.btn-icon-danger` / `.btn-icon.delete` for
  the destructive variant
- `.separator-btn`: segmented single-character choice

### Forms
- `.form-group`: wrapper: `label` + `input/select` with consistent spacing
- `.form-group label`: block label on the label role (13px / 500)
- `.form-hint`: small helper text below inputs
- `.field-hint` / `.field-error`: the hint and validation error under a form
  control (§ 26)
- `.form-input` / `.form-select`: standalone inputs outside `.form-group`

### Loading States
- `.loading-state`: **sub-panel** loading: 200px height, centered, 48px icon
- `.spinning`: animation class: `spin 1s linear infinite reverse`
- Use with: `<span className="material-icons spinning">sync</span>`

### Tab Loading (App.css)
- `.tab-loading`: **full-page** tab loading: flex:1, centered, 2rem icon
- Used for top-level tab early returns when data is loading
- All tabs MUST use this for consistency

### Empty States
- `.empty-state`: centered, dashed border, `--icon-empty` glyph, h3 + p
- `.empty-inline`: the one-line "nothing here yet" string inside a list or
  card, as opposed to the full-page block

### Error/Warning/Success Banners
- `.error-banner`: red banner with icon + dismiss button
- `.error-message`: inline red notice (no dismiss button)
- `.success-message`: green banner with slide-in animation
- `.warning-message`: yellow/amber banner

### Badges
- `.badge`: neutral default (bg-tertiary, text-secondary)
- `.badge-success` / `.badge-error` / `.badge-warning` / `.badge-info`: semantic colors
- `.badge-sm` / `.badge-lg`: size variants
- `.badge-pill`: rounded pill shape
- `.badge-outline`: transparent with border
- `.badge-uppercase`: uppercase with letter-spacing

### Status Indicators
- `.status-success` / `.status-error` / `.status-pending` / `.status-disabled` / `.status-idle`

### Micro Labels
- `.micro-label`: the tracked uppercase label idiom (column headers, the status
  word under a glyph). Sets size/weight/case/tracking from the micro role and
  nothing else; supply the colour from context. `.list-header` picks up the same
  rule, so a list header needs no extra class.

### Pagination (§ 25)
- `.pagination`: the list footer strip; carries the body role, its parts
  inherit it
- `.pagination-left` / `-center` / `-right`: its three columns
- `.page-info` / `.page-indicator`: "Page 3 of 12"
- `.entries-count`, `.page-size-label`, `.page-size-select`

### Group Rows (§ 27)
- `.group-name`: item-title role, truncating
- `.group-count`: meta role; draw your own pill around it if you want one

### Updated Timestamp (§ 29)
- `.updated-label` / `.updated-time`: the "Updated: &lt;when&gt;" pair in a list
  row. Colour only; the size and line-height come from the page's own wrapper
  (`.source-updated` on EPG Manager, `.account-updated` on M3U Manager), which
  sets the meta role on the pair. Hoisted from those two pages, which declared
  the same colour bare in two different lazy chunks
  (bead `enhancedchannelmanager-mktnb`).

### Other
- `.visually-hidden`: WCAG screen-reader-only utility (§ 2). `.sr-only` is an
  alias of it, declared on the same rule: the two were property-for-property
  identical, and `.sr-only`'s only copy sat in the Channel Pipeline lazy chunk
  while the Dashboard and Settings `aria-live` regions rendered it, so those
  announcements were visible text until that tab was opened
  (bead `enhancedchannelmanager-zncyv`). Use either name; prefer
  `.visually-hidden` in new markup.
- `.file-info` / `.file-name` / `.file-size`: the picked-file chip (§ 17)
- `.search-box`: icon + input search field
- `.action-btn`: icon-only row action button, 32x32
- `.drag-handle`: drag handle with grab cursor
- `.checkbox-group` / `.checkbox-option`: checkbox lists
- `.filter-dropdown`: multi-select filter dropdown

## Header / toolbar overflow policy

**Every header/toolbar row that pairs a title with an action cluster MUST have
a defined overflow behavior. The policy is: the row wraps.** This is one
reusable idiom, not a route-local judgment call. Headers were repeatedly shipping
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

- **`.page-header`** (via the `PageHeader` component, `PageHeader.css`)
- **`.tab-header`** and **`.header-actions`** (`shared/common.css` § TAB HEADERS)

Prefer the `PageHeader` component for new manager-tab headers. A bespoke header
row (one carrying live content that can't move into `PageHeader`, e.g. Stats'
provider tiles, the Guide timeline controls, the Normalization drag-drop list)
must still apply the same `flex-wrap: wrap; row-gap` idiom to its row and to
its action cluster, with a comment pointing back to this section.

### Collapse-to-kebab (when a row has too many actions to wrap nicely)

When wrapping alone still leaves an unwieldy row (M3U Manager had 7 buttons),
move the **setup/admin** actions into the shared **`OverflowMenu`** kebab
(`OverflowMenu.tsx`) and keep only the primary actions as buttons. The kebab is
the generalized form of ChannelsPane's `PaneToolbarMenu`; pass it an
`OverflowMenuItem[]` (`label`, `icon`, `onClick`, optional `disabled`/`title`).
This is decluttering on top of the wrap policy, not instead of it: the row
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
0.5rem gap, accent-primary color. The box takes `--control-box-size` from the
base rule in `index.css`. `.checkbox-label input[type="checkbox"]` must not
restate a size (it declared 18px until bead `enhancedchannelmanager-7lwe0`).

Wrapping the `<input>` in the `<label>` is not cosmetic: it is what makes the
whole row the pointer target, which is the only reason a 16px box is
acceptable under WCAG 2.5.8. A checkbox rendered without an associated label
has no target but its own box. Wrapping also picks up the `--control-target-min`
floor automatically. A `<label for>` beside its input does not, and has to
declare the floor itself.

## Modal Patterns (ModalBase.css)

`ModalOverlay` is deliberately neutral: it supplies the backdrop and topmost
Escape routing, but it does not default a dialog role, accessible name, or
focus behavior. Each caller owns exactly one semantic surface, either on the
overlay or on one reviewed descendant, with `role="dialog"`/`alertdialog`,
`aria-modal="true"`, and a stable accessible name. Never add a second role to
the other layer. The closed caller contract lives in
`frontend/src/a11y/modalOverlayManifest.ts`; a recorded missing state is debt,
not an exception.

Callers opting into managed focus use `useModalFocusLifecycle` with their
dialog container and preferred initial-focus refs. A missing or disabled
preferred target falls back to the first eligible control; if none exists, the
helper temporarily makes the container programmatically focusable and restores
its original tab-index state at cleanup. It traps or recaptures Tab only in the
topmost overlay and restores the opener in stack order. Escape remains owned
by `ModalOverlay` and the caller's `onClose`, so a busy caller suppresses it by
passing its existing no-op/guarded close callback rather than through the focus
helper.

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
- **Buttons**: `modal-btn modal-btn-primary` / `modal-btn-secondary` / `modal-btn-danger`:
  the **base `modal-btn` class is mandatory**; it carries the geometry and the
  type, the variant carries only the colours. 27 buttons wore a variant with no
  base class and rendered at the user-agent default 13.3333px with none of the
  padding (bead `enhancedchannelmanager-xhldy`).
  There is **one** spelling: hyphenated. `.modal-btn.primary` / `.cancel` /
  `.danger` used to exist in `ChannelsPane.css` and are gone; `.cancel` maps to
  `-secondary`. Do not reintroduce an adjective-class variant.
  `.modal-btn` is deliberately **not** unified with `common.css`'s `.btn-*`:
  the two differ in padding and radius (`0.5rem 1rem` / `--radius-md` against
  `0.625rem 1.25rem` / `--radius-lg`) and unifying 168 buttons is a vocabulary
  decision, not a typography one. Never wear both (`modal-btn btn-secondary`):
  `common.css` is emitted last, so `.btn-*` wins the geometry and the button
  renders a size its siblings do not.
- **Checkboxes**: `modal-checkbox-label`
- **Close button**: `modal-close-btn` (not `modal-close`)
- **Hints**: `form-hint` inside `modal-form-group`
- **Required marks**: `modal-required`
- **Section titles**: `modal-section-title`

Size classes: `modal-sm` (400px), `modal-md` (550px), `modal-lg` (700px), `modal-xl` (900px), `modal-xxl` (1000px), `modal-full` (95vw)

### The header band is 49px, and the close button is what sizes it

`.modal-header` is `min-height: 49px` = `--modal-header-padding`'s 8px, the
32x32 `.modal-close-btn`, another 8px, and the 1px bottom border. Measured
animation-free across all 72 harness headers, **55 of them were exactly that
sum** before bead `enhancedchannelmanager-99o0x` shortened the padding. The
title's line box (20.8px at 16px/1.3) sits *inside* the button's row and does
not reach it. So **changing `--type-modal-title-size` alone moves no band**;
the band levers are the padding token and the close button's box, and the
button is deliberately left at 32x32 (it is the canonical `.action-btn` target
and the dialog's escape hatch: see the comment on `--modal-close-size`).

The `min-height` exists to collapse an accident: a header used to be 65px with
a close button and 56px without, purely because of whether the dialog offered
one. It is a floor, not a cap. A header carrying a `.modal-subtitle` second
line still grows past it, to ~61px, and that extra is content and is correct.

### Verifying a modal change

`frontend/src/devHarness/` force-renders 82 of the 83 dialogs with **no backend
and no login** (the 83rd is `ModalOverlay.tsx`, the shared wrapper, measured
indirectly by all the others). A committed baseline of selector-signature rows
plus per-dialog geometry lives beside it, so any change to modal type or chrome
is a before/after measurement rather than an argument:

```
node scripts/measure-modal-typography.mjs --diff            # what moved
node scripts/measure-modal-typography.mjs                   # re-baseline
node scripts/measure-modal-typography.mjs --viewport 1280x720 --out /tmp/x.json
```

Run `--diff` **before** you start and confirm it reads zero. A stale baseline
silently mixes someone else's landed work into your delta. The baseline is
captured at 1440x900 and `--diff` refuses any other viewport for that reason;
1280x720 (the minimum supported viewport) and 1920x1080 are checked with
`--viewport` + `--out`. Mobile is not a target, so the `max-width: 480/500/600px`
blocks in the modal estate are deliberately unverified. None of them is active
at either supported width.

## Theme Variables: Critical Rules

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
6. Am I using `--accent-primary` for a background? Stop. Use `--bg-tertiary` or `--button-primary-bg`.
7. Am I reaching for `opacity` to make text or a control's only glyph quieter? Stop. That is a group fade against the page, not an ink change. Use an ink token and record the measured ratio: [`opacity` de-emphasises the group, not the ink](#opacity-de-emphasises-the-group-not-the-ink).
