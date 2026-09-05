import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { resolve } from 'node:path';

test.skip(process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true' || !process.env.ECM_PYTHON,
  'Cleanup contract requires isolated exact build and project interpreter');

test('cleanup opt-in persists, preview is read-only, executor history explains detaches', async ({ page }, testInfo) => {
  const child = spawn(process.env.ECM_PYTHON!, ['-m', 'tests.fixtures.event_sync_cleanup_server'], {
    cwd: resolve('backend'), env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  let port = '';
  child.stdout.on('data', chunk => { output += chunk; port = output.match(/CLEANUP_API_PORT=(\d+)/)?.[1] ?? ''; });
  child.stderr.on('data', chunk => { output += chunk; });
  try {
    await expect.poll(() => port || (child.exitCode !== null ? `exited: ${output}` : ''), { timeout: 20000 }).toMatch(/^\d+$/);
    const backend = `http://127.0.0.1:${port}`;
    await expect.poll(async () => {
      try { return (await fetch(`${backend}/fixture/state`)).status; } catch { return 0; }
    }).toBe(200);
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      if (/^\/api\/channel-pipeline\/(rules|event-sync-preview|executions|schema)/.test(path)) {
        const response = await route.fetch({ url: `${backend}${path}${url.search}` });
        await route.fulfill({ response });
        return;
      }
      const data: Record<string, unknown> = {
        '/api/profile-conflict-reviews': { reviews: [] },
        '/api/event-sync-reviews': { reviews: [], total: 0 },
        '/api/event-sync-exclusions': { exclusions: [], total: 0 },
        '/api/auth/status': { require_auth: false, setup_complete: true, dispatcharr_enabled: false },
        '/api/auth/setup-required': { required: false },
        '/api/settings': { configured: true, url: 'https://fixture.invalid' },
        '/api/notifications': { notifications: [], unread_count: 0 },
        '/api/channel-groups': [{ id: 10, name: 'Master' }, { id: 20, name: 'Secondary' }],
        '/api/providers/group-settings/by-provider': [],
        '/api/channel-pipeline/circuit-breaker': { tripped: false },
      };
      await route.fulfill({ json: data[path] ?? [] });
    });
    await page.goto('/#channel-pipeline');
    await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await page.getByTestId('event-sync-step-3').click();
    const toggle = page.getByTestId('event-sync-detach-stale-streams');
    await expect(toggle).not.toBeChecked();
    await toggle.check();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByTestId('event-sync-editor')).toHaveCount(0);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await page.getByTestId('event-sync-step-3').click();
    await expect(toggle).toBeChecked();
    await page.getByRole('button', { name: 'Preview matches', exact: false }).click();
    await expect(page.getByRole('region', { name: 'Stale attachment cleanup' })).toContainText('would detach stream 2');
    expect(await (await fetch(`${backend}/fixture/state`)).json()).toEqual({ streams: [1, 2], writes: 1 });
    await page.screenshot({ path: testInfo.outputPath('cleanup-preview-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('region', { name: 'Stale attachment cleanup' }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('region', { name: 'Stale attachment cleanup' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('cleanup-preview-mobile.png'), fullPage: true });
    const result = await (await fetch(`${backend}/fixture/run`, { method: 'POST' })).json();
    expect(result.streams).toEqual([1, 3]);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.reload();
    const detailsLoaded = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/channel-pipeline/executions/2' && response.status() === 200);
    await page.locator('.execution-section').getByRole('button', { name: /details/i }).click();
    await detailsLoaded;
    await expect(page.getByRole('region', { name: 'Stale attachment cleanup' })).toContainText('detached stream 2');
    await expect(page.getByRole('region', { name: 'Stale attachment cleanup' })).toContainText('same account protected');
  } finally {
    await page.unrouteAll({ behavior: 'wait' });
    if (child.exitCode === null) {
      const exited = once(child, 'exit');
      child.kill('SIGTERM');
      await exited;
    }
    await testInfo.attach('cleanup-api.log', { body: output, contentType: 'text/plain' });
  }
});
