import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { test, expect } from '@playwright/test'

const FRONTEND = path.resolve(process.cwd(), 'frontend')
const HARNESS_URL = 'http://127.0.0.1:4273/modal-harness.html'
const TARGETS = ['pending-merge-bulk', 'tag-create-group'] as const
const REFERENCE = 'csv-import'

let server: ChildProcess

async function waitForHarness(): Promise<void> {
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
}

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
  await waitForHarness()
})

test.afterAll(() => {
  if (!server?.pid) return
  try {
    process.kill(-server.pid)
  } catch {
    // The preview may already have exited after a failed test.
  }
})

type Chrome = {
  normal: string[]
  hover: string[]
  glyphSize: string
  overflow: boolean
}

async function measure(page: import('@playwright/test').Page, dialog: string): Promise<Chrome> {
  await page.goto(`${HARNESS_URL}?dialog=${dialog}`, { waitUntil: 'load' })
  await page.waitForFunction(() =>
    (window as Window & { __MODAL_HARNESS_READY__?: boolean }).__MODAL_HARNESS_READY__ === true
  )
  const close = page.locator('.modal-close-btn')
  await expect(close).toBeVisible()

  const style = () => close.evaluate((element) => {
    const computed = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return [
      `${Math.round(rect.width)}x${Math.round(rect.height)}`,
      computed.padding,
      computed.backgroundColor,
      computed.borderRadius,
      computed.color,
      computed.display,
      computed.alignItems,
      computed.justifyContent,
    ]
  })

  const normal = await style()
  await close.hover()
  await page.waitForTimeout(200)
  const hover = await style()
  return {
    normal,
    hover,
    glyphSize: await close.locator('.material-icons').evaluate((glyph) => getComputedStyle(glyph).fontSize),
    overflow: await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth),
  }
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
  test(`both former exceptions match canonical close chrome at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const canonical = await measure(page, REFERENCE)
    expect(canonical.normal[0]).toBe('32x32')
    expect(canonical.glyphSize).toBe('18px')

    for (const dialog of TARGETS) {
      const actual = await measure(page, dialog)
      expect(actual.normal, `${dialog} normal chrome`).toEqual(canonical.normal)
      expect(actual.hover, `${dialog} hover chrome`).toEqual(canonical.hover)
      expect(actual.glyphSize, `${dialog} glyph`).toBe('18px')
      expect(actual.overflow, `${dialog} horizontal overflow`).toBe(false)
    }
  })
}
