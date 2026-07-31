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

const STALE_WORKFLOWS = [
  /\bclick commit\b/i,
  /\bclick exit edit mode\b/i,
  /\bclick add source\b/i,
  /\bchannel pipeline.{0,80}\badd rule\b/i,
  /\bright-click on selected channels\b/i,
  /\bctrl\+click\b/i,
  /\bctrl\+a\b/i,
  /\b(?:click|use)\s+bulk remove\b/i,
  /\bclick the x on a stream\b/i,
  /\bclick the copy icon on any (?:channel|stream)\b/i,
  /\bclick the play icon on any (?:channel|stream)\b/i,
  /\bepg manager.{0,240}\bclick save\b/i,
  /\bchannel pipeline.{0,240}\bclick save\b/i,
  /\bclick add method\b/i,
  /\bnotification center from the bell icon\b/i,
  /\blogin with dispatcharr\b/i,
  /\bprimary auth mode\b/i,
  /\benable dispatcharr authentication\b/i,
  /\bmanage (?:accounts|users) in settings\s*(?:→|->)\s*users\b/i,
  /\blink multiple authentication methods\b/i,
]

const REQUIRED_WORKSPACE_IMAGES = new Map([
  ['docs/images/user_guide/operator-workspace/1-channel-manager-1280-collapsed.png', [1280, 720]],
  ['docs/images/user_guide/operator-workspace/2-channel-manager-1920-health-expanded.png', [1920, 1080]],
  ['docs/images/user_guide/operator-workspace/3-channel-manager-1280-edit-actions.png', [1280, 720]],
  ['docs/images/user_guide/operator-workspace/4-channel-manager-1920-edit-collapsed.png', [1920, 1080]],
])

/** True for the errors that mean "this path is not there", however it is not there. */
function isMissing(error) {
  return error.code === 'ENOENT' || error.code === 'ENOTDIR' || error.code === 'EISDIR'
}

function markdownFiles(root) {
  const files = ['USER_GUIDE.md', 'docs/index.md']
  // Optional directories are handled by attempting the read and treating the
  // failure as absence. Probing with existsSync first is a check-then-use race
  // (js/file-system-race) and answers a question the readdir already answers.
  const walk = (directory) => {
    let entries
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true })
    } catch (error) {
      if (isMissing(error)) return
      throw error
    }
    for (const entry of entries) {
      const absolute = path.join(directory, entry.name)
      if (entry.isDirectory()) walk(absolute)
      else if (entry.name.endsWith('.md')) files.push(path.relative(root, absolute))
    }
  }
  const docs = path.join(root, 'docs')
  for (const entry of fs.readdirSync(docs, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith('.md')) files.push(`docs/${entry.name}`)
  }
  for (const directory of ['user_guide', 'runbooks']) walk(path.join(docs, directory))
  return [...new Set(files)]
}

/** Dimensions, or null when the bytes are not a PNG. Throws when the file is unreadable. */
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

/**
 * Drop inline HTML so a heading's markup contributes no tokens to its anchor,
 * matching what the renderers slug from the heading's text content.
 *
 * Repeated to a fixed point, which is the contract this function owes its
 * caller: no tag-shaped span survives, whatever the pattern happens to be.
 *
 * A single global pass of THIS pattern already satisfies that — `<[^>]+>` is
 * idempotent, because a `<` only survives when no `>` follows it anywhere, and
 * then no `>` survives after it either, so a removal can never splice a new tag
 * together. (Verified exhaustively over every string up to length 12 in
 * `{<, >, a}`.) CodeQL's js/incomplete-multi-character-sanitization does not
 * model that and reports it, so this is a false positive on today's pattern.
 *
 * The loop is here anyway rather than a per-alert dismissal, because the
 * idempotence is a property of the pattern and not of the intent: narrowing it
 * to something like `<[a-z][^>]*>` — a reasonable future edit — makes it
 * genuinely incomplete, leaving `<b>` from `<<a>b>` on a single pass. Repeating
 * to a fixed point costs one no-op pass per heading and makes the contract
 * independent of the pattern. Anchors are unchanged for every heading in the
 * docs tree.
 */
function stripInlineHtml(text) {
  let current = text
  let previous
  do {
    previous = current
    current = current.replace(/<[^>]+>/g, '')
  } while (current !== previous)
  return current
}

function headingAnchors(content) {
  const anchors = new Set()
  const counts = new Map()
  for (const line of content.split('\n')) {
    const heading = line.match(/^#{1,6}\s+(.+?)\s*#*\s*$/)
    if (!heading) continue
    const explicit = heading[1].match(/\{#([^}]+)\}\s*$/)
    let slug = explicit?.[1] ?? stripInlineHtml(heading[1].replace(/\{#([^}]+)\}\s*$/, ''))
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
    for (const pattern of [...STALE_NAVIGATION, ...STALE_WORKFLOWS]) {
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
      if (fragmentPart && resolved.endsWith('.md')) {
        // One operation, not a check followed by a use: reading the target both
        // proves it is there and yields the anchors. Testing existence first is
        // the js/file-system-race shape and can disagree with the read.
        let targetContent
        try {
          targetContent = fs.readFileSync(resolved, 'utf8')
        } catch (error) {
          // EISDIR is the old `statSync().isFile()` guard: a directory named
          // `*.md` has no anchors but is not a broken link either.
          if (error.code === 'EISDIR') continue
          if (!isMissing(error)) throw error
          errors.push(`${relative}: missing local target: ${rawTarget}`)
          continue
        }
        const fragment = decodeURIComponent(fragmentPart).toLowerCase()
        const anchors = headingAnchors(targetContent)
        if (!anchors.has(fragment)) errors.push(`${relative}: missing fragment #${fragmentPart} in ${rawTarget}`)
      } else if (!fs.statSync(resolved, { throwIfNoEntry: false })) {
        errors.push(`${relative}: missing local target: ${rawTarget}`)
      }
    }
  }

  for (const [relative, expected] of REQUIRED_WORKSPACE_IMAGES) {
    // Same check-then-use reasoning as the link targets above: the read is what
    // decides both "is it there" and "is it the right size".
    let actual
    try {
      actual = pngDimensions(path.join(root, relative))
    } catch (error) {
      if (!isMissing(error)) throw error
      errors.push(`${relative}: required workspace image is missing`)
      continue
    }
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
