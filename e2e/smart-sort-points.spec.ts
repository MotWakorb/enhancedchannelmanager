import { execFileSync, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createHTTPServer } from 'node:http'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { expect, test, type Locator } from '@playwright/test'

const ROOT = process.cwd()
const FRONTEND = path.join(ROOT, 'frontend')
const BACKEND = path.join(ROOT, 'backend')
const VITE_CONFIG = 'vite.smart-sort-points-e2e.config.ts'

const EXPECTED_RULES = [
  { criterion: 'resolution', operator: 'gte', value: 1080, points: 20 },
  { criterion: 'failed', operator: 'eq', value: true, points: 100 },
  { criterion: 'bitrate', operator: 'lt', value: 5000, points: -25 },
]

let backend: ChildProcessWithoutNullStreams
let preview: ChildProcessWithoutNullStreams
let temporaryDirectory = ''
let appURL = ''

function reservePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error('Could not reserve an E2E harness port'))
        return
      }
      server.close((error) => error ? reject(error) : resolve(address.port))
    })
  })
}

function captureOutput(child: ChildProcessWithoutNullStreams): () => string {
  let output = ''
  child.stdout.on('data', (chunk) => { output += chunk.toString() })
  child.stderr.on('data', (chunk) => { output += chunk.toString() })
  return () => output.slice(-12_000)
}

async function waitForURL(
  url: string,
  child: ChildProcessWithoutNullStreams,
  output: () => string,
  expectedNonce: string,
  timeoutMs = 60_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Harness process exited with ${child.exitCode}\n${output()}`)
    }
    try {
      const response = await fetch(url)
      if (response.ok) {
        const readiness = await response.json()
        if (readiness?.nonce === expectedNonce) return
      }
    } catch {
      // The isolated server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`Timed out waiting for ${url}\n${output()}`)
}

async function stopProcess(child: ChildProcessWithoutNullStreams | undefined): Promise<void> {
  if (!child?.pid || child.exitCode !== null) return
  signalProcessGroup(child.pid, 'SIGTERM')
  await Promise.race([
    once(child, 'exit'),
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ])
  if (child.exitCode === null) signalProcessGroup(child.pid, 'SIGKILL')
}

function signalProcessGroup(pid: number, signal: NodeJS.Signals): void {
  try {
    process.kill(-pid, signal)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error
  }
}

async function chooseOption(control: Locator, option: string): Promise<void> {
  await control.click()
  await control.page().getByRole('option', { name: option, exact: true }).click()
}

function pointRule(page: import('@playwright/test').Page, index: number): Locator {
  return page.getByTestId('smart-sort-point-rule').nth(index)
}

async function chooseStrategy(
  page: import('@playwright/test').Page,
  strategy: 'Points' | 'Priority',
): Promise<void> {
  const group = page.getByRole('radiogroup', { name: 'Smart Sort strategy' })
  await group.getByText(strategy, { exact: true }).click()
  await expect(group.getByRole('radio', { name: strategy })).toBeChecked()
}

test.use({ serviceWorkers: 'block' })
test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  test.setTimeout(120_000)
  temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'ecm-smart-sort-points-e2e-'))
  const [frontendPort, backendPort] = await Promise.all([reservePort(), reservePort()])
  const harnessNonce = randomUUID()
  appURL = `http://127.0.0.1:${frontendPort}`
  const apiURL = `http://127.0.0.1:${backendPort}`
  const environment = {
    ...process.env,
    CONFIG_DIR: path.join(temporaryDirectory, 'config'),
    MCP_SECRETS_DIR: path.join(temporaryDirectory, 'mcp-secrets'),
    RATE_LIMIT_ENABLED: '0',
    SMART_SORT_POINTS_E2E_API: apiURL,
    SMART_SORT_POINTS_E2E_BACKEND_PORT: String(backendPort),
    SMART_SORT_POINTS_E2E_DIST: path.join(temporaryDirectory, 'frontend-dist'),
    SMART_SORT_POINTS_E2E_NONCE: harnessNonce,
    SMART_SORT_POINTS_E2E_PORT: String(frontendPort),
  }

  backend = spawn(process.env.PYTHON ?? 'python3', ['e2e/smart_sort_points_harness.py'], {
    cwd: ROOT,
    env: {
      ...environment,
      PYTHONPATH: [BACKEND, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  })
  await waitForURL(
    `${apiURL}/api/health`, backend, captureOutput(backend), harnessNonce,
  )

  execFileSync('npx', ['vite', 'build', '--config', VITE_CONFIG], {
    cwd: FRONTEND,
    env: environment,
    stdio: 'inherit',
  })
  preview = spawn('npx', ['vite', 'preview', '--config', VITE_CONFIG], {
    cwd: FRONTEND,
    env: environment,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  })
  await waitForURL(
    `${appURL}/api/health`, preview, captureOutput(preview), harnessNonce,
  )
})

test.afterAll(async () => {
  await stopProcess(preview)
  await stopProcess(backend)
  if (temporaryDirectory) await rm(temporaryDirectory, { recursive: true, force: true })
})

test('readiness rejects an unrelated successful server with the wrong nonce', async () => {
  const unrelatedServer = createHTTPServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end(JSON.stringify({ status: 'healthy', nonce: 'wrong-harness' }))
  })
  unrelatedServer.listen(0, '127.0.0.1')
  await once(unrelatedServer, 'listening')

  try {
    const address = unrelatedServer.address()
    if (!address || typeof address === 'string') throw new Error('Test server did not bind')
    await expect(waitForURL(
      `http://127.0.0.1:${address.port}`,
      backend,
      () => '',
      'expected-harness',
      300,
    )).rejects.toThrow('Timed out waiting for')
  } finally {
    await new Promise<void>((resolve, reject) => {
      unrelatedServer.close((error) => error ? reject(error) : resolve())
    })
  }
})

