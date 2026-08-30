/**
 * Rendered-browser guard for Edit Mode's channel-numbering safeguards (beads
 * `enhancedchannelmanager-vdxbx` and `enhancedchannelmanager-ic884.2`).
 *
 * WHY IT HAS TO BE A BROWSER. The unit suites prove each layer on its own:
 * `src/utils/channelNumberPlan.test.ts` proves the plan and its verdicts,
 * `src/components/ChannelsPane.duplicateNumber.test.tsx` proves the warning's
 * markup, `src/hooks/useEditMode.numberingPreflight.test.ts` proves the refusal
 * with `api.bulkCommit` spied. None of them crosses the seam the feature lives
 * on: a real double-click in a real channel list, a real dialog over the real
 * workspace, a real acknowledgement travelling into the real staged ledger, and
 * a real Apply that must make NO HTTP request at all. Green suites on both
 * sides of an uncrossed seam verify the layers, not the feature.
 *
 * THE ANTI-VACUITY CONTROL IS THE POINT OF ARM 2. "Apply sent no bulk-commit"
 * is satisfied by an Apply button that does nothing, by a dialog that never
 * opened, and by a page that failed to load. So arm 2 first proves the same
 * page CAN commit — it applies a clean plan and watches the request go — and
 * only then stages the conflicting one and proves the request does not.
 *
 * NO BACKEND, NO LIVE INSTANCE — STRUCTURALLY, exactly as
 * `e2e/edit-mode-session-restore.spec.ts` refuses for the same reason.
 * `E2E_EXACT_BUILD` + `E2E_START_SERVER` build the checked-out source and serve
 * it on the isolated preview port 127.0.0.1:4173, supplying the base URL
 * themselves, so this spec cannot be aimed at an operator's live ECM.
 */
import { expect, test } from './fixtures/base';
import type { Page } from '@playwright/test';

if (process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true') {
  throw new Error(
    'edit-mode-numbering-guards requires E2E_EXACT_BUILD=true and E2E_START_SERVER=true. ' +
      'It drives Edit Mode staging and Apply, so it runs only against the isolated preview ' +
      'build it serves itself — never a live ECM instance.',
  );
}

const channels = [
  { id: 1, name: 'Alpha', channel_number: 101, channel_group_id: 1 },
  { id: 2, name: 'Bravo', channel_number: 102, channel_group_id: 1 },
  // In a DIFFERENT group, and that is the point of arm 2: Sort & Renumber
  // renumbers a group without looking outside it, so the collision it creates
  // is invisible to every per-operation check.
  { id: 3, name: 'Delta', channel_number: 201, channel_group_id: 2 },
].map((channel) => ({
  ...channel,
  tvg_id: null,
  tvc_guide_stationid: null,
  epg_data_id: null,
  streams: [],
  stream_profile_id: null,
  uuid: `0000000${channel.id}-0000-4000-8000-000000000000`,
  logo_id: null,
  auto_created: false,
  auto_created_by: null,
  auto_created_by_name: null,
}));

const paginated = (results: unknown[]) => ({ count: results.length, next: null, previous: null, results });

