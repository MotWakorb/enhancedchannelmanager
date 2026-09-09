import { expect, type Page } from '@playwright/test'
import { test, exerciseTaskNotifications } from './fixtures/task-notifications'
import { navigateToTab, isLoginPage, performLogin } from './fixtures/base'

// Real Chromium locators/DOM/reloads with short *failure* deadlines. Production
// helper timeout values are unchanged; these are not browser speed benchmarks.
function boundedPage(page: Page): Page {
  return new Proxy(page, {
    get(target, property) {
      if (property === 'waitForSelector') return (selector: string, options: object) => target.waitForSelector(selector, { ...options, timeout: 250 })
      if (property === 'waitForFunction') return (fn: Parameters<Page['waitForFunction']>[0], arg: unknown) => target.waitForFunction(fn, arg, { timeout: 250 })
      const value = Reflect.get(target, property)
      return typeof value === 'function' ? value.bind(target) : value
    },
  })
}

for (const mode of ['healthy', 'missing', 'recovery', 'repeated', 'loading']) {
  test(`synthetic browser navigation: ${mode}`, async ({ page }) => {
    let loads = 0
    await page.route('http://rdnia.invalid/', route => {
      loads++
      const content = mode === 'healthy' || (mode === 'recovery' && loads > 1)
      const blank = (mode === 'recovery' || mode === 'repeated') && loads === 1
      return route.fulfill({ contentType: 'text/html', body: `
        <header class="header">Private navigation fixture</header>
        <nav class="primary-navigation tab-navigation"><button data-tab="settings">Settings</button></nav>
        <main></main>
        <script>document.querySelector('button').onclick = function () {
          this.classList.add('active');
          document.querySelector('main').innerHTML = ${JSON.stringify(content ? '<div class="settings-tab">Ready</div>' : mode === 'loading' ? '<div class="settings-tab">Loading root</div><div class="tab-loading">Loading</div>' : '')};
          if (${blank}) document.querySelector('nav').remove();
        }</script>` })
    })
    await page.goto('http://rdnia.invalid/')
    const outcome = navigateToTab(boundedPage(page), 'settings')
    if (mode === 'healthy' || mode === 'recovery') {
      await outcome
      await expect(page.locator('.settings-tab')).toHaveText('Ready')
    } else {
      await expect(outcome).rejects.toThrow(/Navigation to settings.*failed/)
    }
    expect(loads).toBe(mode === 'recovery' || mode === 'repeated' ? 2 : 1)
  })
}

test('real app navigation enters Settings, leaves its alternate rail, and returns to Dashboard', async ({ privateTask }) => {
  const { page } = privateTask
  await navigateToTab(page, 'settings')
  await expect(page.locator('.settings-navigation')).toBeVisible()
  await navigateToTab(page, 'dashboard')
  await expect(page.locator('.operator-dashboard')).toBeVisible()
})

for (const result of ['success', 'refused']) {
  test(`synthetic login ${result} uses the real form and preserves refusal`, async ({ page }) => {
    await page.setContent(`<form><input name="username"><input name="password" type="password"><button type="submit">Login</button></form>
      <script>document.querySelector('form').onsubmit = event => {
        event.preventDefault();
        document.body.innerHTML = ${JSON.stringify(result === 'success' ? '<header class="header">Signed in</header>' : '<div class="login-error">Synthetic refusal</div>')};
      }</script>`)
    expect(await isLoginPage(page)).toBe(true)
    const login = performLogin(page, 'synthetic-user', 'synthetic-password')
    if (result === 'success') {
      await login
      expect(await isLoginPage(page)).toBe(false)
    } else {
      await expect(login).rejects.toThrow(/Login failed: Synthetic refusal/)
    }
  })
}

for (const initial of [true, false]) {
  test.describe(`original notifications ${initial}`, () => {
    test.use({ initialNotifications: initial })
    for (const execution of ['delayed', 'never', 'failed', 'cancelled'] as const) {
      test.describe(execution, () => {
        test.use({ execution })
        test('rendered completion must succeed before observations; restore original boolean', async ({ privateTask }) => {
          const run = exerciseTaskNotifications(privateTask, !initial, execution === 'never' ? 500 : 15000)
          if (execution === 'delayed') await run
          else await expect(run).rejects.toThrow(execution === 'never' ? /Timeout.*exceeded/ : /terminal success/)
          expect(privateTask.task.show_notifications).toBe(initial)
          if (execution !== 'delayed') await expect(privateTask.page.locator('.notification-list')).toHaveCount(0)
        })
      })
    }
  })
}

test('run response is not UI-consumer readiness while the post-run task refresh is pending', async ({ privateTask }) => {
  const { page } = privateTask
  let release!: () => void
  let refreshing!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  const refresh = new Promise<void>(resolve => { refreshing = resolve })
  let ran = false
  page.on('response', response => { if (response.url().endsWith('/api/tasks/rdnia_probe/run')) ran = true })
  await page.route('**/api/tasks', async route => {
    if (ran) { refreshing(); await held }
    await route.fallback()
  })
  const run = exerciseTaskNotifications(privateTask, true)
  // Attach rejection handling immediately, even if an earlier UI action fails.
  const result = run.then(() => undefined, error => error)
  try {
    await Promise.race([refresh, result.then(error => { throw error ?? Error('flow completed before held refresh') })])
    await expect(page.getByTestId('task-card-rdnia_probe').getByRole('button', { name: /Cancel/ })).toBeVisible()
    await expect(page.locator('.notification-list')).toHaveCount(0)
    await expect(page.waitForRequest(request => new URL(request.url()).pathname === '/api/notifications',
      { timeout: 2000 })).rejects.toThrow(/Timeout/)
  } finally {
    release()
    const error = await result
    if (error) throw error
  }
})

test('disabled observation rejects a forbidden notification rendered late', async ({ privateTask }) => {
  const { page, task } = privateTask
  await page.evaluate(taskName => {
    const observer = new MutationObserver(() => {
      const panel = document.querySelector('.notification-list')
      if (!panel) return
      observer.disconnect()
      setTimeout(() => {
        const item = document.createElement('div')
        item.className = 'notification-item'
        item.textContent = taskName
        panel.appendChild(item)
      }, 250)
    })
    observer.observe(document.body, { childList: true, subtree: true })
  }, task.task_name)
  await expect(exerciseTaskNotifications(privateTask, false)).rejects.toThrow(/forbidden late task notification/)
  expect(task.show_notifications).toBe(true)
})
