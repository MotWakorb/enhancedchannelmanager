import { test, expect, type Locator } from '@playwright/test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { runInNewContext } from 'node:vm'

// Execute the actual spec callbacks with controlled Page/Response objects. This
// is intentionally not a second implementation of the notification test flow.
const requireFrontend = createRequire(resolve('frontend/package.json'))
const ts = requireFrontend('typescript') as typeof import('../frontend/node_modules/typescript')
function callbacks(mutation?: 'completion' | 'teardown') {
  const cases: Array<(fixtures: object) => Promise<void>> = []
  let fixture!: (args: object, use: () => Promise<void>, info: object) => Promise<void>
  const register = (_name: string, fn: (fixtures: object) => Promise<void>) => { cases.push(fn) }
  Object.assign(register, {
    describe: (_name: string, fn: () => void) => fn(),
  })
  const load = (file: string): Record<string, unknown> => {
    const exports = {}
    let source = readFileSync(resolve('e2e', file), 'utf8')
    if (mutation && file === 'fixtures/task-notifications.ts') {
      const target = mutation === 'teardown' ? 'task.show_notifications = original' :
        "assert.equal(result.success, true, 'run must reach terminal success')"
      assert.ok(source.includes(target), 'mutant must change the intended executable statement')
      source = source.replace(target, '')
    }
    const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
    runInNewContext(js, { exports, require: (name: string) => {
      if (name === './fixtures/task-notifications') return { ...load('fixtures/task-notifications.ts'), test: register }
      if (name === './base') return { navigateToTab: async () => {} }
      if (name === '@playwright/test') return { test: { extend: (fixtures: { privateTask: typeof fixture }) => {
        fixture = fixtures.privateTask
        return { use: () => {} }
      } } }
      if (name === 'node:assert/strict') return assert
      throw Error(`Unexpected dependency: ${name}`)
    }, console, Date, URL, Error, setTimeout, clearTimeout }, { filename: file })
    return exports
  }
  load('task-notifications.spec.ts')
  assert.equal(cases.length, 2, 'both real enabled/disabled spec callbacks must be exercised')
  return { cases, fixture }
}

