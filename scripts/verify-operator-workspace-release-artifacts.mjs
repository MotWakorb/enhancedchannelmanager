import { readFile, readdir, stat } from 'node:fs/promises'
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
  if ((await stat(path)).size === 0) empty.push(name)
  const png = await readFile(path)
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
    const expectedClientWidth = expectedBoundingWidth - 1
    const sidebarDiagnostic = `border-box=${metadata.sidebarBoundingWidth}, client=${metadata.sidebarClientWidth}, scroll=${metadata.sidebarScrollWidth}`
    if (metadata.sidebarCollapsed !== expectedCollapsed) {
      metadataProblems.push(`${name}: expected ${expectedCollapsed ? 'collapsed' : 'expanded'} sidebar class; ${sidebarDiagnostic}`)
    }
    if (typeof metadata.sidebarBoundingWidth !== 'number'
      || Math.abs(metadata.sidebarBoundingWidth - expectedBoundingWidth) > 0.5) {
      metadataProblems.push(`${name}: expected ${expectedBoundingWidth}px sidebar border-box; ${sidebarDiagnostic}`)
    }
    if (typeof metadata.sidebarClientWidth !== 'number'
      || Math.abs(metadata.sidebarClientWidth - expectedClientWidth) > 0.5) {
      metadataProblems.push(`${name}: expected approximately ${expectedClientWidth}px sidebar client width; ${sidebarDiagnostic}`)
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
