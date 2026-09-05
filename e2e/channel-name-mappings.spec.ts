import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { resolve } from 'node:path';

if (process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true' || !process.env.ECM_PYTHON) {
  throw new Error('Mapping contract requires isolated exact build and ECM_PYTHON project interpreter');
}

test('selected names -> persisted mapping -> Create grouping and management', async ({ page }, testInfo) => {
  page.setDefaultTimeout(5000);
  const child = spawn(process.env.ECM_PYTHON!, ['-m', 'tests.fixtures.channel_name_mapping_server'], {
    cwd: resolve('backend'),
    env: { ...process.env, ECM_TEST_CONFIG_ROOT: resolve('.test-config'), PYTHONDONTWRITEBYTECODE: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  let port = '';
  child.stdout.on('data', chunk => { output += chunk; port = output.match(/MAPPING_API_PORT=(\d+)/)?.[1] ?? ''; });
  child.stderr.on('data', chunk => { output += chunk; });
  try {
    await expect.poll(() => port || (child.exitCode !== null ? `exited: ${output}` : ''), { timeout: 20000 }).toMatch(/^\d+$/);
    const backend = `http://127.0.0.1:${port}`;
    await expect.poll(async () => {
      try { return (await fetch(`${backend}/api/normalization/mappings`)).status; } catch { return 0; }
    }).toBe(200);
    const streams = ['Stars.TV', 'Stars-TV'].map((name, index) => ({
      id: index + 1, name, channel_group_name: 'Provider', channel_group: null, m3u_account: index + 1,
      url: `https://fixture.invalid/${index}`, stream_profile_id: null,
    }));
    const paginated = (results: unknown[]) => ({ count: results.length, next: null, previous: null, results });
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.startsWith('/api/normalization/mappings') || path === '/api/normalization/normalize') {
        const response = await route.fetch({ url: `${backend}${path}` });
        await route.fulfill({ response });
        return;
      }
      const data: Record<string, unknown> = {
        '/api/profile-conflict-reviews': { reviews: [] },
        '/api/auth/status': { require_auth: false, setup_complete: true, dispatcharr_enabled: false },
        '/api/auth/setup-required': { required: false },
        '/api/settings': { configured: true, url: 'https://fixture.invalid', normalize_on_channel_create: false, show_hide_controls: true, default_channel_profile_ids: [] },
        '/api/channels/logos': paginated([]),
        '/api/channels': paginated([]),
        '/api/channel-groups': [{ id: 5, name: 'Mapped output', channel_count: 0 }],
        '/api/stream-groups': [{ name: 'Provider', count: 2 }],
        '/api/streams': paginated(streams),
        '/api/notifications': { notifications: [], unread_count: 0 },
      };
      await route.fulfill({ json: data[path] ?? [] });
    });
    await page.goto('/#channel-manager');
    await expect(page.locator('.streams-pane')).toBeVisible();
    const close = page.getByRole('button', { name: 'Close', exact: true });
    if (await close.isVisible()) await close.click();
    // The selection is made in the real stream pane, not injected into the editor.
    await page.getByRole('button', { name: 'edit Edit Mode' }).click();
    await page.locator('.streams-pane').getByRole('button', { name: /^Other/ }).click();
    await page.locator('.streams-pane').getByRole('button', { name: /Provider$/, exact: false }).click();
    await page.getByRole('checkbox', { name: 'Select stream Stars.TV', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Select stream Stars-TV', exact: true }).click();
    await page.locator('.streams-pane').getByRole('button', { name: 'Add mapping', exact: true }).click();
    await expect(page.getByLabel('Alternative names (one per line)')).not.toBeEmpty();
    await page.getByLabel('Preferred name', { exact: true }).fill('Stars TV HD');
    await page.getByLabel('Alternative names (one per line)').fill('Stars.TV\nStars-TV');
    await page.getByRole('button', { name: 'Save mapping', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Mapping saved' })).toBeVisible();
    await page.getByRole('button', { name: 'Close mapping editor' }).click();
    await page.locator('.streams-pane').getByRole('button', { name: 'playlist_add Create', exact: true }).click();
    await expect(page.locator('.bulk-create-modal')).toContainText('Stars TV HD');
    await expect(page.locator('.bulk-create-modal')).toContainText('2 streams');
    await page.locator('.bulk-create-modal input[type="number"]').fill('100');
    await page.locator('.bulk-create-modal').getByText('Channel Group', { exact: true }).click();
    await page.locator('.bulk-create-modal').getByRole('radio', { name: 'Create new group' }).check();
    await page.getByPlaceholder('New group name').fill('Alias output');
    await page.screenshot({ path: testInfo.outputPath('mapped-create-preview.png'), fullPage: true });
    await page.locator('.bulk-create-modal').getByRole('button', { name: /Create 1 Channels/ }).click();
    await expect(page.locator('.bulk-create-modal')).toHaveCount(0);
    await page.locator('.channels-pane').getByRole('button', { name: /Alias output$/ }).click();
    await expect(page.locator('.channels-pane .channel-item')).toHaveCount(1);
    await expect(page.locator('.channels-pane .channel-item')).toContainText('Stars TV HD');
    await expect(page.locator('.channels-pane .channel-item .channel-streams-count')).toHaveAttribute('aria-label', /^2 streams;/);
    // Staging exercises App's grouping/assignment, but never commits to Dispatcharr.
    await page.getByRole('link', { name: 'Mapped channels', exact: true }).click();
    await page.getByRole('button', { name: 'Discard', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Stars TV HD' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('button', { name: 'Edit Stars TV HD' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit Stars TV HD' }).click();
    await expect(page.getByLabel('Alternative names (one per line)')).toHaveValue('Stars TV HD\nStars.TV\nStars-TV');
    await page.screenshot({ path: testInfo.outputPath('mapped-channels-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: 'Collapse navigation', exact: true }).click();
    await page.getByLabel('Preferred name', { exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByLabel('Preferred name', { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('mapped-channels-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('button', { name: 'Remove Stars TV HD' }).click();
    await expect(page.getByText('No mappings defined.')).toBeVisible();
    const remaining = await (await fetch(`${backend}/api/normalization/mappings`)).json();
    expect(remaining).toEqual({ mappings: [] });
  } finally {
    if (child.exitCode === null) {
      const exited = once(child, 'exit');
      child.kill('SIGTERM');
      await exited;
    }
    await testInfo.attach('mapping-api.log', { body: output, contentType: 'text/plain' });
  }
});
