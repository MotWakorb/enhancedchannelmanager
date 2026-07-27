import { readdir, stat } from 'node:fs/promises'
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
for (const name of expectedSorted.filter((candidate) => actual.includes(candidate))) {
  if ((await stat(resolve(artifactDirectory, name))).size === 0) empty.push(name)
}

if (missing.length || unexpected.length || empty.length || new Set(actual).size !== expected.length) {
  console.error('Operator workspace release artifact manifest is invalid.')
  if (missing.length) console.error(`Missing: ${missing.join(', ')}`)
  if (unexpected.length) console.error(`Unexpected: ${unexpected.join(', ')}`)
  if (empty.length) console.error(`Empty: ${empty.join(', ')}`)
  console.error(`Expected ${expected.length} unique PNGs; found ${new Set(actual).size}.`)
  process.exit(1)
}

console.log(`Verified ${expected.length} unique, non-empty operator workspace release screenshots.`)
