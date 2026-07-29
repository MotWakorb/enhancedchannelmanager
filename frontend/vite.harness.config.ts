import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Build config for the DEV-ONLY modal harness (bead
 * enhancedchannelmanager-xhldy.1).
 *
 * This is a deliberately SEPARATE file from `vite.config.ts` rather than a
 * `mode`-conditional branch inside it, for two reasons:
 *
 *  1. `vite.config.ts` is asserted on by
 *     `src/cssAudits/sharedClassChunkLeak.audit.test.ts`, which reimplements
 *     Rollup's default code-splitting to reason about CSS chunk membership.
 *     Anything that could change the production chunk graph — a second entry
 *     included, a `manualChunks`, a grouping knob — invalidates that guard.
 *     Keeping the harness out of that file keeps the production build byte-
 *     identical to what it was before the harness existed.
 *  2. It makes "the harness is not in the production bundle" a structural
 *     fact rather than a runtime `import.meta.env.DEV` check. `vite build`
 *     with the default config has exactly one entry (`index.html`); the
 *     harness entry is `modal-harness.html`, reachable only from here.
 *
 * Usage:
 *   npx vite build --config vite.harness.config.ts
 *   npx vite preview --config vite.harness.config.ts
 *   # then: http://127.0.0.1:4273/modal-harness.html?dialog=<id>
 *
 * `scripts/measure-modal-typography.mjs` drives both steps.
 */
const here = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  root: here,
  plugins: [react()],
  build: {
    outDir: resolve(here, '.modal-harness-dist'),
    emptyOutDir: true,
    // Single entry: the harness page. `index.html` is intentionally absent,
    // so a harness build can never be mistaken for a deployable app build.
    rollupOptions: {
      input: resolve(here, 'modal-harness.html'),
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4273,
    strictPort: true,
    // The harness stubs every /api call in-page (see src/devHarness/apiStub.ts)
    // so measurements are deterministic and no backend is required. The proxy
    // exists only for ad-hoc manual exploration with `?live=1`.
    proxy: {
      '/api': {
        target: process.env.ECM_HARNESS_API ?? 'http://localhost:6100',
        changeOrigin: true,
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5273,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.ECM_HARNESS_API ?? 'http://localhost:6100',
        changeOrigin: true,
      },
    },
  },
})
