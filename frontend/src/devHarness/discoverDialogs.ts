/**
 * Source-derived dialog discovery for the dev-only modal harness
 * (bead enhancedchannelmanager-xhldy.1).
 *
 * The list of dialogs is NOT a hand-maintained array. It is computed from the
 * source text of every non-test `.tsx` under `src/`, using the same four
 * markers the estate was originally measured with:
 *
 *     modal-container | ModalOverlay | role="dialog" | role="alertdialog"
 *
 * Two independent implementations exist deliberately:
 *
 *  - this one, `import.meta.glob(..., '?raw')`, which runs in the browser and
 *    lets the harness index page show live discovery vs. declared coverage;
 *  - `harnessCoverage.test.ts`, which re-derives the same set from the
 *    filesystem with `fs` and fails if `dialogCatalog.ts` has drifted.
 *
 * The consequence that matters: a dialog added to this codebase next year
 * turns the coverage test RED until someone either stubs it or records it as
 * a deliberate gap. It cannot be silently missed.
 */

/** The four markers used to define "this file renders a dialog". */
export const DIALOG_MARKER_PATTERN = /modal-container|ModalOverlay|role="dialog"|role="alertdialog"/

const rawSources = import.meta.glob('../**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** `../components/Foo.tsx` -> `src/components/Foo.tsx` */
function toRepoPath(globKey: string): string {
  return globKey.replace(/^\.\.\//, 'src/')
}

/**
 * Vite keys files in the globbing module's OWN directory as `./Foo.tsx`, not
 * `../devHarness/Foo.tsx`, so the harness must exclude both spellings or it
 * discovers itself and reports permanent catalog drift.
 */
function isHarnessOwnFile(globKey: string): boolean {
  return globKey.startsWith('./') || globKey.startsWith('../devHarness/')
}

/**
 * Every file under `src/` (excluding tests and the harness itself) whose
 * source contains at least one dialog marker, as repo-relative paths.
 */
export function discoverDialogFiles(): string[] {
  return Object.entries(rawSources)
    .filter(([key]) => !/\.(test|spec)\.tsx$/.test(key))
    .filter(([key]) => !isHarnessOwnFile(key))
    .filter(([, source]) => DIALOG_MARKER_PATTERN.test(source))
    .map(([key]) => toRepoPath(key))
    .sort()
}