const STUBS: ReadonlyArray<readonly [RegExp, unknown]> = [
  [/\/api\/auth\/status(?:\/|\?|$)/, { require_auth: false, setup_complete: true, dispatcharr_enabled: false }],
  [/\/api\/auth\/setup-required(?:\/|\?|$)/, { required: false }],
  [/\/api\/settings(?:\/|\?|$)/, { configured: true, url: 'http://synthetic.invalid', default_channel_profile_ids: [] }],
  [/\/api\/channels\/logos(?:\/|\?|$)/, paginated([])],
  [/\/api\/channels(?:\/|\?|$)/, paginated(channels)],
  [/\/api\/channel-groups(?:\/|\?|$)/, [
    { id: 1, name: 'Entertainment', channel_count: 2, is_auto_sync: false },
    { id: 2, name: 'News', channel_count: 1, is_auto_sync: false },
  ]],
  [/\/api\/channel-profiles(?:\/|\?|$)/, [{ id: 1, name: 'Default', channels: [] }]],
  [/\/api\/channel-merges(?:\/|\?|$)/, { results: [], count: 0 }],
  [/\/api\/streams\/stale-ids(?:\/|\?|$)/, { stale_stream_ids: [] }],
  [/\/api\/streams(?:\/|\?|$)/, paginated([])],
  [/\/api\/stream-groups(?:\/|\?|$)/, []],
  [/\/api\/stream-profiles(?:\/|\?|$)/, []],
  [/\/api\/notifications(?:\/|\?|$)/, { notifications: [], unread_count: 0, results: [] }],
  [/\/api\/logos(?:\/|\?|$)/, paginated([])],
  [/\/api\/epg\//, []],
  [/\/api\/providers(?:\/|\?|$)/, [{
    id: 1, name: 'Provider A', enabled: true, channel_group_id: 1, auto_sync: false,
    custom_properties: null, stream_count: 0, auto_channel_sync: false,
  }]],
];

/** Every bulk-commit POST the page made, in order, with its body. */
interface CommitRecorder {
  posts: unknown[];
  /**
   * What `GET /api/channels` answers with, RIGHT NOW.
   *
   * Mutable on purpose (bead `enhancedchannelmanager-ic884.4`): arm 4 changes
   * it after Edit Mode has captured its baseline, which is the only way to
   * reproduce another operator moving a channel under a live session — and the
   * only way to prove that Apply reads the server rather than trusting the list
   * it loaded at the start.
   */
  lineup: Record<string, unknown>[];
}

async function stubBackend(page: Page): Promise<CommitRecorder> {
  const recorder: CommitRecorder = { posts: [], lineup: channels.map((channel) => ({ ...channel })) };
  await page.route(/\/api\//, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (/\/api\/auth\/me(?:\/|\?|$)/.test(path)) return json({ detail: 'No session on the preview build' }, 401);
    if (/\/api\/(session-start|auth\/refresh)(?:\/|\?|$)/.test(path)) return route.fulfill({ status: 204, body: '' });

    if (/\/api\/channels\/bulk-commit/.test(path) && request.method() === 'POST') {
      recorder.posts.push(request.postDataJSON());
      return json({
        success: true,
        operationsApplied: 1,
        operationsFailed: 0,
        errors: [],
        tempIdMap: {},
        groupIdMap: {},
      });
    }

    // Served from the mutable copy rather than from STUBS, so a test can move a
    // channel mid-session.
    if (/\/api\/channels(?:\/|\?|$)/.test(path) && !/\/api\/channels\//.test(path)) {
      return json(paginated(recorder.lineup));
    }

    for (const [pattern, body] of STUBS) if (pattern.test(path)) return json(body);
    return json([]);
  });
  return recorder;
}

