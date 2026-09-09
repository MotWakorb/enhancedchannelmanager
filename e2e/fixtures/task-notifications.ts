import { test as base, type Page } from '@playwright/test'
import assert from 'node:assert/strict'
import { navigateToTab } from './base'

interface PrivateTask {
  page: Page
  task: { task_id: string; task_name: string; show_notifications: boolean }
}

/** The endpoint awaits execution and returns TaskResult, not an enqueue ID.
 * See backend/routers/tasks.py:run_task and ScheduledTasksSection:executeRunNow.
 */
export async function exerciseTaskNotifications({ page, task }: PrivateTask, enabled: boolean, completionTimeout = 15000) {
  const original = task.show_notifications
  try {
    await navigateToTab(page, 'settings')
    await page.locator('.settings-nav-item').filter({ hasText: 'Scheduled Tasks' }).click()
    const card = page.getByTestId(`task-card-${task.task_id}`)
    await card.getByRole('button', { name: 'Edit', exact: false }).click()
    const checkbox = page.locator('label').filter({ hasText: 'Show notifications in bell icon' }).locator('input[type="checkbox"]')
    await checkbox.waitFor({ state: 'visible' })
    assert.equal(await checkbox.isChecked(), original, 'editor must reflect the private backend setting')
    await checkbox.setChecked(enabled)
    await page.locator('.task-editor-modal').getByRole('button', { name: 'Save Changes', exact: true }).click()
    await checkbox.waitFor({ state: 'hidden' })
    assert.equal(task.show_notifications, enabled, 'save must reach the private backend')

    const runButton = card.getByRole('button', { name: 'Run Now', exact: false })
    const completion = page.waitForResponse(response =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/tasks/${task.task_id}/run`,
    { timeout: completionTimeout })
    // Both promises are observed, including when clicking fails or completion times out.
    const [response] = await Promise.all([completion, runButton.click()])
    assert.equal(response.status(), 200, 'run must return HTTP 200')
    const result = await response.json()
    assert.equal(result.success, true, 'run must reach terminal success')
    assert.ok(!result.error, `run error: ${result.error}`)
    assert.equal(result.failed_count, 0, 'partial/failed execution is not clean success')
    assert.ok(Number.isFinite(Date.parse(result.started_at)) && Number.isFinite(Date.parse(result.completed_at)) &&
      Date.parse(result.completed_at) >= Date.parse(result.started_at), 'run must have valid terminal timestamps')

    // executeRunNow awaits loadTasks() before removing the Cancel control. An
    // HTTP response alone does not prove the React consumer finished its work.
    await runButton.waitFor({ state: 'visible', timeout: 5000 })
    await card.getByRole('button', { name: 'Cancel', exact: false }).waitFor({ state: 'hidden', timeout: 5000 })
    // Transient run/save toasts can cover the bell; dismiss them through their
    // controls without changing persistent notification-center data.
    for (const dismiss of await page.locator('.toast-dismiss').all()) await dismiss.click()
    await page.locator('.toast').first().waitFor({ state: 'hidden', timeout: 5000 })
    const fetched = page.waitForResponse(response => response.request().method() === 'GET' &&
      new URL(response.url()).pathname === '/api/notifications', { timeout: 5000 })
    const [notifications] = await Promise.all([fetched, page.locator('.notification-bell').click()])
    assert.equal(notifications.status(), 200)
    await notifications.finished()
    await page.locator('.notification-list').waitFor({ state: 'visible' })
    // Response events precede React's fetch continuation/commit.
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    const matching = page.locator('.notification-item').filter({ hasText: task.task_name })
    if (enabled) {
      await matching.first().waitFor({ state: 'visible', timeout: 5000 })
      assert.ok(await matching.count() > 0)
    } else {
      assert.equal(await matching.count(), 0)
      // A bounded negative observation, not a guessed task-completion sleep.
      let appeared = false
      try {
        await matching.first().waitFor({ state: 'visible', timeout: 1000 })
        appeared = true
      } catch (error) {
        if (!(error instanceof Error) || error.name !== 'TimeoutError') throw error
      }
      assert.equal(appeared, false, 'forbidden late task notification')
    }
  } finally {
    // This object IS the per-test routed backend. No live API or cleanup task.
    task.show_notifications = original
  }
}

export const test = base.extend<{
  privateTask: PrivateTask
  initialNotifications: boolean
  execution: 'fast' | 'delayed' | 'never' | 'failed' | 'cancelled'
}>({
  initialNotifications: [true, { option: true }],
  execution: ['fast', { option: true }],
  privateTask: async ({ page, context, baseURL, initialNotifications, execution }, use, testInfo) => {
    assert.equal(testInfo.project.metadata.rdniaIsolated, true,
      'Task notification tests require playwright.verification.config.ts; live runs are forbidden')
    assert.ok(baseURL && new URL(baseURL).hostname === '127.0.0.1')
    const task = {
      task_id: 'rdnia_probe', task_name: 'Verification Probe', task_description: 'Private routed no-op',
      show_notifications: initialNotifications, enabled: true, effective_enabled: true,
      status: 'idle', progress: { total: 0, current: 0, status: 'idle', success_count: 0, failed_count: 0, skipped_count: 0 },
      schedule: { schedule_type: 'manual' }, schedules: [], last_run: null as string | null, next_run: null, config: {},
    }
    let notification: Record<string, unknown> | undefined
    let stopping = false
    let release: (() => void) | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    const blocked: string[] = []
    await context.routeWebSocket('**/*', socket => socket.close())
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      if (url.origin !== new URL(baseURL!).origin) {
        blocked.push(request.url())
        return route.abort('blockedbyclient')
      }
      if (!path.startsWith('/api/')) {
        if (request.method() === 'GET') return route.continue()
        blocked.push(`${request.method()} ${path}`)
        return route.abort('blockedbyclient')
      }
      if (path === `/api/tasks/${task.task_id}/run` && request.method() === 'POST') {
        const started_at = new Date().toISOString()
        if (execution === 'delayed' || execution === 'never') {
          await new Promise<void>(resolve => {
            release = resolve
            if (execution === 'delayed') timer = setTimeout(resolve, 6000)
          })
        }
        if (stopping) return route.abort()
        task.last_run = new Date().toISOString()
        if (task.show_notifications) notification = {
          id: 1, type: 'success', title: 'Task Completed', message: `${task.task_name} completed`,
          source: `task_${task.task_id}`, read: false, created_at: task.last_run, metadata: {},
        }
        return route.fulfill({ json: {
          success: execution !== 'failed' && execution !== 'cancelled',
          message: 'Private probe finished', error: execution === 'cancelled' ? 'CANCELLED' : undefined,
          started_at, completed_at: task.last_run, total_items: 1, success_count: 1, failed_count: 0, skipped_count: 0,
        } })
      }
      if (path === `/api/tasks/${task.task_id}` && request.method() === 'PATCH') {
        const body = request.postDataJSON()
        assert.equal(typeof body.show_notifications, 'boolean')
        task.show_notifications = body.show_notifications
        return route.fulfill({ json: task })
      }
      if (path === '/api/auth/refresh') return route.fulfill({ status: 401, json: { detail: 'No session in private fixture' } })
      if (request.method() !== 'GET' && path !== '/api/session-start') {
        blocked.push(`${request.method()} ${path}`)
        return route.abort('blockedbyclient')
      }
      const data: Record<string, unknown> = {
        '/api/auth/status': { require_auth: false, setup_complete: true, dispatcharr_enabled: false },
        '/api/auth/setup-required': { required: false },
        '/api/auth/me': null,
        '/api/settings': { configured: true, url: 'https://fixture.invalid', theme: 'dark' },
        '/api/tasks': { tasks: [task] },
        [`/api/tasks/${task.task_id}/parameter-schema`]: { parameters: [] },
        [`/api/tasks/${task.task_id}/schedules`]: { schedules: [] },
        '/api/tasks/history': { history: [], total: 0 },
        '/api/notifications': { notifications: notification ? [notification] : [], unread_count: notification ? 1 : 0 },
        '/api/profile-conflict-reviews': { reviews: [] },
        '/api/event-sync-reviews': { reviews: [], total: 0 },
      }
      return route.fulfill({ status: path === '/api/auth/me' ? 401 : 200, json: data[path] ?? [] })
    })
    try {
      await page.goto('/')
      await use({ page, task })
    } finally {
      stopping = true
      task.show_notifications = initialNotifications
      if (timer) clearTimeout(timer)
      release?.()
      // Stop the consumer before draining routes, including a never-completing POST.
      await page.close()
      await context.unrouteAll({ behavior: 'wait' })
      assert.equal(task.show_notifications, initialNotifications)
      assert.deepEqual(blocked, [], 'unexpected network traffic was blocked, never passed through')
    }
  },
})
test.use({ serviceWorkers: 'block' })
