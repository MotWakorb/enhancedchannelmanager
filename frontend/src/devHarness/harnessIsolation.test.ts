/**
 * The guard that keeps the dev-only modal harness out of the shipped app
 * (bead enhancedchannelmanager-xhldy.1).
 *
 * "It's behind `import.meta.env.DEV`" is not good enough: that only strips
 * code Rollup can prove is unreachable, and it does nothing about a stray
 * import pulling harness modules — and their stub data — into the production
 * graph in the first place. The harness relies on a structural property
 * instead, and this test pins it:
 *
 *   1. `vite.config.ts` (the production build) never mentions the harness,
 *      so `vite build` keeps its single `index.html` entry and the harness
 *      is simply not in the module graph.
 *   2. No file outside `src/devHarness/` imports anything from it, so there
 *      is no path by which it could enter that graph.
 *
 * Verified end-to-end at least once by building for production and grepping
 * `dist/` for the harness marker; this test is the cheap continuous version.
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const FRONTEND = path.resolve(__dirname, '../..')
const SRC = path.resolve(FRONTEND, 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else if (entry.isFile()) out.push(full)
  }
  return out
}

describe('modal harness isolation', () => {
  it('is not reachable from any app file', () => {
    const offenders = walk(SRC)
      .filter((f) => /\.(ts|tsx)$/.test(f))
      .filter((f) => !f.includes(`${path.sep}devHarness${path.sep}`))
      .filter((f) => /from\s+['"][^'"]*devHarness/.test(fs.readFileSync(f, 'utf8')))
      .map((f) => path.relative(FRONTEND, f))

    expect(
      offenders,
      'A production file imports from src/devHarness/. That puts the harness — and its ' +
        'stub API responses — into the shipped bundle. Move whatever is shared into a ' +
        'normal module instead.'
    ).toEqual([])
  })

  it('leaves the production vite config with a single, harness-free entry', () => {
    const config = fs.readFileSync(path.resolve(FRONTEND, 'vite.config.ts'), 'utf8')

    expect(
      config,
      'vite.config.ts references the harness. It must not: the harness has its own ' +
        'config (vite.harness.config.ts) precisely so the production build stays ' +
        'byte-identical to what it was before the harness existed.'
    ).not.toMatch(/harness/i)

    expect(
      config,
      'vite.config.ts declares rollupOptions.input. Vite defaults to the single ' +
        'index.html entry; adding entries is how the harness would sneak in, and it ' +
        'also changes the chunk graph that sharedClassChunkLeak.audit.test.ts models.'
    ).not.toMatch(/rollupOptions/)
  })

  it('keeps the harness entry out of index.html', () => {
    const html = fs.readFileSync(path.resolve(FRONTEND, 'index.html'), 'utf8')
    expect(html).not.toMatch(/devHarness|modal-harness/)
  })
})
