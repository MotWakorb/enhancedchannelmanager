/**
 * The guard that makes "a dialog added next year is covered automatically"
 * true rather than aspirational (bead enhancedchannelmanager-xhldy.1).
 *
 * `dialogCatalog.ts` says what the harness does about each dialog. This test
 * re-derives the SET OF FILES CONTAINING DIALOGS straight from the source
 * tree — independently of `discoverDialogs.ts`, which does the same thing
 * with `import.meta.glob` in the browser — and fails if the two disagree.
 *
 * So the failure modes are:
 *   - a new dialog file appears and nobody catalogues it  -> RED
 *   - a dialog file is deleted or stops being a dialog     -> RED
 *   - a catalogued dialog loses its render recipe          -> `tsc --noEmit`
 *     (see the `satisfies` at the bottom of dialogRenderers.tsx)
 *
 * None of those can be reached by "measured what we could, reasoned about
 * the rest", which is the failure this harness exists to remove.
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { DIALOG_CATALOG, catalogFiles } from './dialogCatalog'

const SRC = path.resolve(__dirname, '..')

/** Must stay identical to DIALOG_MARKER_PATTERN in discoverDialogs.ts. */
const MARKERS = /modal-container|ModalOverlay|role="dialog"|role="alertdialog"/

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else if (entry.isFile()) out.push(full)
  }
  return out
}

function discoverFromDisk(): string[] {
  return walk(SRC)
    .filter((f) => f.endsWith('.tsx'))
    .filter((f) => !/\.(test|spec)\.tsx$/.test(f))
    .filter((f) => !f.includes(`${path.sep}devHarness${path.sep}`))
    .filter((f) => MARKERS.test(fs.readFileSync(f, 'utf8')))
    .map((f) => `src/${path.relative(SRC, f).split(path.sep).join('/')}`)
    .sort()
}

describe('modal harness coverage', () => {
  it('catalogues every source file that renders a dialog, and nothing else', () => {
    const onDisk = discoverFromDisk()
    const declared = catalogFiles()

    const missing = onDisk.filter((f) => !declared.includes(f))
    const stale = declared.filter((f) => !onDisk.includes(f))

    expect(
      missing,
      'These files match the dialog markers but have no entry in dialogCatalog.ts. ' +
        'Add one per dialog they contain — status "stubbed" with a recipe in ' +
        'dialogRenderers.tsx, or status "gap" with a reason. Do not delete this test.'
    ).toEqual([])

    expect(
      stale,
      'dialogCatalog.ts references files that no longer contain a dialog. ' +
        'Delete the stale entries (and their recipes).'
    ).toEqual([])
  })

  it('gives every dialog a unique id', () => {
    const ids = DIALOG_CATALOG.map((e) => e.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('requires a reason on every declared gap', () => {
    const unexplained = DIALOG_CATALOG.filter(
      (e) => e.status === 'gap' && !(e as { reason?: string }).reason?.trim()
    )
    expect(
      unexplained.map((e) => e.id),
      'A gap without a reason is exactly the silent hole this harness exists to prevent.'
    ).toEqual([])
  })

  it('keeps ids URL-safe, because ?dialog=<id> addresses them', () => {
    const bad = DIALOG_CATALOG.filter((e) => !/^[a-z0-9-]+$/.test(e.id))
    expect(bad.map((e) => e.id)).toEqual([])
  })
})