test('persisted Points rules drive manual Smart Sort ordering', async ({ page }) => {
  test.setTimeout(90_000)
  await page.goto(`${appURL}/#settings/channel-defaults`, { waitUntil: 'domcontentloaded' })
  await expect(page.locator('.settings-content-main[data-settings-page="channel-defaults"]')).toBeVisible()

  const videoCodecPriority = page.getByRole('checkbox', { name: 'Video Codec — use as a stream sort criterion' })
  await expect(videoCodecPriority).not.toBeChecked()
  await videoCodecPriority.check()

  await chooseStrategy(page, 'Points')
  for (let index = 0; index < EXPECTED_RULES.length; index += 1) {
    await page.getByRole('button', { name: 'Add rule' }).click()
  }

  const resolutionRule = pointRule(page, 0)
  await resolutionRule.getByLabel('Points (signed integer)').fill('20')

  const failedRule = pointRule(page, 1)
  await chooseOption(failedRule.getByLabel('Condition'), 'Failed Streams')
  await failedRule.getByLabel('Points (signed integer)').fill('100')

  const bitrateRule = pointRule(page, 2)
  await chooseOption(bitrateRule.getByLabel('Condition'), 'Bitrate')
  await chooseOption(bitrateRule.getByLabel('Operator'), 'Less than (<)')
  await bitrateRule.getByLabel('Value (kbps)').fill('5000')
  await bitrateRule.getByLabel('Points (signed integer)').fill('-25')

  const settingsSave = page.waitForResponse((response) => (
    response.url().endsWith('/api/settings')
      && response.request().method() === 'POST'
  ))
  await page.getByRole('button', { name: 'Save Settings' }).click()
  const saveResponse = await settingsSave
  expect(saveResponse.status()).toBe(200)
  expect(await saveResponse.json()).toMatchObject({ status: 'saved', configured: true })
  expect(saveResponse.request().postDataJSON()).toMatchObject({
    stream_sort_strategy: 'points',
    stream_sort_point_rules: EXPECTED_RULES,
    stream_sort_enabled: { video_codec: true },
  })
  await expect(
    page.locator('#main-content').getByRole('status').filter({ hasText: 'Settings saved successfully' }),
  ).toBeAttached()

  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('smart-sort-point-rule')).toHaveCount(3)
  await expect(page.getByRole('radio', { name: 'Points' })).toBeChecked()
  await expect(pointRule(page, 0).getByLabel('Points (signed integer)')).toHaveValue('20')
  await expect(pointRule(page, 1).getByLabel('Condition')).toContainText('Failed Streams')
  await expect(pointRule(page, 1).getByLabel('Points (signed integer)')).toHaveValue('100')
  await expect(pointRule(page, 2).getByLabel('Condition')).toContainText('Bitrate')
  await expect(pointRule(page, 2).getByLabel('Operator')).toContainText('Less than (<)')
  await expect(pointRule(page, 2).getByLabel('Value (kbps)')).toHaveValue('5000')
  await expect(pointRule(page, 2).getByLabel('Points (signed integer)')).toHaveValue('-25')

  const persistedSettings = await page.evaluate(async () => {
    const response = await fetch('/api/settings')
    if (!response.ok) throw new Error(`GET /api/settings failed: ${response.status}`)
    return response.json()
  })
  expect(persistedSettings).toMatchObject({
    stream_sort_strategy: 'points',
    stream_sort_point_rules: EXPECTED_RULES,
    stream_sort_enabled: { video_codec: true },
  })

  await chooseStrategy(page, 'Priority')
  await expect(videoCodecPriority).toBeChecked()
  await expect(page.getByText('Deprioritize Failed Streams')).toBeVisible()
  await chooseStrategy(page, 'Points')
  await expect(page.getByTestId('smart-sort-point-rule')).toHaveCount(3)

  if (process.env.SMART_SORT_POINTS_DANGEROUS_MUTANT === 'priority') {
    const response = await page.request.post(`${appURL}/api/e2e/mutate-sort-settings`)
    expect(response.ok()).toBe(true)
  }

  await page.getByRole('button', { name: 'Back to main navigation' }).click()
  await page.getByRole('link', { name: 'Channel Manager' }).click()
  await expect(page.locator('.channels-pane')).toBeVisible()
  await page.locator('.channels-pane .group-header').filter({ hasText: 'Fixture Channels' }).click()
  const channel = page.locator('.channels-pane .channel-item').filter({ hasText: 'Points Sorting Fixture' })
  await expect(channel).toBeVisible()
  await channel.click()
  await expect(page.locator('.inline-stream-item')).toHaveCount(2)
  await page.locator('.enter-edit-mode-btn').click()
  await expect(page.locator('.edit-mode-done-btn')).toBeVisible()

  const computeResponsePromise = page.waitForResponse((response) => (
    response.url().endsWith('/api/stream-stats/compute-sort')
      && response.request().method() === 'POST'
  ))
  await page.getByRole('button', { name: 'Sort streams' }).click()
  await page.locator('.sort-dropdown-menu').getByText('Smart Sort', { exact: true }).click()
  const computeResponse = await computeResponsePromise
  expect(computeResponse.status()).toBe(200)
  expect(computeResponse.request().postDataJSON()).toEqual({
    channels: [{ channel_id: 41, stream_ids: [101, 202] }],
    mode: 'smart',
  })
  expect(await computeResponse.json()).toEqual({
    results: [{ channel_id: 41, sorted_stream_ids: [202, 101], changed: true }],
  })

  await expect(page.locator('.inline-stream-item .inline-stream-name')).toHaveText([
    'Failed 720p',
    'Healthy 1080p',
  ])
})
