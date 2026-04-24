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

## Common CSS Classes (shared/common.css)

### Buttons
- `.btn-primary` — main action button (uses `--button-primary-bg/text`)
- `.btn-secondary` — secondary action (uses `--border-primary` bg)
- `.btn-danger` — destructive action (red)
- `.btn-cancel` — cancel/dismiss action

### Forms
- `.form-group` — wrapper: `label` + `input/select` with consistent spacing
- `.form-group label` — block label, 0.875rem, font-weight 500
- `.form-hint` — small helper text below inputs
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
- `.empty-state` — centered, dashed border, 64px icon, h3 + p

### Error/Warning/Success Banners
- `.error-banner` — red banner with icon + dismiss button
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

### Other
- `.search-box` — icon + input search field
- `.action-btn` — small icon-only action buttons
- `.drag-handle` — drag handle with grab cursor
- `.checkbox-group` / `.checkbox-option` — checkbox lists
- `.filter-dropdown` — multi-select filter dropdown

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