async function verifyLifecycle(initial: boolean, outcome: string, enabled: boolean, mutation?: 'completion' | 'teardown') {
  const { cases } = callbacks(mutation)
  const state = { now: Date.now(), checked: initial, show_notifications: initial, ran: false, completed: false, settled: false, observed: false, armed: false, sleeps: [] as number[] }
  const task = {
    task_id: 'rdnia_probe', task_name: 'Verification Probe',
    get show_notifications() { return state.show_notifications },
    set show_notifications(value: boolean) { state.show_notifications = value },
  }
  const locator = (selector: string): Locator => ({
    locator: (sub: string) => locator(`${selector} ${sub}`),
    filter: (options: { hasText: string | RegExp }) => locator(`${selector} ${options.hasText}`),
    first: () => locator(selector),
    getByRole: (_role: string, options: { name: string }) => locator(`${selector} ${options.name}`),
    all: async () => [],
    isChecked: async () => state.checked,
    setChecked: async (value: boolean) => { state.checked = value },
    waitFor: async () => {
      if (selector.includes('Run Now')) {
        assert.ok(state.completed, 'cannot await settled UI before terminal result')
        state.settled = true
      }
      if (selector.includes('.notification-item') && !enabled) {
        const error = new Error('negative observation elapsed')
        error.name = 'TimeoutError'
        throw error
      }
    },
    click: async () => {
      if (selector.includes('Save')) state.show_notifications = state.checked
      if (selector.includes('Run Now')) {
        assert.ok(state.armed, 'completion must be armed before clicking Run Now')
        if (outcome === 'click-error') throw Error('injected click failure')
        state.ran = true
      }
    },
    count: async () => {
      assert.ok(state.completed && state.settled, 'notification observation requires successful completion and settled UI')
      state.observed = true
      if (outcome === 'notification-error') throw Error('injected notification failure')
      return state.completed && enabled ? 1 : 0
    },
  }) as unknown as Locator
  const request = { method: () => 'POST', url: () => 'http://fixture.invalid/api/tasks/rdnia_probe/run' }
  const response = {
    url: request.url, request: () => request, status: () => 200, finished: async () => null,
    json: async () => {
      state.completed = true
      return {
        success: outcome !== 'failed' && outcome !== 'cancelled', error: outcome === 'cancelled' ? 'CANCELLED' : undefined,
        started_at: new Date(state.now).toISOString(), completed_at: new Date(state.now + (outcome === 'delayed' ? 6000 : 1)).toISOString(),
        total_items: 1, success_count: 1, failed_count: 0, skipped_count: 0,
      }
    },
  }
  const page = {
    locator, getByTestId: (id: string) => locator(id), evaluate: async () => {},
    waitForTimeout: async (ms: number) => { state.sleeps.push(ms); state.now += ms },
    waitForResponse: async (predicate: (value: typeof response) => boolean) => {
      if (state.completed) {
        const notifications = { ...response, url: () => 'http://fixture.invalid/api/notifications', request: () => ({ ...request, method: () => 'GET' }) }
        assert.ok(predicate(notifications))
        return notifications
      }
      state.armed = true
      await Promise.resolve()
      if (outcome === 'never') throw Error('completion timeout')
      assert.ok(predicate(response), 'completion predicate must accept this exact POST')
      assert.equal(predicate({ ...response, url: () => 'http://fixture.invalid/api/tasks/other/run' }), false)
      assert.equal(predicate({ ...response, url: () => 'http://fixture.invalid/api/tasks/rdnia_probe/history' }), false)
      assert.equal(predicate({ ...response, request: () => ({ ...request, method: () => 'GET' }) }), false)
      return response
    },
  }
  let error: unknown
  try {
    await cases[enabled ? 1 : 0]({ privateTask: { page, task } })
  } catch (caught) { error = caught }
  const succeeds = outcome === 'fast' || outcome === 'delayed'
  assert.equal(error === undefined, succeeds, `lifecycle outcome must reflect terminal success: ${String(error)}`)
  if (!succeeds) {
    const expected = outcome === 'never' ? /completion timeout/ :
      outcome === 'click-error' ? /injected click failure/ :
        outcome === 'notification-error' ? /injected notification failure/ : /terminal success|run error/
    expect(String(error)).toMatch(expected)
  }
  expect(state.show_notifications, 'original boolean must survive every outcome').toBe(initial)
  expect(state.sleeps).toEqual([])
  if (succeeds) {
    expect(state.ran).toBe(true)
    expect(state.armed).toBe(true)
    expect(state.completed).toBe(true)
    expect(state.observed).toBe(true)
  } else if (['never', 'failed', 'cancelled'].includes(outcome)) {
    expect(state.observed, 'no notification observation before successful completion').toBe(false)
  }
}

for (const initial of [true, false]) {
  for (const outcome of ['never', 'failed', 'cancelled', 'fast', 'delayed', 'click-error', 'notification-error']) {
    for (const enabled of [false, true]) {
      test(`${enabled ? 'enabled' : 'disabled'} / ${outcome} / initially ${initial}`, () => verifyLifecycle(initial, outcome, enabled))
    }
  }
}

test('terminal-success mutant is rejected by the real callback controls', async () => {
  await expect(verifyLifecycle(true, 'failed', false, 'completion')).rejects.toThrow(/lifecycle outcome must reflect terminal success/)
})

test('missing-finally-restore mutant is rejected by the real callback controls', async () => {
  await expect(verifyLifecycle(true, 'click-error', false, 'teardown')).rejects.toThrow(/original boolean/)
})

test('private fixture refuses normal/live configuration before touching page or state', async () => {
  const { fixture } = callbacks()
  let used = false
  await expect(fixture({}, async () => { used = true }, { project: { metadata: {} } })).rejects.toThrow(/live runs are forbidden/)
  expect(used).toBe(false)
})