async function openChannelManagerInEditMode(page: Page): Promise<CommitRecorder> {
  const recorder = await stubBackend(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.tab-navigation', { state: 'visible', timeout: 30_000 });
  await page.evaluate(() => { window.location.hash = '#channel-manager'; });
  await page.waitForSelector('.channels-pane', { timeout: 60_000 });
  await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
  await page.locator('.enter-edit-mode-btn').click();
  await page.waitForSelector('.edit-mode-done-btn', { timeout: 15_000 });
  await page.locator('.channels-pane .group-header').filter({ hasText: 'Entertainment' }).first().click();
  await page.waitForSelector('.channels-pane .channel-item', { timeout: 15_000 });
  return recorder;
}

/** Double-click a row's number, type `value`, and commit the field with Enter. */
async function editNumber(page: Page, channelName: string, value: string): Promise<void> {
  const row = page.locator('.channels-pane .channel-item').filter({ hasText: channelName }).first();
  await row.locator('.channel-number').dblclick();
  const input = page.locator('.channel-number-input');
  await expect(input).toBeVisible();
  await input.fill(value);
  await input.press('Enter');
}

// ============================= ARM 1: the warning, and what confirming records

test('staging a number another channel holds warns, and confirming records the choice', async ({ page }) => {
  test.setTimeout(3 * 60 * 1000);
  await openChannelManagerInEditMode(page);

  await editNumber(page, 'Alpha', '102');

  const confirm = page.locator('[data-testid="channel-number-confirm"]');
  await expect(confirm, 'staging onto an occupied number must warn before it stages').toBeVisible();
  // Anti-vacuity: the real dialog, over the real workspace, naming the real
  // conflicting channel rather than merely saying something is wrong.
  await expect(page.locator('.channels-pane')).toHaveCount(1);
  await expect(confirm).toContainText('102');
  await expect(confirm).toContainText('Bravo');

  // Nothing is staged while the question is open.
  await expect(page.locator('.edit-mode-changes')).toHaveCount(0);

  await confirm.getByRole('button', { name: /use it anyway/i }).click();
  await expect(confirm).toHaveCount(0);
  await expect(page.locator('.edit-mode-changes'), 'confirming stages exactly one change')
    .toHaveText('1 change');

  // The acknowledgement is on the OPERATION, which is what makes it survive the
  // ledger and stop the preflight re-asking at Apply.
  const stored = await page.evaluate(() =>
    window.sessionStorage.getItem('ecm.channelManager.stagedLedger'));
  expect(stored, 'the staged operation must be persisted').not.toBeNull();
  const operations = JSON.parse(stored!).operations as { apiCall: Record<string, unknown> }[];
  expect(operations.map((op) => (op.apiCall.acknowledgedDuplicate as { number?: number } | undefined)?.number))
    .toContain(102);
});

// ============ ARM 2: Apply refuses a bad final state before it mutates anything

test('Apply stops before any request when the combined final state collides', async ({ page }) => {
  test.setTimeout(3 * 60 * 1000);
  const recorder = await openChannelManagerInEditMode(page);

  // Sort & Renumber "Entertainment" from 201. It renumbers its own group and
  // asks nothing about the rest of the lineup — deliberate, approved behaviour
  // this work leaves alone. Delta sits at 201 in another group, so the plan's
  // FINAL state puts two channels on 201 while every operation in it is
  // individually legal. That is the collision no per-operation check can see.
  const header = page.locator('.channels-pane .group-header').filter({ hasText: 'Entertainment' }).first();
  await header.locator('.group-menu-btn').click();
  await page.getByRole('button', { name: /Sort & Renumber/ }).click();
  const dialog = page.locator('.sort-renumber-dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Starting Channel Number').fill('201');
  await dialog.getByRole('button', { name: /^Sort & Renumber$/ }).click();

  // Staging is NOT what stops it: the operations go in unremarked, exactly as
  // they did before this work.
  await expect(page.locator('.edit-mode-changes')).toBeVisible();

  await page.locator('.edit-mode-done-btn').click();
  await page.getByRole('button', { name: /apply all/i }).click();

  const failure = page.locator('.edit-mode-dialog-commit-failure-intro');
  await expect(failure, 'the operator must be told why nothing was applied').toBeVisible({ timeout: 20_000 });
  await expect(failure, 'and told that nothing landed, not that some of it did')
    .toContainText('0 operations applied');
  const detail = page.locator('.edit-mode-dialog-commit-failure-list li').first();
  await expect(detail, 'the number is what makes it actionable').toContainText('201');
  await expect(detail, 'and so are the channels that would share it').toContainText('Delta');

  expect(
    recorder.posts.length,
    'a plan refused by preflight must not reach the server at all',
  ).toBe(0);

  // The staged work survives, so the operator can fix it rather than redo it.
  await expect(page.locator('.edit-mode-done-btn')).toBeVisible();
});

// ================== ARM 3: the anti-vacuity control for arm 2's zero requests

test('a clean plan still reaches the server from the same page', async ({ page }) => {
  // Arm 2 asserts a count of zero, and zero is equally consistent with an Apply
  // button that does nothing, a dialog that never opened, and a page that never
  // loaded. This arm walks the identical path with a plan that has no conflict
  // and watches the request go, so arm 2's zero means what it says.
  test.setTimeout(3 * 60 * 1000);
  const recorder = await openChannelManagerInEditMode(page);

  const header = page.locator('.channels-pane .group-header').filter({ hasText: 'Entertainment' }).first();
  await header.locator('.group-menu-btn').click();
  await page.getByRole('button', { name: /Sort & Renumber/ }).click();
  const dialog = page.locator('.sort-renumber-dialog');
  await expect(dialog).toBeVisible();
  // 301 collides with nothing; only the starting number differs from arm 2.
  await dialog.getByLabel('Starting Channel Number').fill('301');
  await dialog.getByRole('button', { name: /^Sort & Renumber$/ }).click();

  await expect(page.locator('.edit-mode-changes')).toBeVisible();
  await page.locator('.edit-mode-done-btn').click();
  await page.getByRole('button', { name: /apply all/i }).click();

  await expect
    .poll(() => recorder.posts.length, { timeout: 20_000, message: 'a clean plan must reach the server' })
    .toBeGreaterThan(0);
});

// ===== ARM 4: Apply reads the server first, and will not write over a change
//              the operator has not been shown (bead …-ic884.4)

test('a number changed under the session holds Apply until the operator chooses', async ({ page }) => {
  // WHY THIS HAS TO BE A BROWSER TOO. The unit suites prove the detector
  // (`src/utils/channelNumberConcurrency.test.ts`) and the hook's refusal with
  // `api.bulkCommit` spied (`src/hooks/useEditMode.concurrentNumbering.test.ts`).
  // Neither crosses the seam: a real Apply, a real dialog rendered over the real
  // workspace, a real per-channel choice, and a real second Apply that carries
  // the operator's answer onto the wire. The server-side lineup MOVES between
  // Edit Mode being entered and Apply being pressed, which is the whole
  // scenario and cannot be staged from inside the page.
  test.setTimeout(3 * 60 * 1000);
  const recorder = await openChannelManagerInEditMode(page);

  await editNumber(page, 'Alpha', '150');
  await expect(page.locator('.edit-mode-changes')).toHaveText('1 change');

  // Somebody else moves Alpha while this session is staging.
  recorder.lineup = recorder.lineup.map((channel) =>
    channel.id === 1 ? { ...channel, channel_number: 199 } : channel);

  await page.locator('.edit-mode-done-btn').click();
  await page.getByRole('button', { name: /apply all/i }).click();

  const conflict = page.locator('.numbering-conflict-dialog');
  await expect(conflict, 'the operator must be shown the change before it is overwritten')
    .toBeVisible({ timeout: 20_000 });
  await expect(conflict, 'named, with the number it started on').toContainText('Alpha');
  await expect(conflict, 'the number somebody else put it on').toContainText('199');
  await expect(conflict, 'and the number this session would write').toContainText('150');

  expect(
    recorder.posts.length,
    'nothing may be written while the question is unanswered',
  ).toBe(0);

  // No option is pre-selected: a default "keep mine" is a silent overwrite
  // wearing a checkbox.
  const apply = conflict.getByRole('button', { name: /apply with these choices/i });
  await expect(apply).toBeDisabled();

  await conflict.getByRole('radio', { name: /use my number/i }).check();
  await expect(apply).toBeEnabled();
  await apply.click();

  await expect
    .poll(() => recorder.posts.length, {
      timeout: 20_000,
      message: 'answering the question must let the Apply through',
    })
    .toBeGreaterThan(0);

  // The choice reached the wire as an expectation of the value the operator
  // agreed to overwrite — not of the stale one this session started with.
  const sent = recorder.posts.flatMap((post) =>
    ((post as { operations?: Record<string, unknown>[] }).operations ?? []));
  const alpha = sent.find((operation) => operation.channelId === 1);
  expect(alpha, 'the reconciled edit must be the one that was sent').toBeTruthy();
  expect((alpha as { data: Record<string, unknown> }).data.channel_number).toBe(150);
  expect((alpha as { expectedNumber?: { number?: number } }).expectedNumber?.number).toBe(199);
});
