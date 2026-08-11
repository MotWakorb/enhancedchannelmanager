import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'

type CatalogEntry = { id: string; status: 'rendered' | 'gap' }
const FRONTEND = path.resolve(process.cwd(), 'frontend')
const HARNESS_URL = 'http://127.0.0.1:4273/modal-harness.html'
let server: ChildProcess

test.beforeAll(async () => {
  execFileSync('npx', ['vite', 'build', '--config', 'vite.harness.config.ts'], { cwd: FRONTEND, stdio: 'inherit' })
  server = spawn('npx', ['vite', 'preview', '--config', 'vite.harness.config.ts'], {
    cwd: FRONTEND,
    stdio: 'ignore',
    detached: true,
  })
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try { if ((await fetch(HARNESS_URL)).ok) return } catch { /* starting */ }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('modal harness preview did not start')
})

test.afterAll(() => {
  if (!server?.pid) return
  try { process.kill(-server.pid) } catch { /* already stopped */ }
})

async function openDialog(page: Page, id: string, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height })
  await page.goto(`${HARNESS_URL}?dialog=${encodeURIComponent(id)}`, { waitUntil: 'load' })
  await page.waitForFunction(() => (window as Window & { __MODAL_HARNESS_READY__?: boolean }).__MODAL_HARNESS_READY__)
  await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' })
  const status = await page.evaluate(() => {
    const harness = (window as Window & { __MODAL_HARNESS__: { status: string; error: unknown } }).__MODAL_HARNESS__
    return { status: harness.status, error: harness.error !== null }
  })
  expect(status, `${id} must genuinely render`).toEqual({ status: 'rendered', error: false })
}

test('all catalogued dialogs render and fit the 200%-zoom CSS viewport', async ({ page }) => {
  test.setTimeout(180_000)
  await page.setViewportSize({ width: 640, height: 360 })
  await page.goto(HARNESS_URL)
  await page.waitForFunction(() => (window as Window & { __MODAL_HARNESS_READY__?: boolean }).__MODAL_HARNESS_READY__)
  const catalog = await page.evaluate(() => (window as Window & { __MODAL_HARNESS__: { catalog: CatalogEntry[] } }).__MODAL_HARNESS__.catalog)
  for (const entry of catalog) {
    if (entry.status === 'gap') continue
    await openDialog(page, entry.id, 640, 360)
    const geometry = await page.evaluate(() => {
      const modal = document.querySelector<HTMLElement>('.modal-container')
      const rect = modal?.getBoundingClientRect()
      const body = modal?.querySelector<HTMLElement>('.modal-body')
      const header = modal?.querySelector<HTMLElement>('.modal-header')?.getBoundingClientRect()
      const footer = modal?.querySelector<HTMLElement>('.modal-footer')?.getBoundingClientRect()
      const bodyStyle = body && getComputedStyle(body)
      return {
        documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        // Non-modal harness entries (portal listboxes and action bars) still
        // participate in the render/overflow assertion, but have no chassis.
        contained: !rect || (rect.left >= -0.5 && rect.right <= innerWidth + 0.5 && rect.top >= -0.5 && rect.bottom <= innerHeight + 0.5),
        chromePinned: !rect || ((!header || header.top >= rect.top - 0.5) && (!footer || footer.bottom <= rect.bottom + 0.5)),
        chromeEdges: rect ? { modalTop: rect.top, modalBottom: rect.bottom, headerTop: header?.top, footerBottom: footer?.bottom } : null,
        scrollable: !body || body.scrollHeight <= body.clientHeight || bodyStyle?.overflowY === 'auto' || bodyStyle?.overflowY === 'scroll',
      }
    })
    expect(geometry.documentOverflow, `${entry.id} horizontal overflow`).toBe(false)
    expect(geometry.contained, `${entry.id} viewport containment`).toBe(true)
    expect(geometry.chromePinned, `${entry.id} header/footer containment ${JSON.stringify(geometry.chromeEdges)}`).toBe(true)
    expect(geometry.scrollable, `${entry.id} overflowing body scroll`).toBe(true)
  }
})

