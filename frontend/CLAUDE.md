# Frontend Agent Instructions

> Full system architecture diagram: `docs/architecture.md`

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

- **File pairing**: `ComponentName.tsx` + `ComponentName.css` + `ComponentName.test.tsx`
- **PascalCase** for components, **camelCase** for utilities/hooks
- **CustomSelect** for all dropdowns — never use native `<select>`
- Modals named `*Modal.tsx`, base styles in `ModalBase.css`
- Icons: `<span className="material-icons">icon_name</span>`

## State Management

- **No Redux** — state centralized in `App.tsx` via `useState` hooks, passed as props
- **Context** for cross-cutting concerns: `AuthContext`, `NotificationContext`
- **Custom hooks** for complex logic: `useEditMode`, `useChangeHistory`, `useSelection`
- **localStorage** for persisted filters: `streamProviderFilters`, `streamGroupFilters`

## CSS & Styling

- **CSS variables** in `:root` (dark theme default) — defined in `index.css`
- **BEM-inspired** naming: `.component-name`, `.component-name-child`, `.component-name-item`
- **State classes**: `.is-active`, `.is-disabled`, `.is-loading`, `.active`, `.filter-active`
- **Component-scoped CSS** — each component has its own `.css` file
- No CSS modules or styled-components

Key variables:
```css
--bg-primary: #1e1e23;     --text-primary: rgba(255, 255, 255, 0.95);
--bg-secondary: #252530;   --text-secondary: #a8a8b8;
--accent-50: rgba(100, 108, 255, 0.5);
--success: #10b981;  --error: #ef4444;  --warning: #f59e0b;
```

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

```typescript
// services/api.ts — named exports per endpoint
export async function getChannels(): Promise<Channel[]>
export async function getEPGSources(): Promise<EPGSource[]>
export async function getChannelGroups(): Promise<ChannelGroup[]>
```

- Uses `fetchJson()` from `httpClient.ts` for all HTTP calls
- Endpoints match backend routes (e.g., `/api/channels`, `/api/m3u`)

## Testing

- Run: `npm test` (984 tests, Vitest + @testing-library/react)
- Run watch: `npm run test:watch`
- **MSW** mocks API responses in `src/test/mocks/`
- Test setup in `src/test/setup.ts` (mocks matchMedia, ResizeObserver, IntersectionObserver)
- Tests colocated with components: `Component.test.tsx`

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
