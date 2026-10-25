import { expect } from '@playwright/test'
import { test } from './fixtures/task-notifications'
import { navigateToTab } from './fixtures/base'
import results from './fixtures/p3-sync-task-results.json'

// Golden TaskResult messages produced by the real Python mapper, replayed through
// the built app's real API client and notification consumer. No live sync writes.
test('normal sync preview shows unpredicted streams without opening raw JSON', async ({ privateTask }, info) => {
  const { page } = privateTask
  const runs: unknown[] = []
  await page.route('**/api/sync-targets', route => route.fulfill({ json: [{
    id: 7, name: 'Private replica', base_url: 'https://replica.example.com',
    credentials: { username: '***user' }, enabled: true, insecure: false,
    fuzzy_stream_matching: false, sync_logos: false, logo_sync_interval_hours: 24,
    core_settings_excluded: [], has_schedules_direct_password: false, credential_version: 1,
  }] }))
  await page.route('**/api/sync-targets/source-credential-needs', route => route.fulfill({
    json: { needs_schedules_direct_password: false, schedules_direct_sources: [] },
  }))
  await page.route('**/api/tasks/dbas_sync_7/run', route => {
    const parameters = route.request().postDataJSON().parameters
    runs.push(parameters)
    return route.fulfill({ json: parameters.confirm_apply ? results.abortedApply : results.preview })
  })
  await navigateToTab(page, 'settings')
  await page.locator('.settings-nav-item').filter({ hasText: 'Backup & Restore' }).click()
  await page.getByTestId('sync-target-preview-7').click()
  const previewToast = page.locator('.toast').filter({ hasText: 'Sync Preview (dry run)' })
  await expect(previewToast).toContainText('stream: not predicted')
  await expect(previewToast).toContainText('Streams cannot be predicted until destination refresh.')
  await expect(previewToast).toContainText('counts cover predicted categories only')
  await expect(previewToast).toBeVisible()
  await previewToast.screenshot({ path: info.outputPath('preview-unpredicted-streams.png') })
  for (const dismiss of await page.locator('.toast-dismiss').all()) await dismiss.click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByTestId('sync-target-apply-7').click()
  const failedApply = page.locator('.toast').filter({ hasText: 'Cross-Instance Sync' })
  await expect(failedApply).toContainText('Cross-instance sync run could not read the destination')
  await expect(failedApply).not.toContainText('dry_run')
  await failedApply.screenshot({ path: info.outputPath('aborted-apply-not-preview.png') })
  expect(runs).toEqual([
    { sync_target_id: 7, confirm_apply: false },
    { sync_target_id: 7, confirm_apply: true },
  ])
})
