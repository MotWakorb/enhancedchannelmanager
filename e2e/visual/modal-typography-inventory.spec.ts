import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { expect, test } from '@playwright/test'

type CatalogEntry = {
  id: string
  status: 'rendered' | 'gap'
}

const GOVERNED_TAGS = new Set(['SPAN', 'STRONG', 'P', 'SUMMARY', 'LABEL', 'LI', 'DT', 'LEGEND'])
const FRONTEND = path.resolve(process.cwd(), 'frontend')
const HARNESS_URL = 'http://127.0.0.1:4273/modal-harness.html'
let server: ChildProcess

test.beforeAll(async () => {
  execFileSync('npx', ['vite', 'build', '--config', 'vite.harness.config.ts'], {
    cwd: FRONTEND,
    stdio: 'inherit',
  })
  server = spawn('npx', ['vite', 'preview', '--config', 'vite.harness.config.ts'], {
    cwd: FRONTEND,
    stdio: 'ignore',
    detached: true,
  })
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      if ((await fetch(HARNESS_URL)).ok) return
    } catch {
      // Preview is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('modal harness preview did not start')
})

test.afterAll(() => {
  if (!server?.pid) return
  try {
    process.kill(-server.pid)
  } catch {
    // Preview may already have exited after a failed test.
  }
})

async function waitForHarness(page: import('@playwright/test').Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Window & { __MODAL_HARNESS_READY__?: boolean }).__MODAL_HARNESS_READY__ === true,
  )
}

async function freezeAnimations(page: import('@playwright/test').Page): Promise<void> {
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        transition: none !important;
        animation: none !important;
      }
    `,
  })
  await page.evaluate(async () => {
    for (const animation of document.getAnimations()) animation.cancel()
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    )
  })
}

test('all catalogued modal text has a reviewed type-scale owner at 1280x720', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto(HARNESS_URL, { waitUntil: 'load' })
  await waitForHarness(page)
  const catalog = await page.evaluate(
    () =>
      (window as Window & {
        __MODAL_HARNESS__: { catalog: CatalogEntry[] }
      }).__MODAL_HARNESS__.catalog,
  )

  expect(catalog).toHaveLength(82)
  expect(catalog.filter((entry) => entry.status === 'gap').map((entry) => entry.id)).toEqual([
    'modal-overlay-base',
  ])
  const residuals: string[] = []
  for (const entry of catalog) {
    if (entry.status === 'gap') continue
    await page.goto(`${HARNESS_URL}?dialog=${encodeURIComponent(entry.id)}`, {
      waitUntil: 'load',
    })
    await waitForHarness(page)
    const runtime = await page.evaluate(() => {
      const harness = (window as Window & {
        __MODAL_HARNESS__: { status: string; error: unknown }
      }).__MODAL_HARNESS__
      const visibleProductionNodes = [...document.body.querySelectorAll('*')].filter((element) => {
        if (element.closest('[data-harness-chrome]')) return false
        const style = getComputedStyle(element)
        return style.display !== 'none' && style.visibility !== 'hidden'
      }).length
      return { status: harness.status, hasError: harness.error !== null, visibleProductionNodes }
    })
    expect(runtime.status, `${entry.id} harness status`).toBe('rendered')
    expect(runtime.hasError, `${entry.id} harness error`).toBe(false)
    expect(runtime.visibleProductionNodes, `${entry.id} rendered DOM`).toBeGreaterThan(0)
    await freezeAnimations(page)
    const rows = await page.evaluate((governedTags) => {
      const counts = new Map<string, number>()
      for (const element of document.body.querySelectorAll('*')) {
        if (!governedTags.includes(element.tagName)) continue
        // The governed population is the bead's bare-tag inventory. Named
        // icon, visually-hidden and component-specific roles are independently
        // owned and were never part of the 101-row decision ledger.
        if (element.classList.length !== 0) continue
        if (element.closest('[data-harness-chrome]')) continue
        if (
          !Array.from(element.childNodes).some(
            (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim(),
          )
        ) continue
        const style = getComputedStyle(element)
        if (style.display === 'none' || style.visibility === 'hidden' || style.fontSize !== '16px') continue
        const signature = `${element.tagName.toLowerCase()}${Array.from(element.classList)
          .filter((name) => !/^_|^css-/.test(name))
          .sort()
          .map((name) => `.${name}`)
          .join('')}`
        counts.set(signature, (counts.get(signature) ?? 0) + 1)
      }
      return [...counts].sort(([left], [right]) => left.localeCompare(right))
    }, [...GOVERNED_TAGS])
    for (const [signature, count] of rows) residuals.push(`${entry.id}|${signature}|${count}`)
  }

  // Diagnostics contain only catalog id, selector signature and count; never
  // rendered text. A new production bare <p> therefore fails this real-browser
  // gate without leaking modal content into public CI logs.
  expect(residuals).toEqual([])
})
