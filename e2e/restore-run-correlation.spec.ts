import { test, expect } from '@playwright/test'
import assert from 'node:assert/strict'

// Full built app + actual modals/hook/API client; only HTTP data is synthetic.
for (const flow of ['uploaded', 'saved']) {
  test(`${flow}: remount ignores old progress and history before enabling Apply`, async ({ page, context, baseURL }, info) => {
    assert.equal(info.project.metadata.rdniaIsolated, true, 'requires private verification config')
    assert.ok(baseURL && new URL(baseURL).hostname === '127.0.0.1')
    const filename = 'ecm-backup-2026-01-01_000000.zip'
    const state = { requested: 0, progress: 0, history: 0, progressReads: 0, historyReads: 0 }
    const blocked: string[] = []
    const traffic: object[] = []
    page.on('pageerror', error => console.error('browser error:', error.message))
    await context.routeWebSocket('**/*', socket => socket.close())
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      if (url.origin !== new URL(baseURL!).origin) {
        blocked.push(request.url())
        return route.abort('blockedbyclient')
      }
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/backup/restore-dbas' || path === '/api/backup/restore-dbas-saved') {
        assert.equal(request.method(), 'POST')
        state.requested++
        traffic.push({ trigger: path, run_id: `run-${state.requested}` })
        return route.fulfill({ json: { status: 'started', task_id: 'dbas_restore',
          run_id: `run-${state.requested}`, is_dry_run: true, channel_reattach_mode: 'preserve' } })
      }
      if (path === '/api/tasks/dbas_restore') {
        state.progressReads++
        traffic.push({ progress: state.progress })
        return route.fulfill({ json: { task_id: 'dbas_restore', progress: {
          run_id: `run-${state.progress}`, started_at: '2026-01-01T00:00:00Z',
          status: 'completed', total: 13, current: 13, percentage: 100, current_item: 'finalize',
          success_count: 1, failed_count: 0, skipped_count: 0,
        } } })
      }
      if (path === '/api/tasks/dbas_restore/history') {
        state.historyReads++
        traffic.push({ history: state.history })
        return route.fulfill({ json: { history: [{ status: 'completed', details: {
          run_id: `run-${state.history}`, restore_report: {
            contract_version: 1, is_dry_run: true, categories: [], logo_misses: 0, notes: [],
            epg_link_reattach: { mode: 'overwrite', created_channels: 0, existing_channels: 1,
              preserved_channels: 0, existing_channels_named: [`REPORT-${state.history}`], preserved_channels_named: [] },
          },
        } }] } })
      }
      if (path === '/api/auth/refresh') return route.fulfill({ status: 401, json: {} })
      if (request.method() !== 'GET' && path !== '/api/session-start') {
        blocked.push(`${request.method()} ${path}`)
        return route.abort('blockedbyclient')
      }
      const data: Record<string, unknown> = {
        '/api/auth/status': { require_auth: false, setup_complete: true, dispatcharr_enabled: false },
        '/api/auth/setup-required': { required: false },
        '/api/auth/me': null,
        '/api/settings': { configured: true, url: '', theme: 'dark', ssrf_outbound_mode: 'lan', ssrf_outbound_mode_acknowledged: true },
        '/api/backup/saved': [{ filename, size_bytes: 12, type: 'zip', created_at: '2026-01-01T00:00:00Z' }],
        '/api/backup/export-sections': [],
        '/api/tasks': { tasks: [] },
        '/api/notifications': { notifications: [], unread_count: 0 },
        '/api/profile-conflict-reviews': { reviews: [] },
        '/api/event-sync-reviews': { reviews: [], total: 0 },
      }
      return route.fulfill({ status: path === '/api/auth/me' ? 401 : 200, json: data[path] ?? [] })
    })
    try {
      await page.goto('/')
      await page.locator('[data-tab="settings"]').click()
      await page.locator('.settings-nav-item').filter({ hasText: 'Backup & Restore' }).click()
      async function open() {
        if (flow === 'saved') await page.getByRole('button', { name: 'Restore as DBAS backup', exact: true }).click()
        else {
          await page.getByRole('button', { name: 'Restore from artifact...', exact: false }).click()
          const chooser = page.waitForEvent('filechooser')
          await page.getByText(/drag & drop a backup artifact/i).click()
          await (await chooser).setFiles({ name: filename, mimeType: 'application/zip', buffer: Buffer.from('PK synthetic') })
        }
        await page.getByRole('button', { name: 'Run preview', exact: true }).click()
      }
      // Establish a genuine prior terminal report, close, and remount the modal.
      state.progress = state.history = 1
      await open()
      await expect(page.getByTestId('existing-channel-reattach-notice')).toContainText('REPORT-1')
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      const oldHistoryReads = state.historyReads
      const oldProgressReads = state.progressReads
      await open()
      await expect.poll(() => state.progressReads).toBeGreaterThan(oldProgressReads + 1)
      expect(state.historyReads).toBe(oldHistoryReads)
      await expect(page.getByRole('button', { name: /apply these changes/i })).toHaveCount(0)
      await page.screenshot({ path: info.outputPath(`${flow}-old-progress-rejected.png`) })
      state.progress = 2
      await expect.poll(() => state.historyReads).toBeGreaterThan(oldHistoryReads)
      await expect(page.getByRole('button', { name: /apply these changes/i })).toHaveCount(0)
      state.history = 2
      await expect(page.getByTestId('existing-channel-reattach-notice')).toContainText('REPORT-2')
      await expect(page.getByTestId('existing-channel-reattach-notice')).not.toContainText('REPORT-1')
      const apply = page.getByRole('button', { name: /apply these changes/i })
      await expect(apply).toBeEnabled()
      await expect(apply).toBeInViewport()
      await apply.click({ trial: true })
      await page.screenshot({ path: info.outputPath(`${flow}-current-report.png`) })
      await info.attach('trigger-progress-history.json', { body: JSON.stringify(traffic, null, 2), contentType: 'application/json' })
    } finally {
      console.log('browser evidence:', info.outputDir)
      await info.attach('final-dom.txt', { body: await page.locator('body').innerText(), contentType: 'text/plain' })
      await page.screenshot({ path: info.outputPath('final.png') })
      await page.close()
      await context.unrouteAll({ behavior: 'wait' })
      expect(blocked).toEqual([])
    }
  })
}
