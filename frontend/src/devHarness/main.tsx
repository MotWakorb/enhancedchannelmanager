/**
 * Entry point for the DEV-ONLY modal harness (bead
 * enhancedchannelmanager-xhldy.1).
 *
 * Reached only from `modal-harness.html`, which is only an entry of
 * `vite.harness.config.ts`. `vite.config.ts` — the production build — has a
 * single entry (`index.html`), so nothing in this directory is in the
 * production module graph. `harnessIsolation.test.ts` keeps it that way.
 *
 * The stylesheet imports mirror `src/main.tsx` exactly. They have to: the
 * measurement is only meaningful if the cascade the dialogs land in is the
 * same cascade the real app gives them. Each dialog component imports its own
 * CSS, so nothing else needs listing here.
 */
import ReactDOM from 'react-dom/client'
import { installApiStub } from './apiStub'
import { HarnessApp } from './HarnessApp'
import '@fontsource/material-icons'
import '../index.css'
import '../shared/common.css'

const params = new URLSearchParams(window.location.search)

// `?live=1` is for manual poking at a real backend only. The measurement
// script never sets it — baselines are always captured against the
// deterministic stub so a before/after diff shows CSS changes and nothing else.
installApiStub({ live: params.get('live') === '1' })

// Theme parity with the real app: dark is `:root` in index.css and App.tsx
// writes an EMPTY data-theme for it (SettingsTab.tsx:1200), so the harness
// leaves the attribute off unless a non-default theme is asked for.
const theme = params.get('theme')
if (theme && theme !== 'dark') document.documentElement.setAttribute('data-theme', theme)

ReactDOM.createRoot(document.getElementById('harness-root')!).render(
  // StrictMode is deliberately OFF. Its double-invoke re-runs the open-step
  // driver's effect, which would click the same control twice and toggle
  // some dialogs back shut.
  <HarnessApp />
)
