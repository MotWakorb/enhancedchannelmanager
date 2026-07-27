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
  const docs = path.join(root, 'docs')
  for (const entry of fs.readdirSync(docs, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith('.md')) files.push(`docs/${entry.name}`)
  }
  for (const directory of ['user_guide', 'runbooks']) {
    const target = path.join(docs, directory)
    if (fs.existsSync(target)) walk(target)
  }
  return [...new Set(files)]
}

function pngDimensions(file) {
  const header = fs.readFileSync(file).subarray(0, 24)
  if (header.length < 24 || header.toString('ascii', 1, 4) !== 'PNG') return null
  return [header.readUInt32BE(16), header.readUInt32BE(20)]
}

function normalizedMarkdown(content) {
  return content
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[*_`~]/g, '')
    .replace(/\s+/g, ' ')
}

function headingAnchors(content) {
  const anchors = new Set()
  const counts = new Map()
  for (const line of content.split('\n')) {
    const heading = line.match(/^#{1,6}\s+(.+?)\s*#*\s*$/)
    if (!heading) continue
    const explicit = heading[1].match(/\{#([^}]+)\}\s*$/)
    let slug = explicit?.[1] ?? heading[1]
      .replace(/\{#([^}]+)\}\s*$/, '')
      .replace(/<[^>]+>/g, '')
      .replace(/[*`~]/g, '')
      .toLowerCase()
      .trim()
      .replace(/[^\p{L}\p{N}_\s-]/gu, '')
      .replace(/[-\s]+/g, '-')
    const duplicate = counts.get(slug) ?? 0
    counts.set(slug, duplicate + 1)
    if (duplicate) slug = `${slug}-${duplicate}`
    anchors.add(slug)
  }
  return anchors
}

export function checkOperatorDocs(root = process.cwd()) {
  const errors = []
  for (const relative of markdownFiles(root)) {
    const absolute = path.join(root, relative)
    const content = fs.readFileSync(absolute, 'utf8')
    const lines = content.split('\n')

    lines.forEach((line, index) => {
      if (/[ \t]+$/.test(line)) errors.push(`${relative}:${index + 1}: trailing whitespace`)
    })
    const normalized = normalizedMarkdown(content)
    for (const pattern of STALE_NAVIGATION) {
      if (pattern.test(normalized)) errors.push(`${relative}: stale navigation term matching ${pattern}`)
    }

    const links = content.matchAll(/!?\[[^\]]*]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g)
    for (const match of links) {
      const rawTarget = match[1]
      if (/^(?:https?:|mailto:)/.test(rawTarget)) continue
      if (rawTarget === 'url') continue
      const [pathPart, fragmentPart] = rawTarget.split('#', 2)
      const target = decodeURIComponent(pathPart.split('?', 1)[0])
      const resolved = target ? path.resolve(path.dirname(absolute), target) : absolute
      if (!fs.existsSync(resolved)) errors.push(`${relative}: missing local target: ${rawTarget}`)
      else if (fragmentPart && fs.statSync(resolved).isFile() && resolved.endsWith('.md')) {
        const fragment = decodeURIComponent(fragmentPart).toLowerCase()
        const anchors = headingAnchors(fs.readFileSync(resolved, 'utf8'))
        if (!anchors.has(fragment)) errors.push(`${relative}: missing fragment #${fragmentPart} in ${rawTarget}`)
      }
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
