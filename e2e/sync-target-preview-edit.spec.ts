import { expect } from '@playwright/test'
import { test } from './fixtures/task-notifications'
import { navigateToTab } from './fixtures/base'

// Built app and real API client; the private fixture intercepts all backend
// traffic. No sync is sent to a live Dispatcharr instance.
test('editing a previewed sync target requires a fresh preview before Apply', async ({ privateTask }, info) => {
  const { page } = privateTask
  let target = {
    id: 7, name: 'Private replica', base_url: 'https://replica.example.com',
    credentials: { username: '***user' }, enabled: true, insecure: false,
    fuzzy_stream_matching: false, sync_logos: false, logo_sync_interval_hours: 24,
    core_settings_excluded: [], has_schedules_direct_password: false, credential_version: 1,
  }
  const runs: Array<{ sync_target_id: number; confirm_apply: boolean }> = []
  let saves = 0
  await page.route('**/api/sync-targets', route => route.fulfill({ json: [target] }))
  await page.route('**/api/sync-targets/source-credential-needs', route => route.fulfill({
    json: { needs_schedules_direct_password: false, schedules_direct_sources: [] },
  }))
  await page.route('**/api/sync-targets/7', route => {
    expect(route.request().method()).toBe('PUT')
    target = { ...target, ...route.request().postDataJSON() }
    saves++
    return route.fulfill({ json: target })
  })
  await page.route('**/api/tasks/dbas_sync_7/run', route => {
    expect(route.request().method()).toBe('POST')
    runs.push(route.request().postDataJSON().parameters)
    return route.fulfill({ json: { success: true, message: 'Private sync completed' } })
  })

  await navigateToTab(page, 'settings')
  await page.locator('.settings-nav-item').filter({ hasText: 'Backup & Restore' }).click()
  const preview = page.getByTestId('sync-target-preview-7')
  const apply = page.getByTestId('sync-target-apply-7')
  await expect(apply).toHaveCount(0)
  await preview.click()
  await expect(apply).toBeEnabled()
  await page.getByTestId('sync-target-edit-7').click()
  await page.getByLabel('Base URL', { exact: true }).fill('https://edited-replica.example.com')
  await page.getByRole('button', { name: /save changes/i }).click()
  await expect(page.locator('.stc-add-form')).toHaveCount(0)
  await expect(page.getByTestId('sync-target-row-7')).toContainText('https://edited-replica.example.com')
  await expect(preview).toBeEnabled()
  await expect(apply).toHaveCount(0)
  expect(saves).toBe(1)
  expect(runs).toEqual([{ sync_target_id: 7, confirm_apply: false }])
  await page.locator('.sync-targets-card').screenshot({ path: info.outputPath('edited-target-requires-preview.png') })

  await preview.click()
  await expect(apply).toBeEnabled()
  page.once('dialog', dialog => dialog.accept())
  await apply.click()
  await expect(apply).toHaveCount(0)
  expect(runs).toEqual([
    { sync_target_id: 7, confirm_apply: false },
    { sync_target_id: 7, confirm_apply: false },
    { sync_target_id: 7, confirm_apply: true },
  ])
})
