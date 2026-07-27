import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const STALE_NAVIGATION = [
  /\btop navigation(?: bar)?\b/i,
  /\btab navigation(?: bar)?\b/i,
  /\bby tab\b/i,
  /\bper-tab\b/i,
  /\b(?:Channel Manager|M3U Manager|EPG Manager|Logo Manager|M3U Changes|Channel Pipeline|Guide|Journal|Stats|Settings|Auto-Creation) tab\b/i,
]

const REQUIRED_WORKSPACE_IMAGES = new Map([
  ['docs/images/user_guide/operator-workspace/1-channel-manager-1280-collapsed.png', [1280, 720]],
  ['docs/images/user_guide/operator-workspace/2-channel-manager-1920-health-expanded.png', [1920, 1080]],
  ['docs/images/user_guide/operator-workspace/3-channel-manager-1280-edit-actions.png', [1280, 720]],
  ['docs/images/user_guide/operator-workspace/4-channel-manager-1920-edit-collapsed.png', [1920, 1080]],
])

function markdownFiles(root) {
  const files = ['USER_GUIDE.md', 'docs/index.md']
  const walk = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name)
      if (entry.isDirectory()) walk(absolute)
      else if (entry.name.endsWith('.md')) files.push(path.relative(root, absolute))
    }
  }
  walk(path.join(root, 'docs/user_guide'))
  return files
}

function pngDimensions(file) {
  const header = fs.readFileSync(file).subarray(0, 24)
  if (header.length < 24 || header.toString('ascii', 1, 4) !== 'PNG') return null
  return [header.readUInt32BE(16), header.readUInt32BE(20)]
}

export function checkOperatorDocs(root = process.cwd()) {
  const errors = []
  for (const relative of markdownFiles(root)) {
    const absolute = path.join(root, relative)
    const content = fs.readFileSync(absolute, 'utf8')
    const lines = content.split('\n')

    lines.forEach((line, index) => {
      if (/[ \t]+$/.test(line)) errors.push(`${relative}:${index + 1}: trailing whitespace`)
      for (const pattern of STALE_NAVIGATION) {
        if (pattern.test(line)) errors.push(`${relative}:${index + 1}: stale navigation term: ${line.trim()}`)
      }
    })

    const links = content.matchAll(/!?\[[^\]]*]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g)
    for (const match of links) {
      const rawTarget = match[1]
      if (/^(?:https?:|mailto:|#)/.test(rawTarget)) continue
      const target = decodeURIComponent(rawTarget.split(/[?#]/, 1)[0])
      if (!target) continue
      const resolved = path.resolve(path.dirname(absolute), target)
      if (!fs.existsSync(resolved)) errors.push(`${relative}: missing local target: ${rawTarget}`)
    }
  }

  for (const [relative, expected] of REQUIRED_WORKSPACE_IMAGES) {
    const absolute = path.join(root, relative)
    if (!fs.existsSync(absolute)) {
      errors.push(`${relative}: required workspace image is missing`)
      continue
    }
    const actual = pngDimensions(absolute)
    if (!actual || actual[0] !== expected[0] || actual[1] !== expected[1]) {
      errors.push(`${relative}: expected ${expected.join('x')}, got ${actual?.join('x') ?? 'invalid PNG'}`)
    }
  }
  return errors
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const errors = checkOperatorDocs()
  if (errors.length) {
    console.error(errors.join('\n'))
    process.exitCode = 1
  } else {
    console.log('Operator documentation checks passed.')
  }
}
