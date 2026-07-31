import { readFile, readdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const artifactDirectory = resolve(process.cwd(), 'test-results/operator-workspace-release')
const viewports = ['1280x720', '1920x1080']
const states = [
  'populated-normal-expanded',
  'populated-normal-collapsed',
  'populated-edit-expanded',
  'populated-edit-collapsed',
  'populated-edit-selection-menu',
  'empty-expanded',
  'empty-collapsed',
  'empty-edit-expanded',
  'empty-edit-collapsed',
  'error-expanded',
  'error-collapsed',
  'health-and-artwork-matrix-expanded',
  'health-and-artwork-matrix-collapsed',
]
const expected = viewports.flatMap((viewport) =>
  states.map((state) => `operator-workspace--${viewport}--${state}.png`))
const requireIconMetadata = process.argv.includes('--require-icon-metadata')

let actual
try {
  actual = (await readdir(artifactDirectory)).filter((name) => name.endsWith('.png')).sort()
} catch (error) {
  console.error(`Release artifact directory is unavailable: ${artifactDirectory}`)
  console.error(error)
  process.exit(1)
}

const expectedSorted = [...expected].sort()
const missing = expectedSorted.filter((name) => !actual.includes(name))
const unexpected = actual.filter((name) => !expectedSorted.includes(name))
const empty = []
const invalidDimensions = []
for (const name of expectedSorted.filter((candidate) => actual.includes(candidate))) {
  const path = resolve(artifactDirectory, name)
  // The read is the only file system operation: stat()-ing for the size and
  // then reading is a check-then-use race (js/file-system-race) that can
  // report the size of one file and the bytes of another. The buffer answers
  // both questions from a single observation.
  const png = await readFile(path)
  if (png.length === 0) empty.push(name)
  const expectedViewport = name.includes('--1280x720--') ? [1280, 720] : [1920, 1080]
  if (png.length < 24 || png.readUInt32BE(16) !== expectedViewport[0] || png.readUInt32BE(20) !== expectedViewport[1]) {
    invalidDimensions.push(name)
  }
}

if (missing.length || unexpected.length || empty.length || invalidDimensions.length
  || new Set(actual).size !== expected.length) {
  console.error('Operator workspace release artifact manifest is invalid.')
  if (missing.length) console.error(`Missing: ${missing.join(', ')}`)
  if (unexpected.length) console.error(`Unexpected: ${unexpected.join(', ')}`)
  if (empty.length) console.error(`Empty: ${empty.join(', ')}`)
  if (invalidDimensions.length) console.error(`Wrong PNG dimensions: ${invalidDimensions.join(', ')}`)
  console.error(`Expected ${expected.length} unique PNGs; found ${new Set(actual).size}.`)
  process.exit(1)
}

if (requireIconMetadata) {
  const expectedMetadata = expectedSorted.map((name) => name.replace(/\.png$/, '.icons.json'))
  const actualMetadata = (await readdir(artifactDirectory))
    .filter((name) => name.endsWith('.icons.json'))
    .sort()
  const metadataProblems = []
  if (JSON.stringify(actualMetadata) !== JSON.stringify(expectedMetadata)) {
    metadataProblems.push(`expected ${expectedMetadata.length} exact icon metadata files; found ${actualMetadata.length}`)
  }
  for (const name of expectedMetadata.filter((candidate) => actualMetadata.includes(candidate))) {
    const metadata = JSON.parse(await readFile(resolve(artifactDirectory, name), 'utf8'))
    if (metadata.fontStatus !== 'loaded' || metadata.fontAvailable !== true) {
      metadataProblems.push(`${name}: Material Icons font was not ready`)
    }
    if (!Number.isInteger(metadata.visibleIconCount) || metadata.visibleIconCount <= 0) {
      metadataProblems.push(`${name}: no visible icons were audited`)
    }
    if (!Array.isArray(metadata.invalidIcons) || metadata.invalidIcons.length) {
      metadataProblems.push(`${name}: invalid or raw-ligature icons were reported`)
    }
    const expectedCollapsed = name.includes('-collapsed.icons.json')
    const expectedBoundingWidth = expectedCollapsed ? 68 : 244
    const sidebarDiagnostic = `border-box=${metadata.sidebarBoundingWidth}, client=${metadata.sidebarClientWidth}, scroll=${metadata.sidebarScrollWidth}`
    if (metadata.sidebarCollapsed !== expectedCollapsed) {
      metadataProblems.push(`${name}: expected ${expectedCollapsed ? 'collapsed' : 'expanded'} sidebar class; ${sidebarDiagnostic}`)
    }
    if (typeof metadata.sidebarBoundingWidth !== 'number'
      || Math.abs(metadata.sidebarBoundingWidth - expectedBoundingWidth) > 0.5) {
      metadataProblems.push(`${name}: expected ${expectedBoundingWidth}px sidebar border-box; ${sidebarDiagnostic}`)
    }
    // The content box must EQUAL the border box, because `.primary-sidebar`
    // carries no border: the sidebar/content divider is an `::after` overlay
    // chosen precisely so it leaves the 244px/68px box untouched
    // (frontend/src/components/TabNavigation.css). Stating that as an
    // invariant, rather than subtracting a hard-coded border width, is what
    // makes the check survive: it previously asserted `border-box - 1` and had
    // never run against this branch, so every artifact failed by exactly the
    // 1px border the sidebar does not have. If a border is ever added, this
    // fires with the three measured widths instead of silently absorbing it.
    if (typeof metadata.sidebarClientWidth !== 'number'
      || typeof metadata.sidebarBoundingWidth !== 'number'
      || Math.abs(metadata.sidebarClientWidth - metadata.sidebarBoundingWidth) > 0.5) {
      metadataProblems.push(`${name}: sidebar content box must equal its border box — .primary-sidebar has no border; ${sidebarDiagnostic}`)
    }
    if (metadata.sidebarWidthSettled !== true
      || metadata.sidebarClientWidth !== metadata.sidebarScrollWidth) {
      metadataProblems.push(`${name}: sidebar client/scroll width was not settled after font loading; ${sidebarDiagnostic}`)
    }
  }
  if (metadataProblems.length) {
    console.error('Operator workspace icon-readiness metadata is invalid.')
    for (const problem of metadataProblems) console.error(problem)
    process.exit(1)
  }
  console.log(`Verified Material Icons readiness metadata for ${expectedMetadata.length} screenshots.`)
}

console.log(`Verified ${expected.length} unique, non-empty operator workspace release screenshots.`)
