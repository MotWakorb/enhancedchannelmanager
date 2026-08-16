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

  expect(catalog).toHaveLength(84)
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

test('every rendered ModalOverlay catalog state has an accessible named semantic owner', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto(HARNESS_URL, { waitUntil: 'load' })
  await waitForHarness(page)
  const catalog = await page.evaluate(
    () => (window as Window & { __MODAL_HARNESS__: { catalog: CatalogEntry[] } }).__MODAL_HARNESS__.catalog,
  )
  const expectedNested = new Set([
    'norm-apply-confirm',
    'task-schedule-add',
    'task-schedule-edit',
    'streams-bulk-create-conflict',
  ])
  const reviewedNonOverlayCatalogIds = new Set([
    'group-multi-select-dropdown',
    'selection-action-bar',
    'stream-create-menu',
    'cp-circuit-breaker-banner',
    'cp-event-sync-rule-editor',
    'cp-rule-builder',
    'scheduled-tasks-run',
    'channels-pane-renumber-all',
  ])
  // These catalog states predate the owned-focus rollout and are explicitly
  // retained as focus debt. The exact comparison below makes both additions
  // and removals review-visible while every state still runs the same probe.
  const reviewedInitialFocusDebt = new Set([
    'dbas-restore',
    'dbas-restore-saved',
    'stream-dedup',
    'cp-bulk-rule-settings',
    'cp-event-sync-autosync-fix',
    'dummy-epg-delete-confirm',
    'dummy-epg-export',
    'dummy-epg-import-yaml',
    'norm-apply-confirm',
    'settings-plex-token',
    'cp-rule-builder-modal',
    'cp-delete-confirm',
    'cp-import-dialog',
    'cp-export-dialog',
    'cp-execution-details',
    'cp-event-sync-run-confirm',
    'cp-rollback-confirm',
    'cp-revert-confirm',
    'cp-revert-result',
  ])
  const reviewedTabRecaptureDebt = new Set([
    'dummy-epg-delete-confirm',
    'dummy-epg-export',
    'dummy-epg-import-yaml',
    'norm-apply-confirm',
    'settings-plex-token',
    'cp-rule-builder-modal',
    'cp-delete-confirm',
    'cp-import-dialog',
    'cp-export-dialog',
    'cp-execution-details',
    'cp-event-sync-run-confirm',
    'cp-rollback-confirm',
    'cp-revert-confirm',
    'cp-revert-result',
  ])
  const observedWithoutOverlay: string[] = []
  const initialFocusFailures: string[] = []
  const tabRecaptureFailures: string[] = []

  for (const entry of catalog) {
    if (entry.status === 'gap') continue
    await page.goto(`${HARNESS_URL}?dialog=${encodeURIComponent(entry.id)}`, { waitUntil: 'load' })
    await waitForHarness(page)
    const result = await page.evaluate(() => {
      const harness = (window as Window & { __MODAL_HARNESS__: { status: string; error: unknown } }).__MODAL_HARNESS__
      const overlays = [...document.querySelectorAll<HTMLElement>('[data-modal-overlay]')]
        .filter((overlay) => getComputedStyle(overlay).display !== 'none')
      const surfaceNodes = [...document.querySelectorAll<HTMLElement>('[role="dialog"], [role="alertdialog"]')]
        .filter((surface) => getComputedStyle(surface).display !== 'none')
      const surfaces = surfaceNodes.map((surface) => ({
          role: surface.getAttribute('role'),
          modal: surface.getAttribute('aria-modal'),
          name: surface.getAttribute('aria-label')?.trim() ||
            document.getElementById(surface.getAttribute('aria-labelledby') ?? '')?.textContent?.trim() || '',
        }))
      const ownedSurfaceCounts = overlays.map((overlay) =>
        surfaceNodes.filter((surface) => surface.closest('[data-modal-overlay]') === overlay).length)
      return { status: harness.status, hasError: harness.error !== null, overlays: overlays.length, surfaces, ownedSurfaceCounts }
    })
    expect(result.status, `${entry.id} harness status`).toBe('rendered')
    expect(result.hasError, `${entry.id} harness error`).toBe(false)
    if (result.overlays === 0) {
      observedWithoutOverlay.push(entry.id)
      continue
    }
    expect(reviewedNonOverlayCatalogIds.has(entry.id), `${entry.id} unexpected non-overlay classification`).toBe(false)
    expect(result.surfaces, `${entry.id} semantic surface count`).toHaveLength(expectedNested.has(entry.id) ? 2 : 1)
    expect(result.ownedSurfaceCounts, `${entry.id} one semantic owner per overlay`)
      .toEqual(new Array(result.overlays).fill(1))
    for (const surface of result.surfaces) {
      expect(surface.modal, `${entry.id} modal state`).toBe('true')
      expect(surface.name, `${entry.id} accessible name`).not.toBe('')
    }
    await expect(page.locator('[role="dialog"], [role="alertdialog"]').last()).toContainText(/\S/)
    if (!(await page.evaluate(() => {
      const surfaces = [...document.querySelectorAll<HTMLElement>('[role="dialog"], [role="alertdialog"]')]
      return surfaces.at(-1)?.contains(document.activeElement) ?? false
    }))) {
      initialFocusFailures.push(entry.id)
    }
    await page.evaluate(() => {
      const outside = document.querySelector<HTMLElement>('[data-harness-chrome]')
      outside?.setAttribute('tabindex', '-1')
      outside?.focus()
    })
    await page.keyboard.press('Tab')
    if (!(await page.evaluate(() => {
      const surfaces = [...document.querySelectorAll<HTMLElement>('[role="dialog"], [role="alertdialog"]')]
      return surfaces.at(-1)?.contains(document.activeElement) ?? false
    }))) tabRecaptureFailures.push(entry.id)
  }
  expect(observedWithoutOverlay.sort()).toEqual([...reviewedNonOverlayCatalogIds].sort())
  expect(initialFocusFailures.sort()).toEqual([...reviewedInitialFocusDebt].sort())
  expect(tabRecaptureFailures.sort()).toEqual([...reviewedTabRecaptureDebt].sort())
})
