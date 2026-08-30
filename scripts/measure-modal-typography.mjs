#!/usr/bin/env node
/**
 * Walk the dev-only modal harness and dump computed typography for every
 * dialog (bead enhancedchannelmanager-xhldy.1).
 *
 * This is the before/after instrument for putting modals on the P1 type
 * scale. Run it once on the current tree to capture a baseline, do the CSS
 * work, run it again, and diff.
 *
 *   node scripts/measure-modal-typography.mjs                  # write baseline
 *   node scripts/measure-modal-typography.mjs --out after.json # write elsewhere
 *   node scripts/measure-modal-typography.mjs --diff           # compare to baseline
 *   node scripts/measure-modal-typography.mjs --only edit-channel,merge-channels
 *   node scripts/measure-modal-typography.mjs --no-build       # reuse last harness build
 *   node scripts/measure-modal-typography.mjs --viewport 1280x720
 *
 * WHY --viewport
 * -------------
 * The baseline is captured at 1440x900 and that is deliberately the default, so
 * `--diff` always compares like with like. But 1280x720 is the minimum
 * supported viewport and 1920x1080 is the common one, and a modal's GEOMETRY
 * (container width, body max-height, whether the footer wraps, whether a row
 * clips) is viewport-dependent even when its typography is not. Passing an
 * explicit viewport lets a pass check both without disturbing the baseline;
 * write those runs to `--out`, never over the baseline
 * (bead enhancedchannelmanager-xhldy).
 *
 * WHY NOT A LIVE APP + LOGIN
 * --------------------------
 * Earlier rounds of this work drove the real app at :6100 as `e2e_test`, and
 * could only reach the dialogs this instance's data happens to allow — no
 * pending merges, empty review queues, no probe results. The harness serves
 * every dialog from a deterministic in-page stub instead, so (a) all of them
 * are reachable and (b) the "after" run measures the same DOM as the
 * "before" run rather than whatever was in the database that day. No backend
 * and no login are involved.
 *
 * WHAT IS MEASURED
 * ----------------
 * Per dialog, every element that actually renders text (a non-empty direct
 * text node) plus every form control, aggregated by *selector signature*
 * (`tag.class.class`). Element-by-element records would balloon the artefact
 * and churn on list length; signatures are what a CSS change actually moves,
 * and they diff cleanly: "`.modal-title` was 17.6px/600, now 15px/600".
 *
 * WHY EVERY CAPTURE IS ANIMATION-FROZEN
 * ------------------------------------
 * `ModalBase.css` opens every dialog with `modal-container-slide-in`: 0.2s,
 * `translateY(-20px) scale(0.98)` -> `translateY(0) scale(1)`. A capture taken
 * while that is still running multiplies EVERY box in the dialog by a
 * run-dependent scale factor, so the geometry arm reports phantom movement in
 * both directions on any change at all. Measured on bead
 * `enhancedchannelmanager-iotbh`: in a single unfrozen run the 32px close
 * button reported seven distinct sizes across the 81 dialogs (31.6, 31.74,
 * 31.8, 31.85, 31.9, 31.99 and 32.0), and a change that could only shrink type
 * by 0.33px moved 214 of 281 boxes by 1-11px.
 *
 * So this script freezes animations and transitions before it measures, on
 * every dialog, with no flag to turn it off: there is no question this
 * instrument answers that wants a mid-flight box. `.modal-container` declares
 * no `transform` of its own, so suppressing the animation leaves it at exactly
 * the resting state the keyframe was travelling to. TYPOGRAPHY rows were never
 * affected, because `font-size`/`weight`/`family` are not scaled by a transform.
 * Comparisons this instrument produced before the freeze therefore remain valid
 * on the typography arm; only the geometry arm was untrustworthy.
 */
import { spawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const FRONTEND = resolve(REPO, 'frontend')
const DEFAULT_BASELINE = resolve(
  FRONTEND,
  'src/devHarness/baseline/modal-typography.baseline.json'
)
const PREVIEW_URL = 'http://127.0.0.1:4273'
const BASELINE_VIEWPORT = { width: 1440, height: 900 }

const argv = process.argv.slice(2)
const flag = (name) => argv.includes(`--${name}`)
const value = (name, fallback) => {
  const i = argv.indexOf(`--${name}`)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}

const outPath = resolve(REPO, value('out', DEFAULT_BASELINE))
const only = value('only', null)?.split(',').map((s) => s.trim())

/** `--viewport 1280x720`; defaults to the baseline's 1440x900. */
const VIEWPORT = (() => {
  const raw = value('viewport', null)
  if (!raw) return BASELINE_VIEWPORT
  const m = /^(\d+)x(\d+)$/.exec(raw.trim())
  if (!m) throw new Error(`--viewport wants WxH, e.g. 1280x720; got ${raw}`)
  return { width: Number(m[1]), height: Number(m[2]) }
})()

/** A --diff against the committed baseline is only meaningful at its viewport. */
if (flag('diff') && (VIEWPORT.width !== BASELINE_VIEWPORT.width || VIEWPORT.height !== BASELINE_VIEWPORT.height)) {
  throw new Error(
    `--diff compares against a baseline captured at ${BASELINE_VIEWPORT.width}x${BASELINE_VIEWPORT.height}; ` +
      `re-run without --viewport, or capture with --out and diff the two files yourself.`
  )
}

/** ------------------------------------------------------------------ */

function run(cmd, args, opts = {}) {
  return new Promise((ok, fail) => {
    const p = spawn(cmd, args, { stdio: 'inherit', ...opts })
    p.on('exit', (code) => (code === 0 ? ok() : fail(new Error(`${cmd} exited ${code}`))))
    p.on('error', fail)
  })
}

async function waitForServer(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/modal-harness.html`)
      if (res.ok) return
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`harness preview server did not come up at ${url}`)
}

/**
 * Suppress every animation and transition, so a box is measured at rest.
 *
 * Same override, and the same `!important` reasoning, as `FREEZE_ANIMATION` in
 * `e2e/contrast-aa.spec.ts`: the harness's route chunk stylesheets land in
 * <head> after this tag, so ordinary specificity would let them win. It is
 * repeated here rather than imported because that constant lives inside a
 * Playwright TypeScript spec that this plain-ESM script cannot load.
 */
const FREEZE_ANIMATION = `
*, *::before, *::after {
  transition: none !important;
  animation: none !important;
}`

/**
 * Freeze, then wait for the page to actually be at rest before measuring.
 *
 * The style tag alone is enough for the slide-in, because dropping `animation`
 * reverts `.modal-container` to its declared (untransformed) style
 * immediately. The two extra steps cover the rest: `getAnimations()` catches
 * anything script-driven that the stylesheet override cannot stop (the Web
 * Animations API ignores CSS `animation: none`), and the double
 * `requestAnimationFrame` lets the resulting style change reach layout, so
 * `getBoundingClientRect` reads the post-freeze box rather than the one being
 * left behind.
 */
async function freezeAndSettle(page) {
  await page.addStyleTag({ content: FREEZE_ANIMATION })
  await page.evaluate(async () => {
    for (const animation of document.getAnimations()) {
      try {
        animation.finish()
      } catch {
        /* an infinite animation cannot be finished; cancelling is enough */
        animation.cancel()
      }
    }
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  })
}

/**
 * Runs in the page. Collects computed typography for the dialog on screen.
 *
 * `[data-harness-chrome]` is excluded so the harness's own index/status UI
 * can never contaminate a measurement. Everything else in <body> is in
 * scope on purpose: several dialogs (SelectionActionBar, the multi-select
 * listbox, CustomSelect menus) render through a portal into document.body
 * and would be invisible to a container-scoped query.
 */
const COLLECT = () => {
  const PROPS = [
    'font-size',
    'font-weight',
    'line-height',
    'font-family',
    'letter-spacing',
    'text-transform',
    'font-style',
  ]

  const hasOwnText = (el) =>
    Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? '').trim().length > 0)

  const isControl = (el) => ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'OPTION'].includes(el.tagName)

  const signature = (el) => {
    const classes = Array.from(el.classList)
      .filter((c) => !/^_|^css-/.test(c))
      .sort()
    return `${el.tagName.toLowerCase()}${classes.map((c) => `.${c}`).join('')}`
  }

  const inChrome = (el) => el.closest('[data-harness-chrome]') !== null

  const bySignature = new Map()
  const fontSizeHistogram = {}
  let elementCount = 0

  for (const el of document.body.querySelectorAll('*')) {
    if (inChrome(el)) continue
    const cs = getComputedStyle(el)
    if (cs.display === 'none' || cs.visibility === 'hidden') continue

    elementCount += 1
    fontSizeHistogram[cs.fontSize] = (fontSizeHistogram[cs.fontSize] ?? 0) + 1

    if (!hasOwnText(el) && !isControl(el)) continue

    const style = {}
    for (const p of PROPS) style[p] = cs.getPropertyValue(p)

    const sig = signature(el)
    const key = `${sig}|${JSON.stringify(style)}`
    const existing = bySignature.get(key)
    if (existing) {
      existing.count += 1
    } else {
      bySignature.set(key, {
        signature: sig,
        style,
        count: 1,
        sample: (el.textContent ?? '').trim().slice(0, 60),
      })
    }
  }

  /**
   * Geometry, so a viewport run can report what a type change did to the box —
   * the chrome heights and the tallest repeated row. Typography is
   * viewport-independent above the modal media queries (600px), but geometry is
   * not, which is the whole reason for measuring two viewports
   * (bead enhancedchannelmanager-xhldy).
   */
  const box = (sel) => {
    const el = document.querySelector(sel)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { w: Math.round(r.width * 100) / 100, h: Math.round(r.height * 100) / 100 }
  }
  const rowHeights = {}
  for (const sel of ['.modal-btn', '.modal-form-group', '.modal-stepper-item', '.dropdown-option', '.modal-summary-item']) {
    const els = [...document.querySelectorAll(sel)]
    if (!els.length) continue
    const hs = els.map((e) => Math.round(e.getBoundingClientRect().height * 100) / 100)
    rowHeights[sel] = { n: hs.length, min: Math.min(...hs), max: Math.max(...hs) }
  }
  const docEl = document.documentElement
  const geometry = {
    container: box('.modal-container'),
    header: box('.modal-header'),
    body: box('.modal-body'),
    footer: box('.modal-footer'),
    closeBtn: box('.modal-close-btn'),
    rowHeights,
    // a horizontal scrollbar on <body> is the failure this is really watching
    horizontalOverflow: docEl.scrollWidth > docEl.clientWidth,
    scrollWidth: docEl.scrollWidth,
    clientWidth: docEl.clientWidth,
  }

  return {
    elementCount,
    fontSizeHistogram,
    geometry,
    rows: Array.from(bySignature.values()).sort((a, b) =>
      a.signature === b.signature
        ? JSON.stringify(a.style).localeCompare(JSON.stringify(b.style))
        : a.signature.localeCompare(b.signature)
    ),
  }
}

/** ------------------------------------------------------------------ */

async function main() {
  if (!flag('no-build')) {
    console.log('› building harness…')
    await run('npx', ['vite', 'build', '--config', 'vite.harness.config.ts'], { cwd: FRONTEND })
  }

  console.log('› starting preview server…')
  const server = spawn('npx', ['vite', 'preview', '--config', 'vite.harness.config.ts'], {
    cwd: FRONTEND,
    stdio: 'ignore',
    detached: true,
  })

  const browser = await chromium.launch()
  try {
    await waitForServer(PREVIEW_URL)

    const page = await browser.newPage({ viewport: VIEWPORT })
    const consoleErrors = []
    page.on('console', (m) => {
      if (m.type() === 'error') consoleErrors.push(m.text())
    })

    await page.goto(`${PREVIEW_URL}/modal-harness.html`, { waitUntil: 'load' })
    // The login-gate lesson from the earlier round applies here too:
    // `isVisible({timeout})` is a no-op, so every readiness check in this
    // script is an explicit waitForFunction.
    await page.waitForFunction(() => window.__MODAL_HARNESS_READY__ === true, null, {
      timeout: 30_000,
    })

    const meta = await page.evaluate(() => ({
      catalog: window.__MODAL_HARNESS__.catalog,
      discoveredFiles: window.__MODAL_HARNESS__.discoveredFiles,
    }))

    const declaredFiles = [...new Set(meta.catalog.map((e) => e.file))].sort()
    const drift = {
      discoveredNotCatalogued: meta.discoveredFiles.filter((f) => !declaredFiles.includes(f)),
      cataloguedNotDiscovered: declaredFiles.filter((f) => !meta.discoveredFiles.includes(f)),
    }

    const targets = meta.catalog.filter((e) => (only ? only.includes(e.id) : true))
    const results = {}

    for (const entry of targets) {
      if (entry.status === 'gap') {
        results[entry.id] = { status: 'gap', file: entry.file, label: entry.label, reason: entry.reason }
        console.log(`  gap      ${entry.id}`)
        continue
      }

      consoleErrors.length = 0
      await page.goto(`${PREVIEW_URL}/modal-harness.html?dialog=${entry.id}`, { waitUntil: 'load' })
      let harnessStatus
      try {
        await page.waitForFunction(() => window.__MODAL_HARNESS_READY__ === true, null, {
          timeout: 30_000,
        })
        harnessStatus = await page.evaluate(() => ({
          status: window.__MODAL_HARNESS__.status,
          error: window.__MODAL_HARNESS__.error,
          unstubbedCalls: window.__MODAL_HARNESS__.unstubbedCalls,
        }))
      } catch (err) {
        results[entry.id] = {
          status: 'timeout',
          file: entry.file,
          label: entry.label,
          error: String(err.message ?? err),
        }
        console.log(`  TIMEOUT  ${entry.id}`)
        continue
      }

      await freezeAndSettle(page)
      const measured = await page.evaluate(COLLECT)
      results[entry.id] = {
        status: harnessStatus.status,
        file: entry.file,
        label: entry.label,
        via: entry.via,
        error: harnessStatus.error,
        unstubbedCalls: [...new Set(harnessStatus.unstubbedCalls)].sort(),
        consoleErrors: normaliseConsoleErrors(consoleErrors),
        ...measured,
      }
      const mark = harnessStatus.status === 'rendered' ? 'ok      ' : harnessStatus.status.padEnd(8)
      console.log(`  ${mark} ${entry.id}  (${measured.rows.length} signatures)`)
    }

    const payload = {
      capturedAt: new Date().toISOString(),
      viewport: VIEWPORT,
      theme: 'dark (app default)',
      // Absent on any capture predating bead `enhancedchannelmanager-iotbh`,
      // which is exactly how you tell whether a file's geometry can be trusted.
      animationsFrozen: true,
      counts: {
        filesMatchingDialogMarkers: meta.discoveredFiles.length,
        cataloguedDialogs: meta.catalog.length,
        rendered: Object.values(results).filter((r) => r.status === 'rendered').length,
        notRendered: Object.values(results).filter((r) => r.status === 'not-rendered').length,
        crashed: Object.values(results).filter((r) => r.status === 'crashed').length,
        timeout: Object.values(results).filter((r) => r.status === 'timeout').length,
        gaps: Object.values(results).filter((r) => r.status === 'gap').length,
      },
      drift,
      dialogs: results,
    }

    if (flag('diff')) {
      diff(payload)
      return
    }

    mkdirSync(dirname(outPath), { recursive: true })
    writeFileSync(outPath, `${JSON.stringify(payload, null, 2)}\n`)
    console.log(`\n› wrote ${outPath}`)
    console.log(`  ${JSON.stringify(payload.counts)}`)
    if (drift.discoveredNotCatalogued.length || drift.cataloguedNotDiscovered.length) {
      console.log(`  CATALOG DRIFT: ${JSON.stringify(drift)}`)
    }
  } finally {
    await browser.close()
    try {
      process.kill(-server.pid)
    } catch {
      /* already gone */
    }
  }
}

/**
 * Console output is captured because "this dialog rendered, but shouted" is
 * worth knowing — but it arrives with wall-clock timestamps and in
 * nondeterministic order, which would make an otherwise byte-stable baseline
 * churn on every run and bury a real typography change in noise.
 */
function normaliseConsoleErrors(lines) {
  const cleaned = lines.map((line) =>
    line
      .replace(/\[\d{4}-\d{2}-\d{2}T[\d:.]+Z\]\s*/g, '')
      .replace(/\d{4}-\d{2}-\d{2}T[\d:.]+Z/g, '<ts>')
      .trim()
  )
  return [...new Set(cleaned)].sort().slice(0, 8)
}

/**
 * Compare per SIGNATURE, as a multiset of style tuples.
 *
 * A single signature legitimately carries many different computed styles —
 * `span.material-icons` alone appears at half a dozen sizes in one dialog —
 * so "look up the one row for this signature" silently compares unrelated
 * rows and reports hundreds of phantom changes. (It did, on the first
 * self-check run: 239 of them against an identical tree.)
 */
function diff(after) {
  if (!existsSync(DEFAULT_BASELINE)) throw new Error(`no baseline at ${DEFAULT_BASELINE}`)
  const before = JSON.parse(readFileSync(DEFAULT_BASELINE, 'utf8'))
  let changes = 0

  for (const [id, a] of Object.entries(after.dialogs)) {
    const b = before.dialogs[id]
    if (!b) {
      console.log(`NEW DIALOG  ${id}`)
      changes += 1
      continue
    }
    if (a.status !== b.status) {
      console.log(`STATUS      ${id}: ${b.status} -> ${a.status}`)
      changes += 1
    }
    if (!a.rows || !b.rows) continue

    const tuple = (r) =>
      [
        r.style['font-size'],
        r.style['font-weight'],
        r.style['line-height'],
        r.style['letter-spacing'],
        r.style['text-transform'],
      ].join('/')

    const bucket = (rows) => {
      const m = new Map()
      for (const r of rows) {
        if (!m.has(r.signature)) m.set(r.signature, new Map())
        const inner = m.get(r.signature)
        inner.set(tuple(r), (inner.get(tuple(r)) ?? 0) + r.count)
      }
      return m
    }

    const beforeBuckets = bucket(b.rows)
    const afterBuckets = bucket(a.rows)

    for (const sig of new Set([...beforeBuckets.keys(), ...afterBuckets.keys()])) {
      const was = beforeBuckets.get(sig) ?? new Map()
      const now = afterBuckets.get(sig) ?? new Map()
      for (const t of new Set([...was.keys(), ...now.keys()])) {
        const wasCount = was.get(t) ?? 0
        const nowCount = now.get(t) ?? 0
        if (wasCount === nowCount) continue
        console.log(`TYPE        ${id} ${sig}  ${t}  x${wasCount} -> x${nowCount}`)
        changes += 1
      }
    }
  }
  console.log(`\n${changes} typography change(s) vs baseline.`)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
