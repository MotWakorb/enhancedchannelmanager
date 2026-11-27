import { expect } from '@playwright/test'
import { test } from './fixtures/task-notifications'
import { navigateToTab } from './fixtures/base'

test('saved exclusions cross browser → production API → SQLite, and external deltas return to the UI', async ({ privateTask }, testInfo) => {
  const { page } = privateTask
  await page.route('**/api/providers', route => route.fulfill({ json: [
    { id: 1, name: 'Provider One', is_active: true },
    { id: 2, name: 'Provider Two', is_active: true },
  ] }))
  // Only this domain reaches the private server. App shell / upstream APIs
  // remain intercepted by privateTask; no live user instance is contacted.
  await page.route('**/api/m3u/changes**', async route => {
    const url = new URL(route.request().url())
    const response = await route.fetch({ url: `http://127.0.0.1:42872${url.pathname}${url.search}` })
    await route.fulfill({ response })
  })
  await navigateToTab(page, 'm3u-changes')
  const rows = page.locator('.change-row')
  await expect(rows).toHaveCount(3)
  await page.getByRole('button', { name: 'Actions for Sports 🏈 (Provider One)', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Exclude this group' }).click()
  await expect(rows).toHaveCount(2)
  await expect(rows.filter({ hasText: 'Sports 🏈' })).toContainText('Provider Two')
  await expect(page.getByRole('button', { name: 'Excluded groups (1)' })).toBeVisible()

  await page.reload()
  await navigateToTab(page, 'm3u-changes')
  await expect(rows).toHaveCount(2)
  const excluded = page.getByRole('button', { name: 'Excluded groups (1)' })
  await expect(excluded).toBeEnabled()
  await excluded.click()
  await expect(page.getByRole('checkbox', { name: 'Sports 🏈 — Provider One' })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: 'Sports 🏈 — Provider Two' })).not.toBeChecked()
  await page.screenshot({ path: testInfo.outputPath('excluded-groups.png'), fullPage: true })
  await page.getByRole('textbox', { name: 'Search Excluded groups (1)' }).fill('Sports')
  await page.getByRole('button', { name: 'Select All Visible' }).click()
  await expect(page.getByRole('button', { name: 'Excluded groups (2)' })).toBeEnabled()
  await page.keyboard.press('Escape')
  await expect(rows).toHaveCount(1)
  await expect(rows).toContainText('News')

  // Same PATCH delta used by MCP: preserve Provider One's concurrent preference.
  const mutation = await page.request.patch('http://127.0.0.1:42872/api/m3u/changes/exclusions', {
    data: { remove: [{ m3u_account_id: 2, group_name: 'Sports 🏈' }] },
  })
  expect(mutation.ok()).toBe(true)
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Excluded groups (1)' })).toBeEnabled()
  await expect(rows).toHaveCount(2)
  const raw = await page.request.get('http://127.0.0.1:42872/api/m3u/changes?include_excluded=true')
  expect((await raw.json()).total).toBe(3)

  await page.getByRole('button', { name: 'Excluded groups (1)' }).click()
  await page.getByRole('button', { name: 'Select All', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Excluded groups (3)' })).toBeEnabled()
  await page.keyboard.press('Escape')
  await expect(page.getByText('No changes match the current filters. Recorded history is retained.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear exclusions' }).click()
  await expect(rows).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Excluded groups (0)' })).toBeEnabled()
})
