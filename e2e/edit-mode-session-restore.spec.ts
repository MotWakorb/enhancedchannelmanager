/**
 * Rendered-browser guard for Edit Mode's staged-ledger survival across a dead
 * session (epic enhancedchannelmanager-r93hq).
 *
 * WHY IT HAS TO BE A BROWSER. The unit suites prove each layer on its own —
 * `src/utils/stagedLedgerStorage.test.ts` proves the store and the staleness
 * plan, `src/hooks/useEditMode.sessionSurvival.test.ts` proves the rebuild,
 * `src/components/EditModeRestoreDialog.test.tsx` proves the offer's markup.
 * None of them crosses the seam this feature actually lives on: a REAL
 * `sessionStorage` surviving a REAL page load, read on the first render of the
 * real `App` inside the real `ProtectedRoute`, planned against the channel list
 * the app actually fetched, and rendered as a dialog over the real Channel
 * Manager. Green suites on both sides of an uncrossed seam verify the layers,
 * not the feature.
 *
 * TWO ARMS, AND THE SECOND IS THE ONE THAT MATTERS.
 *
 *   1. A ledger this operator left behind is OFFERED, with an account of what
 *      no longer applies, and restoring puts the survivors back into Edit Mode
 *      marked as restored.
 *   2. A ledger stamped with a DIFFERENT operator is never offered and is gone
 *      from the store by the time the app has painted. Two operators share a
 *      workstation; handing A's staged channel edits to B means B Applies them
 *      under B's credentials and the journal attributes every change to B.
 *
 * NO BACKEND, NO LIVE INSTANCE — STRUCTURALLY, exactly as
 * `e2e/edit-mode-immediacy-surfaces.spec.ts` refuses for the same reason.
 * `E2E_EXACT_BUILD` + `E2E_START_SERVER` build the checked-out source and serve
 * it on the isolated preview port 127.0.0.1:4173, supplying the base URL
 * themselves, so this spec cannot be aimed at an operator's live ECM. The stub
 * table below is deliberately its own rather than shared with the immediacy
 * spec: that one is tuned for merge/CSV/profile surfaces this spec never opens,
 * and coupling two guards through one fixture lets one spec's needs silently
 * change the other's inputs.
 */
import { expect, test } from './fixtures/base';
import type { Page } from '@playwright/test';

if (process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true') {
  throw new Error(
    'edit-mode-session-restore requires E2E_EXACT_BUILD=true and E2E_START_SERVER=true. ' +
      'It drives Edit Mode staging, so it runs only against the isolated preview build it ' +
      'serves itself — never a live ECM instance.',
  );
}

/** Must match `STAGED_LEDGER_STORAGE_KEY` in `src/utils/stagedLedgerStorage.ts`. */
const LEDGER_KEY = 'ecm.channelManager.stagedLedger';
/** Must match `STAGED_LEDGER_FORMAT_VERSION`. */
const LEDGER_VERSION = 2;
/**
 * `operatorLedgerKey(null)`. The preview build answers /api/auth/me with 401
 * and reports `require_auth: false`, so the app renders with no identity —
 * which is its own key, never a wildcard.
 */
const THIS_OPERATOR = 'anonymous';

// ---------------------------------------------------------------- API stubs

const channels = [
  { id: 1, name: 'Alpha', channel_number: 101, channel_group_id: 1 },
  { id: 2, name: 'Bravo', channel_number: 102, channel_group_id: 1 },
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
  [/\/api\/channel-groups(?:\/|\?|$)/, [{ id: 1, name: 'Entertainment', channel_count: 2, is_auto_sync: false }]],
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

async function stubBackend(page: Page): Promise<void> {
  await page.route(/\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (/\/api\/auth\/me(?:\/|\?|$)/.test(path)) return json({ detail: 'No session on the preview build' }, 401);
    if (/\/api\/(session-start|auth\/refresh)(?:\/|\?|$)/.test(path)) return route.fulfill({ status: 204, body: '' });

    for (const [pattern, body] of STUBS) if (pattern.test(path)) return json(body);
    return json([]);
  });
}

// -------------------------------------------------------------- the ledger

/**
 * A staged ledger as a session that died would have left it: one operation
 * that still resolves (rename channel 1) and one that cannot (channel 404 was
 * deleted while the session was dead).
 */
function ledgerFor(operatorKey: string) {
  return {
    version: LEDGER_VERSION,
    operatorKey,
    savedAt: Date.now(),
    operations: [
      {
        id: 'op-live',
        timestamp: Date.now(),
        description: 'Rename "Alpha"',
        apiCall: { type: 'updateChannel', channelId: 1, data: { name: 'Alpha Restored' } },
        beforeSnapshot: [],
        afterSnapshot: [],
      },
      {
        id: 'op-stale',
        timestamp: Date.now(),
        description: 'Renumber "Vanished HD"',
        apiCall: { type: 'updateChannel', channelId: 404, data: { channel_number: 999 } },
        beforeSnapshot: [{ id: 404, channel_number: 404, name: 'Vanished HD', channel_group_id: 1, streams: [] }],
        afterSnapshot: [],
      },
    ],
    undoGroups: [['op-live'], ['op-stale']],
  };
}

