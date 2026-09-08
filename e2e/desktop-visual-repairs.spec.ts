import { expect as baseExpect, test as baseTest } from '@playwright/test';
import type { Locator, Page, TestInfo } from '@playwright/test';

const expect = baseExpect.configure({ timeout: 1000 });
const test = baseTest.extend({
  page: async ({ page, baseURL }, use) => {
    if (!baseURL) throw new Error('Desktop visual tests require a resolved Playwright baseURL');
    const origin = new URL(baseURL).origin;
    await page.route('**/*', route => new URL(route.request().url()).origin === origin
      ? route.continue() : route.abort('blockedbyclient'));
    await page.routeWebSocket('**/*', socket => socket.close());
    await use(page);
  },
});

async function contrast(locator: Locator) {
  return locator.evaluate(element => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d')!;
    const rgba = (color: string) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data];
    };
    let background = [255, 255, 255, 255];
    const ancestors: Element[] = [];
    for (let node: Element | null = element; node; node = node.parentElement) ancestors.unshift(node);
    for (const node of ancestors) {
      const color = rgba(getComputedStyle(node).backgroundColor);
      background = background.map((value, i) => i === 3 ? 255 : color[i] * color[3] / 255 + value * (1 - color[3] / 255));
    }
    const luminance = (rgb: number[]) => rgb.slice(0, 3).reduce((sum, value, i) => {
      const c = value / 255;
      return sum + (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][i];
    }, 0);
    const a = luminance(rgba(getComputedStyle(element).color));
    const b = luminance(background);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });
}

async function capture(page: Page, info: TestInfo, name: string) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(document.getAnimations().filter(animation =>
      animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => undefined)));
  });
  await page.screenshot({ path: info.outputPath(`${name}.png`) });
}

async function openSynthetic(page: Page, route: string, theme: string, fixtures: Record<string, unknown> = {}) {
  const responses: Record<string, unknown> = {
    '/api/auth/status': { require_auth: false, setup_complete: true },
    '/api/auth/me': { user: { id: 1, username: 'synthetic', is_admin: true, is_active: true } },
    '/api/auth/setup-required': { required: false },
    '/api/settings': { configured: true, theme },
    '/api/profile-conflict-reviews': { reviews: [], total: 0 },
    '/api/health': { status: 'ok', configured: true, dispatcharr_connected: false },
    '/api/notifications': { notifications: [], total: 0, unread_count: 0 },
    ...fixtures,
  };
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ status: Object.hasOwn(responses, path) ? 200 : 501,
      contentType: 'application/json', body: JSON.stringify(responses[path] ?? { detail: 'Unapproved synthetic request' }) });
  });
  await page.goto(`/#${route}`);
  if (theme !== 'dark') await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}

