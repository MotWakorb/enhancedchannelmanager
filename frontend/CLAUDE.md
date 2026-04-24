# Frontend Agent Instructions

> Full system architecture diagram: `docs/architecture.md`
> Coding conventions (naming, file pairing, components, hooks, CSS naming,
> state classes, API layer, tests): `docs/style_guide.md` is canonical.
> This file covers stack/structure orientation only — it defers to the
> style guide for any rule about how code should be written.

## Framework & Stack

- **React 18** + **TypeScript** (strict mode) + **Vite** build tool
- Entry: `src/main.tsx` → `AuthProvider` → `ProtectedRoute` → `App`
- Dev server: port 5173, proxies `/api` to `http://localhost:8000`

## Directory Structure

```
frontend/src/
├── App.tsx                    # Main app, centralized state via useState hooks
├── TabNavigation.tsx          # Tab switching (data-tab attributes)
├── main.tsx                   # Entry point
├── index.css                  # CSS variables & theme
├── components/                # ~60+ components
│   ├── tabs/                  # Tab content (M3UManagerTab, EPGManagerTab, etc.)
│   ├── autoCreation/          # Rule builder (ActionEditor, ConditionEditor, RuleBuilder)
│   ├── ffmpegBuilder/         # FFmpeg configuration
│   ├── settings/              # Settings subsections
│   ├── CustomSelect.tsx/.css  # Custom dropdown (replaces native <select>)
│   ├── ChannelsPane.tsx       # Channel management (~274KB)
│   ├── ScheduledTasksSection.tsx
│   └── [modals, editors, panels...]
├── contexts/                  # React Context providers
│   └── NotificationContext.tsx # Toast notification system
├── hooks/                     # Custom hooks (~15 files)
│   ├── useAuth.tsx            # Auth state
│   ├── useEditMode.ts         # Edit mode with change tracking
│   ├── useChangeHistory.ts    # Undo/redo
│   └── useAsyncOperation.ts   # Async loading/error tracking
├── services/                  # API layer
│   ├── api.ts                 # Main API client (~2600 lines)
│   ├── httpClient.ts          # fetchJson(), buildQuery()
│   └── autoCreationApi.ts     # Auto-creation endpoints
├── types/                     # TypeScript definitions
│   ├── index.ts               # All shared types (~37KB)
│   └── autoCreation.ts        # Auto-creation types
└── utils/                     # Utility functions
```

## Component Conventions

See `docs/style_guide.md` — sections **Naming Conventions**, **Filenames**,
and **Module Organization → Frontend (React)** — for the canonical rules
on file pairing, casing, the `CustomSelect` requirement, modal naming,
and Material Icons usage.

## State Management

- **No Redux** — state centralized in `App.tsx` via `useState` hooks, passed as props
- **Context** for cross-cutting concerns: `AuthContext`, `NotificationContext`
- **Custom hooks** for complex logic: `useEditMode`, `useChangeHistory`, `useSelection`
- **localStorage** for persisted filters: `streamProviderFilters`, `streamGroupFilters`

## CSS & Styling

CSS naming, state classes, layer architecture, theme variables, and the
shared-class catalog live in `docs/css_guidelines.md` (authoritative for
CSS) with a summary in `docs/style_guide.md` → **CSS Conventions**.

Quick orientation only: theme variables live in `:root` in `index.css`,
the dark theme is the default, and each component owns its own `.css`
file (no CSS modules, no styled-components).

## Tab Navigation

```typescript
type TabId = 'm3u-manager' | 'epg-manager' | 'channel-manager' | 'guide' |
             'logo-manager' | 'm3u-changes' | 'auto-creation' | 'journal' | 'stats' | 'settings'
```

- Tabs have `data-tab={tab.id}` attribute on buttons
- Active state: `.tab-button.active` class
- Tab content lazy-loaded with `React.lazy()` + `Suspense`
- FFMPEG Builder tab also exists (id: `ffmpeg-builder`)

## Types

- Main types in `src/types/index.ts`: `Channel`, `Stream`, `EPGSource`, `ChannelGroup`, etc.
- Auto-creation types in `src/types/autoCreation.ts`: `Action`, `Condition`, `Rule`
- Request types: `*CreateRequest`, `*UpdateRequest`
- Response types: `*Response`

## API Layer

Convention rules (named exports per endpoint, all HTTP via `fetchJson()`)
are in `docs/style_guide.md` → **Module Organization → Frontend (React)**.
Quick orientation:

```typescript
// services/api.ts — named exports per endpoint
export async function getChannels(): Promise<Channel[]>
export async function getEPGSources(): Promise<EPGSource[]>
export async function getChannelGroups(): Promise<ChannelGroup[]>
```

Endpoints match backend routes (e.g., `/api/channels`, `/api/m3u`).

## Testing

Test conventions (naming, AAA structure, colocation, MSW setup) live in
`docs/style_guide.md` → **Test Conventions**, with deeper testing strategy
in `docs/testing.md`.

Quick commands:

- Run: `npm test` (Vitest + @testing-library/react)
- Run watch: `npm run test:watch`

## Build & Deploy

```bash
cd frontend && npm run build       # Output to dist/
docker exec ecm-ecm-1 sh -c 'rm -rf /app/static/assets/*'  # Clean stale bundles
docker cp dist/. ecm-ecm-1:/app/static/
```

Always clean `/app/static/assets/` before copying — `docker cp` only adds files, never removes old bundles.

## CSS Class Patterns for E2E Tests

- `.channels-pane`, `.streams-pane` — Main pane containers
- `.tab-navigation`, `.tab-button` — Tab nav
- `.settings-tab`, `.settings-nav-item` — Settings page
- `.filter-active` — Active filter indicator (logic: true when any filter is NOT showing all)
- `data-testid` attributes on key elements for stable E2E selectors
- `action-input` class for form fields in action editor
- `order-number-input` class for reorder number in card headers only