/**
 * Seed the store BEFORE any application script runs.
 *
 * `useEditMode` reads the ledger in its first render — earlier than any
 * `page.evaluate` after `goto` could write it — which is exactly the ordering
 * this arm needs to exercise.
 */
async function seedLedger(page: Page, operatorKey: string): Promise<void> {
  await page.addInitScript(
    ([key, record]) => { window.sessionStorage.setItem(key as string, JSON.stringify(record)); },
    [LEDGER_KEY, ledgerFor(operatorKey)] as const,
  );
}

async function openChannelManager(page: Page): Promise<void> {
  await stubBackend(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.tab-navigation', { state: 'visible', timeout: 30_000 });
  await page.evaluate(() => { window.location.hash = '#channel-manager'; });
  await page.waitForSelector('.channels-pane', { timeout: 60_000 });
  await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
}

// ================================== ARM 1: the operator's own ledger comes back

test('a staged ledger left by a dead session is offered, accounted for, and restored', async ({ page }) => {
  test.setTimeout(3 * 60 * 1000);
  await seedLedger(page, THIS_OPERATOR);
  await openChannelManager(page);

  const dialog = page.locator('[data-testid="edit-mode-restore-dialog"]');
  await expect(dialog, 'the offer must appear once the channel list has loaded').toBeVisible({ timeout: 30_000 });

  // -- anti-vacuity: the dialog is the real one, over the real workspace ----
  await expect(page.locator('.channels-pane')).toHaveCount(1);
  await expect(dialog).toContainText(/previous session/i);

  // -- the account of what could NOT be restored ---------------------------
  const dropped = page.locator('[data-testid="edit-mode-restore-dropped"] li');
  await expect(dropped, 'exactly one of the two staged operations is stale').toHaveCount(1);
  await expect(dropped.first()).toContainText('Renumber "Vanished HD"');
  await expect(dropped.first(), 'the account must name what moved, not just that something did')
    .toContainText('no longer exists');

  // -- and the offer is sized to the survivors, not to the ledger ----------
  const restore = page.getByRole('button', { name: /restore 1 change/i });
  await expect(restore).toBeEnabled();
  await restore.click();

  // -- Edit Mode comes back holding exactly the survivor --------------------
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('.edit-mode-done-btn'), 'restoring must enter Edit Mode').toBeVisible({ timeout: 15_000 });
  await expect(page.locator('.edit-mode-changes')).toHaveText('1 change');

  // -- and it is unmistakably RESTORED work, not work just made -------------
  const badge = page.locator('[data-testid="edit-mode-restored-badge"]');
  await expect(badge, 'a restored session must say so for as long as it lasts').toBeVisible();
  await expect(badge).toContainText('Restored');

  // -- the staged edit is visible in the working copy, as staged work is ----
  await page.locator('.channels-pane .group-header').filter({ hasText: 'Entertainment' }).first().click();
  await expect(
    page.locator('.channels-pane .channel-item').filter({ hasText: 'Alpha Restored' }),
    'the restored rename must show in the working copy the way any staged rename does',
  ).toHaveCount(1);

  // -- and the ledger is back in the store under this operator --------------
  const stored = await page.evaluate((key) => window.sessionStorage.getItem(key), LEDGER_KEY);
  expect(stored, 'a restored session persists again, so a second expiry does not lose it').not.toBeNull();
  expect(JSON.parse(stored!).operations).toHaveLength(1);
});

// ============================ ARM 2: somebody else's ledger is never offered

test('a ledger staged by a different operator is never offered and is destroyed on sight', async ({ page }) => {
  test.setTimeout(3 * 60 * 1000);
  await seedLedger(page, 'local#7'); // not this session's operator
  await openChannelManager(page);

  // Give the app the same window arm 1 needed to raise the dialog, so this is
  // an absence rather than a race.
  await page.waitForTimeout(2_000);

  await expect(
    page.locator('[data-testid="edit-mode-restore-dialog"]'),
    'another operator\'s staged work must never be offered on this session',
  ).toHaveCount(0);
  await expect(page.locator('.edit-mode-done-btn'), 'and it must not be silently restored either').toHaveCount(0);
  await expect(page.locator('.enter-edit-mode-btn'), 'the workspace is otherwise normal').toBeVisible();

  // Withheld is not enough. Left in the tab, it is one code change or one
  // shared workstation away from being applied under the wrong credentials.
  const stored = await page.evaluate((key) => window.sessionStorage.getItem(key), LEDGER_KEY);
  expect(stored, 'a ledger this session refuses is a ledger it must not keep').toBeNull();
});