// G38: exercise the production opener over populated, synthetic background rows.
for (const theme of ['dark', 'light', 'high-contrast']) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
    test(`G05 Streams hosted mapping ${theme} ${viewport.width}`, async ({ page }, info) => {
        await page.setViewportSize({ width: 1280, height: viewport.height });
        await openSynthetic(page, 'channel-manager', theme, {
          '/api/channels': { count: 0, results: [], next: null },
          '/api/channel-groups': [{ id: 1, name: 'Synthetic', channel_count: 0 }],
          '/api/stream-groups': [{ name: 'Synthetic', count: 1 }],
          '/api/streams': { count: 1, next: null, results: [{ id: 1, name: 'Synthetic news', url: '/synthetic/stream',
            m3u_account: 1, tvg_id: 'synthetic.1', channel_group: 1, channel_group_name: 'Synthetic', is_custom: false }] },
          '/api/normalization/mappings': { mappings: [{ id: 1, preferred_name: 'Synthetic news', aliases: ['Synthetic HD'] }] },
          '/api/normalization/mappings/resolve': { results: [{ original: 'Synthetic news', preferred_name: null }] },
        });
        await page.getByTitle('Enter Edit Mode to make changes').click();
        await page.getByRole('button', { name: 'Expand all groups', exact: true }).last().click();
        await page.locator('.stream-group .group-toggle-btn').first().click();
        await page.locator('.stream-group .group-toggle-btn').first().click();
        await page.getByRole('checkbox', { name: 'Select stream Synthetic news', exact: true }).click();
        await page.getByRole('button', { name: 'Add mapping', exact: true }).click();
      for (const width of [1280, viewport.width]) {
        await page.setViewportSize({ width, height: viewport.height });
        await capture(page, info, `G05-Streams-${width}`);
        await expect.soft(page.locator('.mapped-channels label').first()).toHaveCSS('font-size', '13px');
        await expect.soft(page.locator('.mapped-channels legend')).toHaveCSS('font-size', '13px');
        await expect(page.locator('.mapped-channels > p').first()).toHaveCSS('font-size', '13px');
      }
      await page.route('**/api/normalization/mappings/1', route => route.fulfill({ status: 422,
        contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic alias validation error' }) }));
      await page.getByLabel('Preferred name', { exact: true }).fill('');
      await capture(page, info, 'G05-Streams-empty-preferred');
      expect(await page.getByLabel('Preferred name', { exact: true }).evaluate(e => (e as HTMLInputElement).validity.valueMissing)).toBe(true);
      await page.getByRole('radio', { name: 'Existing', exact: true }).check();
      await page.getByRole('button', { name: 'Existing mapping', exact: true }).click();
      await page.getByRole('option', { name: 'Synthetic news', exact: true }).click();
      await page.getByRole('button', { name: 'Save mapping', exact: true }).click();
      await expect(page.locator('.mapped-channels [role=alert]')).toBeVisible();
      await capture(page, info, 'G05-Streams-alias-validation');
      await expect(page.locator('.mapped-channels [role=alert]')).toHaveCSS('font-size', '13px');
    });
    test(`G49 numbering conflict glyph ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const channels = { count: 1, total: 1, next: null, results: [{ id: 1, name: 'Synthetic news', channel_number: 101,
        channel_group_id: 1, tvg_id: 'synthetic.1', epg_data_id: null, streams: [], logo_id: null }] };
      await openSynthetic(page, 'channel-manager', theme, {
        '/api/channels': channels, '/api/channel-groups': [{ id: 1, name: 'Synthetic', channel_count: 1 }],
        '/api/streams': { count: 0, results: [], next: null },
      });
      await page.getByTitle('Enter Edit Mode to make changes').click();
      await page.getByRole('button', { name: 'Expand all groups', exact: true }).first().click();
      await page.getByRole('checkbox', { name: 'Select all channels in group', exact: true }).click();
      await page.getByRole('button', { name: 'Renumber', exact: true }).click();
      await page.locator('.mass-renumber-dialog input[type=number]').first().fill('501');
      await page.locator('.mass-renumber-dialog').getByRole('button', { name: /Renumber/ }).click();
      channels.results = [{ ...channels.results[0], channel_number: 121 }];
      await page.getByTitle('Apply changes', { exact: true }).click();
      await page.getByRole('button', { name: 'Apply All', exact: true }).click();
      const icon = page.locator('.edit-mode-dialog-commit-failure-intro .material-icons');
      await expect(icon).toBeVisible();
      await capture(page, info, 'G49');
      const size = await icon.evaluate(e => ({ width: e.getBoundingClientRect().width, font: parseFloat(getComputedStyle(e).fontSize) }));
      expect(size.width).toBeGreaterThanOrEqual(size.font);
    });
    test(`G49 profile conflict banners ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'dashboard', theme, {
        '/api/profile-conflict-reviews': { reviews: [{ id: 9, fingerprint: 'synthetic', effective_group_id: 1,
          status: 'pending', evidence: { target: { effective_group_id: 1, name: 'Synthetic events' }, choices: [
            { choice_key: 'sports', profile_ids: [1], profile_names: ['Synthetic sports'], sources: [{ source_group_id: 1,
              source_group_name: 'Synthetic events', m3u_account_id: 1, m3u_account_name: 'Synthetic provider' }] },
          ] } }], total: 1 },
        '/api/profile-conflict-reviews/9/accept': { status: 'accepted', applied: false, updated_account_ids: [],
          failed_account_ids: [1], retry_error: 'The synthetic provider is temporarily unavailable; this choice has been saved and will retry automatically.' },
      });
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('radio', { name: /Synthetic sports/ }).check();
      await page.route('**/api/profile-conflict-reviews/9/accept', route => route.fulfill({ status: 409,
        contentType: 'application/json', body: JSON.stringify({ detail: 'The synthetic conflict changed while this review was open. Refresh the source evidence before selecting a profile again.' }) }));
      await dialog.getByRole('button', { name: 'Apply selected choice', exact: true }).click();
      const icon = dialog.locator('.profile-conflict-message .material-icons');
      await expect(icon).toBeVisible();
      await capture(page, info, 'G49-profile-error');
      expect.soft(await icon.evaluate(e => e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(18);
      await page.unroute('**/api/profile-conflict-reviews/9/accept');
      await dialog.getByRole('button', { name: 'Apply selected choice', exact: true }).click();
      await expect(dialog.getByText(/saved.*retry/i)).toBeVisible();
      await capture(page, info, 'G49-profile-partial');
      expect(await icon.evaluate(e => e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(18);
    });
    test(`G17 selected run prose ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'channel-pipeline', theme, {
        '/api/channel-pipeline/rules': { rules: [{ id: 1, name: 'Synthetic rule', enabled: true, priority: 0,
          conditions: [], actions: [{ type: 'skip' }], rule_kind: 'standard' }] },
        '/api/channel-pipeline/executions': { executions: [], total: 0 },
      });
      await page.getByRole('checkbox', { name: 'Select all visible rules', exact: true }).check();
      await page.getByRole('button', { name: 'Run selected rules', exact: true }).click();
      await capture(page, info, 'G17');
      await expect(page.locator('.selected-run-confirm .modal-body > p').first()).toHaveCSS('font-size', '13px');
      await expect(page.locator('.selected-run-confirm li').first()).toHaveCSS('font-size', '13px');
    });
    test(`G17 G20 Event Sync secondary owners ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const rule = { id: 5, name: 'Synthetic events', enabled: true, priority: 0, conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }], event_sync_config: { master_group_id: 1, secondary_group_ids: [2],
          time_window_minutes: 30, attach_threshold: 0.8, enabled: true } };
      await openSynthetic(page, 'channel-pipeline', theme, {
        '/api/channel-pipeline/rules': { rules: [rule] }, '/api/channel-pipeline/executions': { executions: [], total: 0 },
        '/api/channel-groups': [{ id: 1, name: 'Master Events' }, { id: 2, name: 'Secondary Events' }],
        '/api/providers/group-settings/by-provider': [1, 2].map(id => ({ m3u_account_id: 1, m3u_account_name: 'Synthetic',
          channel_group_id: id, auto_channel_sync: id === 1, enabled: true, stream_count: 10 })),
        '/api/channel-pipeline/event-sync-preview': {
          preflight: { ok: true, failures: [], warnings: [{ check: 'synthetic', message: 'The provider refresh could not verify the selected secondary group; the guard fails open and this advisory does not block preview.', expected: 'available provider group settings', got: 'unavailable provider group settings' }] },
          summary: { secondary_streams: 0, would_attach: 0, ambiguous_skipped: 0, unmatched: 0, parse_failed: 0,
            master_channels: 1, master_channels_unparsed: 0 }, streams: [], unmatched_streams: [], parse_failures: [], unparsed_master_channels: [],
          cleanup: { decisions: [{ channel_id: 1, channel_name: 'Synthetic events', stream_id: 2, decision: 'preserve', reason: 'still_current' }] },
        },
      });
      await page.getByTitle('Edit', { exact: true }).click();
      await page.getByRole('button', { name: /Preview matches/ }).click();
      const warning = page.locator('.event-sync-preflight .material-icons');
      await expect(warning).toBeVisible();
      await warning.scrollIntoViewIfNeeded();
      await capture(page, info, 'G20-preflight');
      expect.soft(await warning.evaluate(e => e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(18);
      await expect.soft(warning).toHaveCSS('font-size', '18px');
      const cleanup = page.getByRole('region', { name: 'Stale attachment cleanup', exact: true });
      await cleanup.scrollIntoViewIfNeeded();
      await capture(page, info, 'G17-cleanup');
      await expect.soft(cleanup.locator('li')).toHaveCSS('font-size', '13px');
      await page.getByRole('textbox', { name: /Rule name/i }).fill('Changed synthetic');
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      const discard = page.getByTestId('event-sync-discard-dialog');
      await expect(discard).toBeVisible();
      await capture(page, info, 'G17-dirty-discard');
      await expect(discard.locator('p')).toHaveCSS('font-size', '13px');
      await page.getByTestId('event-sync-discard-keep').click();
      await expect(page.getByRole('textbox', { name: /Rule name/i })).toHaveValue('Changed synthetic');
    });
    test(`G17 G20 review discard notice ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const reviews = { reviews: [{ id: 1, rule_id: 3, provider_id: 7, status: 'pending', created_at: 1752300000000,
        last_seen_at: 1752300000000, evidence: { rule_name: 'Synthetic events', stream_name: 'Alpha vs Beta',
          provider: 'Synthetic', stream_id: 42, stream_parsed_title: 'Alpha vs Beta', master_channel_name: 'Alpha vs Beta',
          master_channel_id: 5, master_parsed_title: 'Alpha vs Beta', score: 1, band: 'attach', team_verdict: 'agree', time_delta_minutes: 0 } }],
        total: 1, page: 1, page_size: 50, total_pages: 1 };
      await openSynthetic(page, 'channel-pipeline', theme, {
        '/api/channel-pipeline/executions': { executions: [], total: 0 },
        '/api/event-sync-reviews': reviews, '/api/event-sync-exclusions': { exclusions: [], total: 0 },
        '/api/channel-pipeline/rules': { rules: [{ id: 3, name: 'Synthetic events', enabled: true, conditions: [], actions: [], event_sync_config: {} }] },
        '/api/event-sync-reviews/1/accept': { status: 'accepted', attached: false, already_attached: false,
          attach_deferred_reason: 'The selected provider channel is temporarily unavailable after a source refresh. The saved event pairing remains queued for the next pipeline run; verify that the master event channel still exists and the secondary provider group is enabled before retrying the attachment.' },
      });
      await page.locator('.event-sync-review-queue').getByRole('checkbox', { name: /Select all/ }).check();
      await page.getByRole('button', { name: /Discard selected/ }).click();
      const dialog = page.getByRole('dialog');
      await capture(page, info, 'G17-review-discard');
      await expect.soft(dialog.locator('.modal-body p').first()).toHaveCSS('font-size', '13px');
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
      await page.locator('.event-sync-review-queue').getByRole('button', { name: 'Accept & attach', exact: true }).click();
      const icon = page.locator('.event-sync-review-notice .material-icons');
      await expect(icon).toBeVisible();
      await capture(page, info, 'G20-review-notice');
      await expect.soft(icon).toHaveCSS('font-size', '18px');
      expect(await icon.evaluate(e => e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(18);
    });
    test(`G61 restore outcome glyphs ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      for (const consumer of ['saved', 'uploaded']) {
      for (const outcome of ['success', 'completed_with_failures', 'partial_failed_rolled_back', 'failed_rollback_incomplete']) {
        const report: Record<string, unknown> = { contract_version: 1, is_dry_run: true, outcome: null,
          notes: ['Synthetic residue requires manual cleanup.'], categories: [], logo_misses: 0, logo_miss_details: [],
          credentials_needing_reentry: 0, credential_reentry_details: [], channels_needing_stream_reattach: 0,
          channels_with_no_playable_stream: 0, stream_urls_redacted: 0, stream_reattach_details: [],
          channel_group_drift: 0, channel_group_drift_details: [] };
        const task = { task_id: 'dbas_restore', status: 'completed', progress: { status: 'completed', current: 6,
          total: 6, percentage: 100, current_item: '', success_count: 6, failed_count: 0, skipped_count: 0, started_at: 'preview' } };
        await openSynthetic(page, 'settings/backup-restore', theme, {
          '/api/backup/saved': [{ filename: 'synthetic.zip', type: 'zip', size_bytes: 100, created_at: '2026-09-07T00:00:00Z' }],
          '/api/backup/restore-dbas-saved': { task_id: 'dbas_restore' },
          '/api/backup/restore-dbas': { task_id: 'dbas_restore' },
          '/api/tasks/dbas_restore': task, '/api/tasks/dbas_restore/history': { history: [{ id: 1, status: 'completed', details: { restore_report: report } }] },
        });
        if (consumer === 'saved') {
          await page.getByRole('button', { name: 'Restore as DBAS backup', exact: true }).click();
        } else {
          await page.getByRole('button', { name: /Restore from artifact/ }).click();
          const chooser = page.waitForEvent('filechooser');
          await page.getByRole('dialog').locator('.brm-dropzone').click();
          await (await chooser).setFiles({ name: 'synthetic.zip', mimeType: 'application/zip', buffer: Buffer.from('Synthetic rendering fixture, not an archive validity test') });
        }
        await page.getByRole('button', { name: 'Run preview', exact: true }).click();
        await page.getByRole('button', { name: 'Apply these changes', exact: true }).click();
        await page.getByRole('dialog').last().getByRole('textbox').fill('synthetic.zip');
        report.is_dry_run = false; report.outcome = outcome; task.progress.started_at = 'apply';
        await page.getByRole('button', { name: 'Apply restore', exact: true }).click();
        const banner = page.getByTestId('rcs-outcome-banner');
        await expect(banner).toHaveAttribute('data-outcome', outcome);
        await banner.scrollIntoViewIfNeeded();
        await capture(page, info, `G61-${consumer}-${outcome}`);
        const icon = banner.locator('.rcs-outcome-icon');
        expect.soft(await icon.evaluate(e => e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(24);
        await page.goto('about:blank');
      }
      }
    });
    test(`G04 Gracenote text buttons ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'channel-manager', theme, {
        '/api/channels': { count: 1, total: 1, next: null, results: [{ id: 1, name: 'Synthetic news', channel_number: 101,
          channel_group_id: 1, tvg_id: 'synthetic.1', epg_data_id: null, streams: [], logo_id: null }] },
        '/api/channel-groups': [{ id: 1, name: 'Synthetic', channel_count: 1 }],
        '/api/epg/lcn/batch': { results: { 'synthetic.1': { lcn: '12345', source: 'Synthetic guide' } } },
        '/api/streams': { count: 0, results: [], next: null }, '/api/epg/data': [],
      });
      await page.getByTitle('Enter Edit Mode to make changes').click();
      await page.getByRole('button', { name: 'Expand all groups', exact: true }).first().click();
      await page.getByRole('checkbox', { name: 'Select all channels in group', exact: true }).click();
      await page.getByRole('button', { name: 'More selection actions', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Fetch Gracenote IDs', exact: true }).click();
      const buttons = page.locator('.bulk-lcn-modal .section-actions button');
      await expect(buttons).toHaveCount(2);
      await capture(page, info, 'G04');
      for (const button of await buttons.all()) {
        expect.soft(await button.evaluate(e => e.scrollWidth <= e.clientWidth)).toBe(true);
        await button.click();
        await button.focus();
        await expect(button).toBeFocused();
        expect.soft(await button.evaluate(e => e.scrollWidth <= e.clientWidth)).toBe(true);
        await capture(page, info, `G04-${await button.textContent()}`);
      }
    });
    test(`G24 provider heatmap labels ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const providers = [{ id: 1, name: 'Northstar Television' }, { id: 2, name: 'Regional Broadcast Network' }];
      await openSynthetic(page, 'stats', theme, {
        '/api/stats/channels': { channels: [], count: 0 }, '/api/stats/activity': { events: [], count: 0 },
        '/api/m3u/accounts': providers, '/api/providers': providers,
        '/api/stats/providers/buffering': { data: [] }, '/api/stats/providers/watch-time': { data: [] },
        '/api/stats/providers/bitrate': { data: [] },
        '/api/stats/providers/channel-heatmap': { data: providers.map(provider => ({ provider_id: provider.id,
          channel_id: 'synthetic', channel_name: 'Synthetic news', bytes: 9000000000, latest_stream_id: 1, latest_stream_name: 'Synthetic news' })) },
      });
      const chart = page.locator('.heatmap-svg');
      await chart.scrollIntoViewIfNeeded();
      await capture(page, info, 'G24');
      const left = (await chart.boundingBox())!.x;
      await expect(chart.locator('.heatmap-row-label')).toHaveCount(2);
      for (const label of await chart.locator('.heatmap-row-label').all()) {
        expect.soft((await label.boundingBox())!.x).toBeGreaterThanOrEqual(left);
      }
    });
    test(`G20 G58 pipeline result layout ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const execution = { id: 1, mode: 'execute', triggered_by: 'manual', started_at: '2026-09-07T03:00:00Z',
        completed_at: '2026-09-07T03:01:00Z', duration_seconds: 60, status: 'completed_with_errors',
        has_snapshot: true, is_event_sync: false, streams_evaluated: 10, streams_matched: 1, channels_created: 1,
        channels_updated: 0, groups_created: 0, streams_merged: 0, streams_skipped: 0, streams_excluded: 0,
        created_entities: [], modified_entities: [], warnings: [], execution_log: [], run_scope: 'selected',
        selected_rule_integrity: 'valid', selected_rule_ids: [1, 2, 3], selected_rule_outcomes: [
          { rule_id: 1, rule_name: 'Synthetic standard', rule_kind: 'standard', status: 'skipped', skip_reason: 'Disabled at execution time' },
          { rule_id: 2, rule_name: 'Synthetic capped', rule_kind: 'standard', status: 'capped', skip_reason: 'Maximum channels reached' },
          { rule_id: 3, rule_name: 'Synthetic error', rule_kind: 'standard', status: 'error', skip_reason: 'Synthetic provider unavailable' }],
      };
      await openSynthetic(page, 'channel-pipeline', theme, {
        '/api/channel-pipeline/rules': { rules: [] }, '/api/channel-pipeline/executions': { executions: [execution], total: 1 },
        '/api/channel-pipeline/executions/1': execution,
        '/api/channel-pipeline/executions/1/restore-snapshot': { success: false, removed_channels: 2, restored_channels: 3,
          failed_channels: [{ id: 4, name: 'Synthetic Sports HD', error: 'Synthetic channel no longer available' }] },
      });
      await page.getByRole('button', { name: 'View details', exact: true }).click();
      const outcomes = page.locator('.selected-outcomes li');
      await expect(outcomes).toHaveCount(3);
      for (const outcome of await outcomes.all()) {
      await outcome.scrollIntoViewIfNeeded();
      await capture(page, info, 'G58');
      const counter = await outcome.locator('span').boundingBox();
      const reason = await outcome.locator('small').boundingBox();
      expect.soft(reason!.y >= counter!.y + counter!.height || reason!.x >= counter!.x + counter!.width + 4,
        'G58 reason must be separated from counters').toBe(true);
      }
      await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
      await page.getByRole('button', { name: 'Undo this run', exact: true }).click();
      await page.getByRole('button', { name: 'Confirm revert', exact: true }).click();
      const icon = page.locator('.revert-partial-failure .material-icons');
      await expect(icon).toBeVisible();
      await capture(page, info, 'G20');
      const size = await icon.evaluate(e => ({ width: e.getBoundingClientRect().width, font: parseFloat(getComputedStyle(e).fontSize) }));
      expect(size.width).toBeGreaterThanOrEqual(size.font);
    });
    test(`G57 dependency warning layout ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'channel-pipeline', theme, {
        '/api/channel-pipeline/rules': { rules: [] }, '/api/channel-pipeline/executions': { executions: [], total: 0 },
        '/api/channel-pipeline/rules/analyze-body': { rules: [], summary: { info: 0, warning: 0, error: 0 } },
      });
      await page.getByRole('button', { name: 'Create rule', exact: true }).click();
      await page.getByRole('button', { name: /Standard rule/ }).click();
      await page.getByRole('button', { name: 'Add action', exact: true }).click();
      await page.getByRole('combobox', { name: 'Action type' }).click();
      await page.getByRole('option', { name: /^Assign Logo/ }).click();
      const editor = page.locator('.action-editor');
      await editor.scrollIntoViewIfNeeded();
      await capture(page, info, 'G57');
      const warning = await editor.locator('.action-warning').boundingBox();
      const content = await editor.locator('.action-content').boundingBox();
      expect(warning!.y, 'Warning must follow, not squeeze, the assignment controls').toBeGreaterThan(content!.y);
      expect(warning!.x).toBeCloseTo(content!.x, 0);
      for (const action of ['Assign EPG', 'Assign Profile']) {
        await page.getByRole('combobox', { name: 'Action type' }).click();
        await page.getByRole('option', { name: new RegExp(`^${action}`) }).click();
        await editor.scrollIntoViewIfNeeded();
        await capture(page, info, `G57-${action}`);
        const warn = await editor.locator('.action-warning').boundingBox();
        const controls = await editor.locator('.action-content').boundingBox();
        expect.soft(warn!.x).toBeCloseTo(controls!.x, 0);
        expect.soft(warn!.y).toBeGreaterThan(controls!.y);
      }
      await page.getByRole('combobox', { name: 'Action type' }).click();
      await page.getByRole('option', { name: /^Create Channel/ }).click();
      await page.getByRole('button', { name: 'Add action', exact: true }).click();
      const assignment = page.locator('.action-editor').last();
      for (const action of ['Assign Logo', 'Assign EPG', 'Assign Profile']) {
        await assignment.getByRole('combobox', { name: 'Action type' }).click();
        await page.getByRole('option', { name: new RegExp(`^${action}`) }).click();
        await expect(assignment.locator('.action-warning')).toHaveCount(0);
        await assignment.scrollIntoViewIfNeeded();
        await capture(page, info, `G57-no-warning-${action}`);
        for (const field of await assignment.locator('input, .custom-select-trigger').all()) {
          await expect.soft(field).toBeInViewport();
        }
      }
    });
    test(`G51 low annotation popover ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const profile = { id: 1, name: 'Synthetic sports', enabled: true, name_source: 'channel', stream_index: 1,
        title_pattern: null, time_pattern: null, date_pattern: null, substitution_pairs: [], title_template: '{title}',
        description_template: '', event_timezone: 'UTC', output_timezone: null, program_duration: 180, categories: 'Sports',
        tvg_id_template: 'ecm-{channel_number}', include_date_tag: true, include_live_tag: true, include_new_tag: false,
        pattern_builder_examples: JSON.stringify({ examples: [{ id: 'synthetic', text: 'Sports Alpha vs Beta - Premier League - Live coverage from the regional sports network - Match',
          annotations: [{ start: 7, end: 12, variableName: 'team1', variableType: 'text' }] }], activeExampleIndex: 0 }),
        pattern_variants: [], channel_group_ids: [], group_count: 0 };
      await openSynthetic(page, 'epg-manager', theme, {
        '/api/epg/sources': [], '/api/dummy-epg/profiles': [profile], '/api/dummy-epg/profiles/1': profile,
        '/api/dummy-epg/profiles/1/channels': [], '/api/channel-groups': [],
      });
      await page.getByRole('button', { name: 'Edit profile', exact: true }).click();
      await capture(page, info, 'G51-open');
      await page.locator('.pb-canvas').evaluate(e => e.scrollIntoView({ block: 'end' }));
      await capture(page, info, 'G51-anchor');
      await page.locator('.pb-canvas-annotation').click();
      const popover = page.locator('.pb-popover');
      await expect(popover).toBeVisible();
      await capture(page, info, 'G51');
      const box = await popover.boundingBox();
      expect.soft(box!.y + box!.height).toBeLessThanOrEqual(viewport.height - 8);
      expect.soft(box!.x + box!.width).toBeLessThanOrEqual(viewport.width - 8);
      await expect(popover.getByRole('button', { name: /Update/ })).toBeInViewport();
      await popover.getByRole('button', { name: 'Custom', exact: true }).click();
      await popover.getByPlaceholder('e.g. [A-Z]{2,4}').fill('[A-Z]+');
      await page.setViewportSize({ width: 1280, height: 720 });
      await capture(page, info, 'G51-custom-resized');
      for (const button of await popover.locator('.pb-popover-actions button').all()) await expect.soft(button).toBeInViewport();
      await popover.getByRole('button', { name: 'Update', exact: true }).click();
      await expect(popover).not.toBeVisible();
      await page.locator('.pb-canvas-annotation').click();
      await popover.getByRole('button', { name: 'Delete', exact: true }).click();
      await expect(page.locator('.pb-canvas-annotation')).toHaveCount(0);
      const canvas = page.locator('.pb-canvas');
      await canvas.evaluate(element => {
        const node = element.querySelector('.pb-canvas-text')!.firstChild!;
        const length = node.textContent!.length;
        const range = document.createRange(); range.setStart(node, length - 5); range.setEnd(node, length);
        const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
      });
      await canvas.dispatchEvent('mouseup');
      await expect(popover.getByRole('button', { name: 'Add Variable', exact: true })).toBeInViewport();
      await capture(page, info, 'G51-add');
      expect.soft((await popover.boundingBox())!.x + (await popover.boundingBox())!.width).toBeLessThanOrEqual(1272);
      await popover.getByPlaceholder('e.g. team1, league, hour').fill('event');
      await popover.getByRole('button', { name: 'Add Variable', exact: true }).click();
      await expect(page.locator('.pb-canvas-annotation')).toHaveCount(1);
      await page.locator('.pb-canvas-annotation').click();
      await capture(page, info, 'G51-right-edit');
      expect.soft((await popover.boundingBox())!.x + (await popover.boundingBox())!.width).toBeLessThanOrEqual(1272);
      await popover.getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect(popover).not.toBeVisible();
    });
    test(`G23 bandwidth headings ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'stats', theme, {
        '/api/stats/channels': { channels: [], count: 0 }, '/api/stats/activity': { events: [], count: 0 },
        '/api/stats/unique-viewers': { period_days: 7, total_unique_viewers: 2, today_unique_viewers: 2,
          total_connections: 12, avg_watch_seconds: 3600, top_viewers: [], daily_unique: [] },
        '/api/stats/channel-bandwidth': [{ channel_id: 'synthetic', channel_name: 'Synthetic news',
          total_bytes: 9000000000, total_connections: 12, total_watch_seconds: 7200, peak_clients: 2 }],
        '/api/stats/unique-viewers-by-channel': [],
      });
      const header = page.locator('.enhanced-stats-panel .list-header');
      await page.getByRole('button', { name: 'Channel Bandwidth', exact: true }).click();
      for (const width of [viewport.width, 1280]) {
      await page.setViewportSize({ width, height: viewport.height });
      await header.scrollIntoViewIfNeeded();
      await capture(page, info, `G23-${width}`);
      for (const heading of await header.locator('span').all()) {
        const geometry = await heading.evaluate(e => {
          const range = document.createRange(); range.selectNodeContents(e);
          return { text: range.getBoundingClientRect().toJSON(), box: e.getBoundingClientRect().toJSON() };
        });
        expect.soft(geometry.text.left).toBeGreaterThanOrEqual(geometry.box.left - 1);
        expect.soft(geometry.text.right).toBeLessThanOrEqual(geometry.box.right + 1);
      }
      }
    });
    test(`G30 migration preview roles ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'epg-manager', theme, {
        '/api/epg/sources': [1, 2].map(id => ({ id, name: `Synthetic guide ${id}`, source_type: 'xmltv',
          is_active: true, status: 'success', priority: id })), '/api/dummy-epg/profiles': [],
        '/api/epg/migration/preview': { target_source_id: 2, target_source_name: 'Synthetic guide 2',
          preview_token: 'synthetic', counts: { ready: 1 }, rows: [{ channel_id: 1, channel_name: 'Synthetic news',
            current_source_name: 'Synthetic guide 1', lcn: '101', target_name: 'Synthetic news', status: 'ready' }] },
      });
      await page.getByRole('button', { name: /Migrate Guides/ }).click();
      await page.getByLabel('Target EPG source', { exact: true }).selectOption('2');
      await page.getByRole('button', { name: 'Preview migration', exact: true }).click();
      await expect(page.getByLabel('Migration summary')).toBeVisible();
      await capture(page, info, 'G30');
      await expect.soft(page.locator('.guide-migration-summary')).toHaveCSS('font-size', '13px');
      await expect.soft(page.locator('.guide-migration-table-wrap td').first()).toHaveCSS('font-size', '13px');
      await expect.soft(page.locator('.guide-migration-confirm')).toHaveCSS('font-size', '13px');
      await page.locator('.guide-migration-confirm input').check();
      await expect(page.locator('.guide-migration-confirm input')).toBeChecked();
    });
    test(`G45 probe item roles ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/maintenance', theme, {
        '/api/stream-stats/probe/history': [{ timestamp: '2026-09-07T00:00:00Z', end_timestamp: '2026-09-07T00:01:00Z',
          duration_seconds: 60, total: 3, status: 'completed', success_count: 2, failed_count: 1, skipped_count: 0,
          success_streams: [], failed_streams: [{ id: 981, name: 'Synthetic failed stream', error: 'Synthetic unavailable' }], skipped_streams: [],
          black_screen_count: 1, black_screen_streams: [{ id: 982, name: 'Synthetic black screen stream' }],
          low_fps_count: 0, low_fps_streams: [], low_bitrate_count: 1, low_bitrate_streams: [{ id: 980, name: 'Synthetic low bitrate stream' }] }],
      });
      for (const bucket of ['low bitrate', 'failed', 'black screen']) {
      await page.getByTitle(`View ${bucket} streams`, { exact: true }).click();
      const name = page.locator('.probe-result-item-name');
      await expect(name).toBeVisible();
      await capture(page, info, `G45-${bucket}`);
      await expect.soft(name).toHaveCSS('font-size', '13px');
      await expect(name).toHaveCSS('font-weight', '600');
      await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
      }
    });
    test(`G44 compound checkbox geometry ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/normalization', theme, {
        '/api/tags/groups': { groups: [] },
        '/api/normalization/rules': { groups: [{ id: 1, name: 'Synthetic rules', enabled: true, priority: 0, rules: [] }] },
      });
      await page.locator('.norm-engine-group-header').first().click();
      await page.getByRole('button', { name: /Add Rule/ }).first().click();
      await page.getByRole('button', { name: 'Compound (AND/OR/NOT)', exact: true }).click();
      const inputs = page.locator('.norm-engine-condition-checkbox input');
      await expect(inputs).toHaveCount(2);
      await capture(page, info, 'G44');
      for (const input of await inputs.all()) {
        expect.soft(await input.evaluate(e => e.getBoundingClientRect().width)).toBe(16);
        expect.soft(await input.evaluate(e => e.getBoundingClientRect().height)).toBe(16);
        await input.check();
        await input.focus();
        await expect(input).toBeChecked();
        await expect(input).toBeFocused();
      }
      await capture(page, info, 'G44-checked');
      await page.locator('.norm-engine-logic-select .custom-select-trigger').click();
      await page.getByRole('option', { name: 'OR (any must match)', exact: true }).click();
      await page.getByRole('checkbox', { name: /Execute alternate action/ }).check();
      await inputs.first().scrollIntoViewIfNeeded();
      await capture(page, info, 'G44-OR-Else');
      for (const input of await inputs.all()) {
        await expect(input).toHaveCSS('width', '16px');
        await input.uncheck();
        await input.focus();
        await expect(input).toBeFocused();
      }
    });
    test(`G26 speed foreground contrast ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'stats', theme, {
          '/api/stats/channels': { channels: [1.02, 0.9, 0.5].map((speed, i) => ({ channel_id: `synthetic-${i}`, channel_name: `Synthetic news ${i}`, channel_number: 101 + i,
          state: 'active', uptime: 7200, client_count: 0, clients: [], ffmpeg_speed: speed,
          avg_bitrate: '6.40 Mbps', avg_bitrate_kbps: 6400, ffmpeg_fps: 50, source_fps: 50,
          video_codec: 'h264', audio_codec: 'aac', resolution: '1920x1080', total_bytes: 2340000000 })), count: 3 },
        '/api/stats/activity': { events: [], count: 0, total: 0 }, '/api/stats/top-watched': [],
        '/api/epg/grid': [], '/api/epg/data': [],
      });
      const speed = page.locator('.stat-value.speed-good');
      await expect(speed).toBeVisible();
      await capture(page, info, 'G26');
      expect(await contrast(speed)).toBeGreaterThanOrEqual(4.5);
      const states = page.locator('.stat-value[class*="speed-"]');
      await expect(states).toHaveCount(3);
      for (const value of await states.all()) expect.soft(await contrast(value)).toBeGreaterThanOrEqual(4.5);
    });
    test(`G27 G28 Guide boundary geometry ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      const hour = new Date('2026-09-07T12:00:00Z');
      await page.clock.setFixedTime(hour);
      await openSynthetic(page, 'guide', theme, {
        '/api/channels': { count: 1, total: 1, next: null, previous: null, results: [
          { id: 1, channel_number: 101, name: 'Synthetic news', channel_group_id: 1, tvg_id: 'synthetic.1',
            epg_data_id: null, streams: [], uuid: '00000000-0000-4000-8000-000000000001', logo_id: null },
        ] },
        '/api/channel-groups': [{ id: 1, name: 'Synthetic', channel_count: 1 }],
        '/api/channel-profiles': [], '/api/channels/logos': { count: 0, results: [], next: null },
        '/api/epg/grid': [
          { id: 1, start_time: '2026-09-07T11:00:00Z', end_time: '2026-09-07T12:02:00Z', title: 'Short news remainder', tvg_id: 'synthetic.1' },
          { id: 2, start_time: '2026-09-07T12:02:00Z', end_time: '2026-09-07T13:02:00Z', title: 'Wildlife documentary', tvg_id: 'synthetic.1' },
        ],
      });
      await page.getByRole('button', { name: 'Collapse navigation', exact: true }).click();
      const blocks = page.locator('.guide-row .program-block');
      await expect(blocks).toHaveCount(2);
      await capture(page, info, 'G27-G28');
      const first = await blocks.nth(0).boundingBox();
      const second = await blocks.nth(1).boundingBox();
      expect.soft(first!.x + first!.width, 'G27 short remainder cannot overpaint successor').toBeLessThanOrEqual(second!.x);
      const channel = await page.locator('.guide-row .channel-info').boundingBox();
      const marker = await page.locator('.now-indicator').boundingBox();
      expect(marker!.x, 'G28 fixed-hour marker begins at programme boundary').toBeCloseTo(channel!.x + channel!.width, 0);
    });
    test(`G18 G19 task editor roles ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      for (const taskId of ['cleanup', 'journal_noise_purge']) {
      await openSynthetic(page, 'settings/scheduled-tasks', theme, {
        '/api/tasks': { tasks: [{ task_id: taskId, task_name: taskId, task_description: 'Synthetic cleanup',
          status: 'completed', enabled: true, effective_enabled: true, config: {}, schedules: [],
          schedule: { schedule_type: 'manual', interval_seconds: 0, timezone: 'UTC' },
          progress: { total: 10, current: 10, percentage: 100, success_count: 10, failed_count: 0 },
          last_run: '2026-09-07T00:00:00Z', next_run: null, send_alerts: true, show_notifications: true }] },
        [`/api/tasks/${taskId}/schedules`]: { schedules: [] },
        [`/api/tasks/${taskId}/parameter-schema`]: { task_id: taskId, parameters: [] },
        '/api/alert-methods': [],
      });
      const completed = page.getByTestId(`task-card-${taskId}`).getByText('Completed', { exact: true });
      await expect(completed).toBeVisible();
      await capture(page, info, 'G19');
      expect.soft(await contrast(completed), 'G19 Completed contrast on actual surface').toBeGreaterThanOrEqual(4.5);
      await page.getByTestId(`task-card-${taskId}`).getByRole('button', { name: /Edit/ }).click();
      const label = page.locator('.task-editor-modal .config-checkbox').first();
      await label.scrollIntoViewIfNeeded();
      await capture(page, info, `G18-${taskId}`);
      await expect.soft(label).toHaveCSS('font-size', '13px');
      await expect.soft(label).toHaveCSS('font-weight', '500');
      await expect(page.locator('.task-editor-modal .alert-config-header .section-label')).toHaveCount(2);
      for (const section of await page.locator('.task-editor-modal .alert-config-header .section-label').all()) {
        await section.scrollIntoViewIfNeeded();
        await capture(page, info, `G18-${taskId}-alert-${await section.textContent()}`);
        await expect.soft(section).toHaveCSS('font-size', '15px');
        await expect.soft(section).toHaveCSS('font-weight', '600');
      }
      await expect(label.locator('input')).toHaveCSS('width', '16px');
      await page.goto('about:blank');
      }
    });
    test(`G13 server group roles ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'm3u-manager', theme, {
        '/api/providers': [], '/api/m3u/server-groups': [{ id: 1, name: 'Primary television providers' }],
      });
      await page.getByRole('button', { name: 'M3U setup actions' }).click();
      await page.getByRole('menuitem', { name: 'Server Groups', exact: true }).click();
      await capture(page, info, 'G13');
      await expect.soft(page.locator('.server-group-name')).toHaveCSS('font-size', '13px');
      await expect.soft(page.locator('.server-group-name')).toHaveCSS('font-weight', '600');
      await page.getByRole('button', { name: /^Rename / }).click();
      await page.locator('.server-group-edit-input').fill('Synthetic rename');
      await expect(page.locator('.server-group-edit-input')).toBeFocused();
    });
    for (const route of ['m3u-manager', 'epg-manager', 'logo-manager']) {
      test(`G14 empty action icons ${route} ${theme} ${viewport.width}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        await openSynthetic(page, route, theme, {
          '/api/providers': [], '/api/m3u/server-groups': [], '/api/epg/sources': [], '/api/dummy-epg/profiles': [],
          '/api/channels/logos': { count: 0, next: null, previous: null, results: [] },
        });
        const empty = page.locator('.empty-state').first();
        await expect(empty).toBeVisible();
        await capture(page, info, `G14-${route}`);
        await expect.soft(empty.locator(':scope > .material-icons')).toHaveCSS('font-size', '64px');
        await expect(empty.locator('button .material-icons').first()).toHaveCSS('font-size', '16px');
      });
    }
    test(`G31 dashboard roles ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'dashboard', theme, { '/api/providers': [{ id: 1, name: 'Synthetic' }],
        '/api/channels': { count: 1, results: [{ id: 1, name: 'Synthetic', streams: [] }], next: null },
        '/api/streams': { count: 1, results: [{ id: 1, name: 'Synthetic', url: '/synthetic' }], next: null },
        '/api/channel-groups': [],
        '/api/tasks': { tasks: [{ task_id: 'cleanup', enabled: true, status: 'completed' }] },
        '/api/m3u/changes/summary': { total_changes: 2, streams_added: 2, streams_removed: 0, since: '2026-09-07T00:00:00Z' },
        '/api/journal/stats': { total_entries: 2, by_category: { system: 2 }, date_range: { newest: '2026-09-07T00:00:00Z' } },
      });
      const value = page.locator('.dashboard-card-value').first();
      await expect(value).toBeVisible();
      await capture(page, info, 'G31');
      await expect.soft(value).toHaveCSS('font-size', '20px');
      await expect.soft(value).toHaveCSS('font-weight', '600');
      await expect(page.locator('.dashboard-card-link').first()).toHaveCSS('font-size', '13px');
      await expect(page.locator('.dashboard-card-value')).toHaveCount(6);
      for (const metric of await page.locator('.dashboard-card-value').all()) {
        await expect.soft(metric).toHaveCSS('font-size', '20px');
        await expect.soft(metric).toHaveCSS('font-weight', '600');
      }
    });
    test(`G12 logo delete chassis ${theme} ${viewport.width}`, async ({ page }, info) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'logo-manager', theme, {
        '/api/channels/logos': { count: 1, next: null, previous: null, results: [
          { id: 1, name: 'Synthetic logo', url: '/synthetic.png', channel_count: 2, is_used: true },
        ] },
      });
      await page.getByTitle('Delete', { exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Delete Logo' });
      await expect(dialog).toBeVisible();
      await capture(page, info, 'G12');
      const close = dialog.getByRole('button', { name: 'Close', exact: true });
      expect.soft(await close.evaluate(e => e.getBoundingClientRect().width)).toBe(32);
      expect.soft(await close.evaluate(e => e.getBoundingClientRect().height)).toBe(32);
      await expect.soft(page.locator('.delete-confirm-modal')).toHaveCSS('padding-top', '0px');
      await close.focus();
      await expect(close).toBeFocused();
      await close.click();
      await expect(dialog).not.toBeVisible();
      await page.goto('/#channel-manager');
      await expect(page.locator('.channel-manager-tab')).toBeVisible();
      await page.goto('/#logo-manager');
      await page.getByTitle('Delete', { exact: true }).click();
      await capture(page, info, 'G12-cross-route');
      await expect(close).toHaveCSS('width', '32px');
      await expect(page.locator('.delete-confirm-modal')).toHaveCSS('padding-top', '0px');
    });
    test(`G09 dummy toolbar ${theme} ${viewport.width}`, async ({ page }, info) => {
      for (const width of [1280, viewport.width]) {
        await page.setViewportSize({ width, height: viewport.height });
        await openSynthetic(page, 'epg-manager', theme, {
          '/api/epg/sources': [], '/api/dummy-epg/profiles': [{ id: 1, name: 'Synthetic sports', enabled: true, group_count: 0 }],
        });
        const header = page.locator('.dep-manager-header');
        await header.scrollIntoViewIfNeeded();
        await capture(page, info, `G09-${width}`);
        const right = await header.evaluate(e => e.getBoundingClientRect().right);
        for (const button of await header.locator('button').all()) {
          expect.soft(await button.evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(right + 1);
          await button.focus();
          await expect(button).toBeFocused();
        }
      }
    });
    test(`G39 ntfy field styling ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/email', theme, { '/api/alert-methods': [] });
      const form = page.locator('.ntfy-create-form');
      await form.scrollIntoViewIfNeeded();
      await form.getByLabel('Name', { exact: true }).fill('Synthetic target');
      await expect(form.locator('input')).toHaveCount(4);
      await capture(page, testInfo, 'G39');
      for (const control of await form.locator('input').all()) {
        await control.focus();
        await expect(control).toBeFocused();
        expect.soft(await control.evaluate(e => parseFloat(getComputedStyle(e).paddingLeft))).toBeGreaterThanOrEqual(8);
        expect.soft(await control.evaluate(e => parseFloat(getComputedStyle(e).borderRadius))).toBeGreaterThan(0);
      }
      expect(await form.locator('label').first().evaluate(e => getComputedStyle(e).fontSize)).toBe('13px');
    });

    test(`G16 G60 Points control geometry ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/channel-defaults', theme);
      await page.locator('.smart-sort-strategy-option').filter({ has: page.getByLabel('Points', { exact: true }) }).click();
      await page.getByRole('button', { name: /Add.*rule/i }).click();
      const row = page.getByTestId('smart-sort-point-rule').first();
      await row.scrollIntoViewIfNeeded();
      await expect(row.locator('.custom-select-trigger')).toHaveCount(2);
      await expect(row.locator('input[type="number"]')).toHaveCount(2);
      await capture(page, testInfo, 'G16-G60');
      for (const trigger of await row.locator('.custom-select-trigger').all()) {
        const boxes = await trigger.evaluate(e => ({ trigger: e.getBoundingClientRect().toJSON(), field: e.closest('.smart-sort-point-field')!.getBoundingClientRect().toJSON() }));
        expect.soft(boxes.trigger.right, 'G16 selector must stay inside its grid column').toBeLessThanOrEqual(boxes.field.right + 1);
      }
      for (const input of await row.locator('input[type="number"]').all()) {
        await input.fill('-12');
        await expect(input).toHaveValue('-12');
        expect.soft(await input.evaluate(e => parseFloat(getComputedStyle(e).paddingLeft)), 'G60 numeric padding').toBeGreaterThanOrEqual(8);
        expect.soft(await input.evaluate(e => e.getBoundingClientRect().height), 'G60 numeric height').toBeGreaterThanOrEqual(32);
      }
      await row.locator('.custom-select-trigger').first().focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('listbox')).toBeVisible();
      await capture(page, testInfo, 'G16-open-menu');
      await page.keyboard.press('Escape');
    });
    test(`G05 mapped editor roles ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/normalization?section=settings-normalization-section-mapped-channels', theme, {
        '/api/normalization/rules': { groups: [] },
        '/api/tags/groups': { groups: [] },
        '/api/normalization/mappings': { mappings: [{ id: 1, preferred_name: 'BBC One', aliases: ['BBC ONE HD'] }] },
      });
      await page.getByRole('button', { name: 'Add mapping', exact: true }).click();
      await capture(page, testInfo, 'G05');
      await page.setViewportSize({ width: 1280, height: 900 });
      await capture(page, testInfo, 'G05-settings-1280');
      await expect.soft(page.locator('.mapped-channels label').first()).toHaveCSS('font-size', '13px');
      await page.setViewportSize(viewport);
      await expect.soft(page.locator('.mapped-channels > p').first()).toHaveCSS('font-size', '13px');
      await expect.soft(page.locator('.mapped-channels label').first()).toHaveCSS('font-size', '13px');
      await page.getByLabel('Preferred name', { exact: true }).fill('Synthetic preferred');
      await page.route('**/api/normalization/mappings', route => route.request().method() === 'POST'
        ? route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic mapping validation error' }) })
        : route.fallback());
      await page.getByRole('button', { name: 'Save mapping', exact: true }).click();
      await expect(page.locator('.mapped-channels [role=alert]')).toBeVisible();
      await capture(page, testInfo, 'G05-settings-validation');
      await expect(page.locator('.mapped-channels [role=alert]')).toHaveCSS('font-size', '13px');
      await expect.soft(page.locator('.mapped-channels h3')).toHaveCSS('font-size', '13px');
      await expect.soft(page.locator('.mapped-channels h3')).toHaveCSS('font-weight', '600');
      await page.goto('about:blank');
      await openSynthetic(page, 'settings/normalization?section=settings-normalization-section-mapped-channels', theme, {
        '/api/normalization/rules': { groups: [] },
        '/api/tags/groups': { groups: [] },
        '/api/normalization/mappings': { mappings: [] },
      });
      await expect(page.getByText('No mappings defined.', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Add mapping', exact: true }).click();
      await capture(page, testInfo, 'G05-empty-list-editor');
      await expect(page.getByText('No mappings defined.', { exact: true })).toHaveCSS('font-size', '13px');
    });

    test(`G40 team alias input roles ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/channel-pipeline', theme, {
        '/api/event-sync/team-aliases': { groups: [{ terms: ['Man Utd', 'Manchester United'], note: 'Synthetic evidence' }] },
      });
      const input = page.getByPlaceholder('Add a spelling, e.g. MUFC');
      await input.scrollIntoViewIfNeeded();
      await input.fill('MUFC');
      expect.soft(await page.locator('.settings-group > .form-description').evaluate(e => getComputedStyle(e).fontSize)).toBe('13px');
      await capture(page, testInfo, 'G40');
      for (const control of [input, page.locator('.team-alias-note-input')]) {
        await control.focus();
        await expect(control).toBeFocused();
        expect.soft(await control.evaluate(e => parseFloat(getComputedStyle(e).paddingLeft))).toBeGreaterThanOrEqual(8);
        expect.soft(await control.evaluate(e => parseFloat(getComputedStyle(e).borderRadius))).toBeGreaterThan(0);
      }
    });

    test(`G41 trusted networks textarea ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/integrations', theme);
      const input = page.getByTestId('trusted-media-networks-input');
      await input.scrollIntoViewIfNeeded();
      await input.fill('192.0.2.0/24');
      await input.focus();
      await expect(input).toBeFocused();
      await expect(input).toHaveCSS('resize', 'both');
      await capture(page, testInfo, 'G41');
      expect.soft(await input.evaluate(e => parseFloat(getComputedStyle(e).paddingLeft))).toBeGreaterThanOrEqual(8);
      expect.soft(await input.evaluate(e => parseFloat(getComputedStyle(e).borderRadius))).toBeGreaterThan(0);
    });
    test(`G38 opaque Tag Group surface ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await openSynthetic(page, 'settings/tag-engine', theme, {
          '/api/tags/groups': { groups: Array.from({ length: 12 }, (_, i) => ({
            id: i + 1, name: `Synthetic group ${i + 1}`, description: 'Background opacity fixture',
            is_builtin: false, tag_count: 2,
          })) },
      });
      await page.getByRole('button', { name: /New Group/ }).click();
      const surface = page.locator('.tag-engine-group-modal .modal-content');
      await expect(surface).toBeVisible();
      await capture(page, testInfo, 'G38');
      const alpha = await surface.evaluate(element => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 1;
        const context = canvas.getContext('2d')!;
        context.fillStyle = getComputedStyle(element).backgroundColor;
        context.fillRect(0, 0, 1, 1);
        return context.getImageData(0, 0, 1, 1).data[3];
      });
      expect(alpha, 'Dialog foreground must not expose background rows').toBe(255);
    });
  }
}