test('the 700 zoom tier owns exact chrome and common reflow boundaries', async ({ page }) => {
  for (const width of [1280, 701, 700, 640]) {
    await openDialog(page, 'dummy-epg-profile', width, width === 640 ? 360 : 720)
    const chrome = await page.evaluate(() => {
      const read = (selector: string) => {
        const node = document.querySelector<HTMLElement>(selector)!
        const style = getComputedStyle(node)
        return { padding: style.padding, columns: style.gridTemplateColumns, overflowY: style.overflowY }
      }
      return { header: read('.modal-header'), body: read('.modal-body'), footer: read('.modal-footer'), row: read('.modal-form-row') }
    })
    if (width > 700) {
      expect(chrome.header.padding).toBe('8px 20px')
      expect(chrome.body.padding).toBe('20px')
      expect(chrome.footer.padding).toBe('16px 20px')
      expect(chrome.row.columns.split(' ')).toHaveLength(2)
    } else {
      expect(chrome.header.padding).toBe('8px 16px')
      expect(chrome.body.padding).toBe('16px')
      expect(chrome.footer.padding).toBe('14px 16px')
      expect(chrome.row.columns.split(' ')).toHaveLength(1)
    }
  }
})

test('complex, zoom and compact component behavior changes only at its semantic tier', async ({ page }) => {
  await openDialog(page, 'cp-rule-builder', 821, 720)
  expect(await page.locator('.modal-twopane').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(2)
  await openDialog(page, 'cp-rule-builder', 820, 720)
  expect(await page.locator('.modal-twopane').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(1)

  await openDialog(page, 'cp-rule-builder', 701, 720)
  expect(await page.locator('.rule-builder-footer').evaluate((node) => getComputedStyle(node).flexDirection)).toBe('row')
  expect(await page.locator('.rule-active-window-fields').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(2)
  await openDialog(page, 'cp-rule-builder', 700, 720)
  expect(await page.locator('.rule-builder-footer').evaluate((node) => getComputedStyle(node).flexDirection)).toBe('column-reverse')
  expect(await page.locator('.rule-active-window-fields').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(1)

  await openDialog(page, 'dummy-epg-channel-picker', 701, 720)
  expect(await page.locator('.channel-picker-body').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(2)
  await openDialog(page, 'dummy-epg-channel-picker', 700, 720)
  expect(await page.locator('.channel-picker-body').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(1)

  await openDialog(page, 'm3u-filters', 700, 720)
  const responsiveValues = page.locator('.filter-responsive-label:visible')
  expect(await responsiveValues.allTextContents()).toEqual(expect.arrayContaining(['Action', 'Order']))
  await expect(page.locator('.filter-action').first()).toBeVisible()
  await expect(page.locator('.filter-order').first()).toBeVisible()
  await expect(page.getByRole('cell', { name: /Action (Include|Exclude)/ }).first()).toBeVisible()
  await expect(page.getByRole('cell', { name: /Order \d+/ }).first()).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Action', exact: true })).toBeHidden()

  await openDialog(page, 'm3u-filters', 701, 720)
  await expect(page.locator('.filter-responsive-label').first()).toBeHidden()
  await expect(page.getByRole('columnheader', { name: 'Action', exact: true })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Order' })).toBeVisible()

  await openDialog(page, 'bulk-lcn-fetch', 501, 720)
  const wideLimit = await page.locator('.lcn-item-content .channel-name').first().evaluate((node) => getComputedStyle(node).maxWidth)
  await openDialog(page, 'bulk-lcn-fetch', 500, 720)
  const compactLimit = await page.locator('.lcn-item-content .channel-name').first().evaluate((node) => getComputedStyle(node).maxWidth)
  expect(wideLimit).toBe('200px')
  expect(compactLimit).toBe('150px')

  await openDialog(page, 'logo-editor', 501, 720)
  const wide = await page.locator('.logo-modal').evaluate((node) => getComputedStyle(node).maxWidth)
  await openDialog(page, 'logo-editor', 500, 720)
  const compact = await page.locator('.logo-modal').evaluate((node) => getComputedStyle(node).maxWidth)
  expect(wide).not.toBe('95%')
  expect(compact).toBe('95%')

  for (const [dialog, selector] of [
    ['vlc-protocol-helper', '.vlc-os-tabs'],
    ['preview-stream', '.preview-stream-info-header'],
    ['m3u-profile', '.profile-card'],
  ] as const) {
    await openDialog(page, dialog, 701, 720)
    const wideDirection = await page.locator(selector).first().evaluate((node) => getComputedStyle(node).flexDirection)
    await openDialog(page, dialog, 700, 720)
    const zoomDirection = await page.locator(selector).first().evaluate((node) => getComputedStyle(node).flexDirection)
    expect(wideDirection, `${dialog} before zoom tier`).toBe('row')
    expect(zoomDirection, `${dialog} at zoom tier`).toBe('column')
  }
})
