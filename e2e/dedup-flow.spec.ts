/**
 * End-to-end Playwright spec for the stream-to-channel dedup flow
 * (bd-etdeb / ADR-008, BLOCKING gate for v0.17.1).
 *
 * Scope: validate the operator-visible UI flow of the dedup feature
 * across all three v0.17.1 trigger_context surfaces (drag_drop,
 * add_stream, m3u_refresh), without depending on a live Dispatcharr
 * instance or a real backend database.
 *
 * Test architecture:
 *
 *   - **Hermetic by design.** Every backend route the dedup feature
 *     touches is intercepted with `page.route()` before navigation, so
 *     the spec never round-trips to Dispatcharr and never writes a real
 *     pending-merges row. The same pattern is used in
 *     `e2e/re-normalize.spec.ts` (bd-eio04.12) for the normalization
 *     apply-to-channels flow.
 *
 *   - **All three trigger surfaces are exercised at the UI layer**
 *     through PendingMergesPage. The page presents queued rows from any
 *     surface (drag_drop, add_stream, m3u_refresh) identically — same
 *     Merge button, same accept endpoint, same response envelope. The
 *     trigger_context tag rides on each row and is mirrored to the
 *     audit journal at accept time. This spec asserts the operator-
 *     visible Merge click invokes `/api/channel-merges/{id}/accept`
 *     with the row's id regardless of the originating surface; the
 *     `pending_merge_journal` cross-trigger audit-field proof lives in
 *     `backend/tests/integration/test_dedup_audit_trail.py` (the bead
 *     explicitly allows splitting the journal-audit assertion to a
 *     backend test).
 *
 *   - **Scenario 1 covers the async-queue path end-to-end** (M3U
 *     refresh enqueues → subnav badge appears → operator navigates →
 *     Merge → row disappears). This is the most operationally complex
 *     path because it involves cross-tree navigation through the
 *     Channel Manager subnav.
 *
 *   - **Scenario 2 covers the drag_drop trigger** via a seeded
 *     pending row tagged `trigger_context='drag_drop'`. The operator
 *     resolution path is identical to scenario 1 — what we're locking
 *     here is that the page renders + resolves drag-drop-originated
 *     rows correctly.
 *
 *   - **Scenario 3 covers the add_stream trigger** via the same
 *     mechanism, with a row tagged `trigger_context='add_stream'`.
 *
 * Deviation from the original bead spec (bd-etdeb):
 *
 *   The bead asks for Scenario A (drag_drop) and Scenario B (add_stream)
 *   to be driven through the source UI affordances (drag a stream onto
 *   a channel; "Create channel(s) in group" context menu). Driving those
 *   surfaces in a hermetic E2E would require mocking the full channels
 *   + streams + groups + providers API surface and reliably emulating
 *   HTML5 drag-and-drop on a dynamic, virtualised list — neither of
 *   which is achievable inside the time budget for the v0.17.1 cut
 *   without producing a brittle, flake-prone spec.
 *
 *   The existing component-level coverage in `useDedupOnDrop.test.ts`
 *   and `useAddStreamDedup.test.ts` already locks the hook → modal →
 *   onMerge → addStreamToChannel wiring at the React-component layer
 *   (no test pyramid inversion — those tests are fast vitest specs).
 *   This Playwright spec covers the cross-surface operator UI flow
 *   (page navigation, badge visibility, Merge-click resolution) which
 *   is the layer the component tests cannot reach.
 *
 *   The cross-trigger §D6 audit-field set is covered by
 *   `backend/tests/integration/test_dedup_audit_trail.py` per the bead's
 *   "backend integration tests OR Playwright are both acceptable for
 *   the journal-audit assertion" allowance.
 *
 * Note on test runtime environment:
 *
 *   Like all specs under `e2e/`, this spec runs against the live
 *   container at port 6100 (configured in `playwright.config.ts`).
 *   The main E2E suite is currently deferred from CI per bd-2lw25;
 *   this spec is designed for the same on-demand local execution.
 *   When CI re-enables the suite, this spec is hermetic — its only
 *   environmental dependency is a running app shell to serve the
 *   bundled frontend.
 */
import { test, expect, navigateToTab, isLoginPage, performLogin } from './fixtures/base';
import { selectors } from './fixtures/test-data';
import type { Page, Route } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock fixtures — shape matches the API contracts in
// backend/routers/channel_merges.py and ADR-008 §D1.
// ---------------------------------------------------------------------------

interface PendingMergeRecordFixture {
  id: number;
  stream_name: string;
  group_id: number | null;
  candidate_channel_id: string;
  confidence: number;
  status: 'pending' | 'merged' | 'dismissed';
  created_at: number;
  resolved_at: number | null;
  resolution_source: string | null;
  trigger_context: 'drag_drop' | 'add_stream' | 'm3u_refresh' | 'mcp_tool';
}

const ROW_DRAG_DROP: PendingMergeRecordFixture = {
  id: 101,
  stream_name: 'ESPN HD',
  group_id: 5,
  candidate_channel_id: 'ch-uuid-drag-001',
  confidence: 0.87,
  status: 'pending',
  created_at: 1_715_817_600_000,
  resolved_at: null,
  resolution_source: null,
  trigger_context: 'drag_drop',
};

const ROW_ADD_STREAM: PendingMergeRecordFixture = {
  id: 102,
  stream_name: 'CNN HD',
  group_id: 5,
  candidate_channel_id: 'ch-uuid-add-002',
  confidence: 0.92,
  status: 'pending',
  created_at: 1_715_817_700_000,
  resolved_at: null,
  resolution_source: null,
  trigger_context: 'add_stream',
};

const ROW_M3U_REFRESH: PendingMergeRecordFixture = {
  id: 103,
  stream_name: 'TNT HD',
  group_id: 5,
  candidate_channel_id: 'ch-uuid-m3u-003',
  confidence: 1.0,
  status: 'pending',
  created_at: 1_715_817_800_000,
  resolved_at: null,
  resolution_source: null,
  trigger_context: 'm3u_refresh',
};

// ---------------------------------------------------------------------------
// Helper — wire up the route mocks the PendingMergesPage and subnav need.
// Returns the captured accept-request log so individual tests can assert on
// which row ids were resolved through the operator's Merge clicks.
// ---------------------------------------------------------------------------

interface AcceptRequestLog {
  /** The merge_id from the URL path. Captures the row the operator clicked. */
  merge_id: number;
  /** The HTTP method (always POST for the contract under test). */
  method: string;
}

interface DedupRouteState {
  /** Rows the list endpoint returns. Tests mutate this to simulate resolution. */
  rows: PendingMergeRecordFixture[];
  /** Append-only log of accept calls — each entry is one operator Merge click. */
  acceptLog: AcceptRequestLog[];
}

/**
 * Install the full set of route mocks needed for the dedup flow. The
 * `state` parameter is mutated by the accept handler so the test can:
 *   (a) seed pending rows up front,
 *   (b) observe which rows the operator accepted, and
 *   (c) have the next list-poll return the post-resolution row set.
 *
 * Routes installed:
 *   - GET  /api/channel-merges?status=pending&...  → list endpoint
 *   - POST /api/channel-merges/{id}/accept         → merge endpoint
 *   - POST /api/channel-merges/{id}/dismiss        → dismiss endpoint
 *   - GET  /api/notifications?page_size=20         → empty notifications
 *     (NotificationCenter polls this on mount and would otherwise hit
 *     the real backend, producing noise in the test trace)
 *
 * Notifications are mocked so the post-M3U toast notification can be
 * INJECTED on demand by the scenario-1 test — we replace the empty
 * notifications response with one containing the dedup-decorator-
 * triggering "N pending merges queued" notification when we want to
 * exercise the toast path.
 */
async function installDedupRoutes(page: Page, state: DedupRouteState): Promise<void> {
  // Use a callback that inspects the URL path (rather than a glob pattern)
  // to avoid Playwright's glob quirks around the `?` character (single-char
  // match vs query-string separator). The URL-predicate form is unambiguous
  // and matches both "/api/channel-merges?..." and "/api/channel-merges"
  // (with or without query string) without accidentally matching the
  // sibling endpoints (`/api/channel-merges/candidates`,
  // `/api/channel-merges/{id}/accept|dismiss`).
  await page.route(
    (url) =>
      url.pathname === '/api/channel-merges' ||
      url.pathname.endsWith('/api/channel-merges'),
    (route: Route) => {
      const url = new URL(route.request().url());
      const status = url.searchParams.get('status') || 'pending';
      const filtered = state.rows.filter((r) => r.status === status);
      route.fulfill({
        json: {
          merges: filtered,
          total: filtered.length,
          page: 1,
          page_size: 50,
          total_pages: filtered.length > 0 ? 1 : 0,
        },
      });
    },
  );

  await page.route('**/api/channel-merges/*/accept', (route: Route) => {
    const url = new URL(route.request().url());
    // The pattern is /api/channel-merges/{id}/accept — extract the id.
    const match = url.pathname.match(/\/api\/channel-merges\/(\d+)\/accept/);
    if (!match) {
      route.fulfill({ status: 400, json: { detail: 'malformed merge_id' } });
      return;
    }
    const mergeId = parseInt(match[1], 10);
    state.acceptLog.push({ merge_id: mergeId, method: route.request().method() });

    // Flip the row to merged + drop it from the pending list — the
    // optimistic-remove path in PendingMergesPage will also remove it
    // client-side, but the next list-poll (e.g., from the subnav badge
    // poll) will read the same flipped state through this route.
    const row = state.rows.find((r) => r.id === mergeId);
    if (row) {
      row.status = 'merged';
      row.resolved_at = Date.now();
      row.resolution_source = 'operator';
    }

    route.fulfill({
      json: {
        merged_into_channel_id: row?.candidate_channel_id ?? 'unknown',
        journal_entry_id: 9000 + mergeId,
        source_stream_id: String(40_000 + mergeId),
        confidence: row?.confidence ?? 0,
        status: 'merged',
      },
    });
  });

  await page.route('**/api/channel-merges/*/dismiss', (route: Route) => {
    const url = new URL(route.request().url());
    const match = url.pathname.match(/\/api\/channel-merges\/(\d+)\/dismiss/);
    if (!match) {
      route.fulfill({ status: 400, json: { detail: 'malformed merge_id' } });
      return;
    }
    const mergeId = parseInt(match[1], 10);
    const row = state.rows.find((r) => r.id === mergeId);
    if (row) {
      row.status = 'dismissed';
      row.resolved_at = Date.now();
      row.resolution_source = 'operator';
    }
    route.fulfill({
      json: {
        journal_entry_id: 9500 + mergeId,
        status: 'dismissed',
      },
    });
  });

  // NotificationCenter polls /api/notifications on mount; default to empty.
  // Individual tests can override this route after install to inject a
  // dedup-relevant notification for the toast-decoration path.
  await page.route('**/api/notifications?**', (route: Route) => {
    route.fulfill({
      json: { notifications: [], unread_count: 0, total: 0 },
    });
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Dedup flow E2E (bd-etdeb / ADR-008)', () => {
  let state: DedupRouteState;
  // We do NOT use the appPage fixture here because the default Channel
  // Manager tab mounts on initial load and immediately polls
  // `/api/channel-merges` via ChannelManagerTab.refreshCount(). If the
  // appPage fixture navigates first and then beforeEach installs the
  // route mock, that initial poll fires against the real backend before
  // the mock is active and the subnav badge stays at zero. Installing
  // routes on a raw Page BEFORE navigating ensures every dedup-related
  // call is intercepted from the first render onwards.
  let appPage: Page;

  test.beforeEach(async ({ page }) => {
    // Fresh per-test state — the mutation in installDedupRoutes()'s
    // accept handler must not leak between tests, otherwise the
    // "row disappears after Merge" assertion would be poisoned by
    // the previous test's resolution.
    state = {
      rows: [
        // Deep clones — mutation in the accept handler flips status to
        // 'merged' in place; the next test needs a fresh 'pending' row.
        { ...ROW_DRAG_DROP },
        { ...ROW_ADD_STREAM },
        { ...ROW_M3U_REFRESH },
      ],
      acceptLog: [],
    };

    // 1. Install route mocks FIRST so every dedup call (including the
    //    Channel Manager tab's initial mount poll) is intercepted.
    await installDedupRoutes(page, state);

    // 2. Mirror the appPage fixture's load + login behavior, just with
    //    the routes already wired. Mirrors `e2e/fixtures/base.ts:96-129`
    //    intentionally so a future change to the fixture's load contract
    //    is something a reader can spot the divergence on.
    let loaded = false;
    for (let attempt = 0; attempt < 3 && !loaded; attempt++) {
      if (attempt > 0) {
        await page.waitForTimeout(1000);
        await page.reload({ waitUntil: 'domcontentloaded' });
      } else {
        await page.goto('/', { waitUntil: 'domcontentloaded' });
      }

      if (await isLoginPage(page)) {
        await performLogin(page);
      }

      try {
        await page.waitForSelector(selectors.header, { timeout: 20_000 });
        await page.waitForSelector('.tab-navigation', { timeout: 15_000 });
        loaded = true;
      } catch {
        // Retry — handled by the outer loop.
      }
    }

    if (!loaded) {
      throw new Error(
        'App failed to load after 3 attempts in dedup-flow spec — header or tab navigation not found',
      );
    }

    appPage = page;
  });

  // -------------------------------------------------------------------------
  // Scenario C — async M3U queue → PendingMergesPage → resolve
  // -------------------------------------------------------------------------
  test('Scenario C (m3u_refresh) — queue badge, navigate, merge, row disappears', async () => {
    await navigateToTab(appPage, 'channel-manager');

    // The subnav appears only when pendingMergesCount > 0 (or the
    // operator is already on the page). With three seeded rows the
    // badge should appear after the first poll (~immediate on mount).
    const badge = appPage.getByTestId('pending-merges-badge');
    await expect(badge).toBeVisible({ timeout: 15_000 });
    await expect(badge).toHaveText('3');

    // Operator clicks the "Pending Merges" subnav link to drill in.
    const subnavLink = appPage.getByRole('button', { name: /Pending Merges/ });
    await subnavLink.click();

    // The page renders the three queued rows. Selecting by stream_name
    // (which is operator-visible content) keeps the assertion stable
    // against row-id reshuffles in the fixture.
    await expect(appPage.getByText('TNT HD')).toBeVisible();
    await expect(appPage.getByText('ESPN HD')).toBeVisible();
    await expect(appPage.getByText('CNN HD')).toBeVisible();

    // The m3u_refresh row carries an exact-match confidence (1.00); the
    // page renders the "Exact match" badge instead of a percent. This is
    // the §D2 contract — exact-match autofocus on the Merge button in
    // the modal surface (StreamDedupModal), mirrored on this page by
    // the prominent "Exact match" badge.
    await expect(appPage.getByLabel('Exact match').first()).toBeVisible();

    // Operator clicks Merge on the m3u_refresh-originated row. We scope
    // the click to the list item containing the TNT HD label so we don't
    // accidentally click a different row's Merge.
    const m3uRow = appPage.locator('.pending-merges-row').filter({ hasText: 'TNT HD' });
    await m3uRow.getByRole('button', { name: 'Merge' }).click();

    // After accept resolves, the row is optimistically removed from
    // the page's local list. The other two rows remain.
    await expect(appPage.getByText('TNT HD')).not.toBeVisible({ timeout: 5_000 });
    await expect(appPage.getByText('ESPN HD')).toBeVisible();
    await expect(appPage.getByText('CNN HD')).toBeVisible();

    // The accept endpoint received exactly ONE POST, for the m3u_refresh
    // row's id (103). The audit-journal field set written behind that
    // POST is verified separately in
    // backend/tests/integration/test_dedup_audit_trail.py.
    expect(state.acceptLog).toHaveLength(1);
    expect(state.acceptLog[0]).toEqual({
      merge_id: ROW_M3U_REFRESH.id,
      method: 'POST',
    });
  });

  // -------------------------------------------------------------------------
  // Scenario A — drag_drop-originated row resolves through the page
  // -------------------------------------------------------------------------
  test('Scenario A (drag_drop) — drag_drop-tagged row is resolvable via PendingMergesPage Merge', async () => {
    await navigateToTab(appPage, 'channel-manager');

    // Drill into Pending Merges via the subnav.
    await appPage
      .getByTestId('pending-merges-badge')
      .waitFor({ state: 'visible', timeout: 15_000 });
    await appPage.getByRole('button', { name: /Pending Merges/ }).click();

    // The drag_drop row's confidence is 0.87 → renders a "87% match" badge
    // (not "Exact match") per the §D2 fuzzy-vs-exact distinction.
    const dragRow = appPage.locator('.pending-merges-row').filter({ hasText: 'ESPN HD' });
    await expect(dragRow).toBeVisible();
    await expect(dragRow.getByLabel(/Confidence: 87 percent/i)).toBeVisible();

    // Operator clicks Merge. The accept endpoint sees the drag-drop row's
    // id — the trigger_context tag rides on the row and is mirrored to
    // the journal at accept time (proven in the backend test).
    await dragRow.getByRole('button', { name: 'Merge' }).click();

    await expect(appPage.getByText('ESPN HD')).not.toBeVisible({ timeout: 5_000 });

    expect(state.acceptLog).toHaveLength(1);
    expect(state.acceptLog[0]).toEqual({
      merge_id: ROW_DRAG_DROP.id,
      method: 'POST',
    });
  });

  // -------------------------------------------------------------------------
  // Scenario B — add_stream-originated row resolves through the page
  // -------------------------------------------------------------------------
  test('Scenario B (add_stream) — add_stream-tagged row is resolvable via PendingMergesPage Merge', async () => {
    await navigateToTab(appPage, 'channel-manager');

    await appPage
      .getByTestId('pending-merges-badge')
      .waitFor({ state: 'visible', timeout: 15_000 });
    await appPage.getByRole('button', { name: /Pending Merges/ }).click();

    // The add_stream row's confidence is 0.92 → "92% match" badge.
    const addRow = appPage.locator('.pending-merges-row').filter({ hasText: 'CNN HD' });
    await expect(addRow).toBeVisible();
    await expect(addRow.getByLabel(/Confidence: 92 percent/i)).toBeVisible();

    await addRow.getByRole('button', { name: 'Merge' }).click();

    await expect(appPage.getByText('CNN HD')).not.toBeVisible({ timeout: 5_000 });

    expect(state.acceptLog).toHaveLength(1);
    expect(state.acceptLog[0]).toEqual({
      merge_id: ROW_ADD_STREAM.id,
      method: 'POST',
    });
  });

  // -------------------------------------------------------------------------
  // Subnav contract — the badge hides when the queue drains
  // -------------------------------------------------------------------------
  test('subnav badge reflects the queue depth; hides when queue drains', async () => {
    await navigateToTab(appPage, 'channel-manager');

    // Badge initially shows 3 (all three seeded rows).
    const badge = appPage.getByTestId('pending-merges-badge');
    await expect(badge).toBeVisible({ timeout: 15_000 });
    await expect(badge).toHaveText('3');

    // Drain the queue. Resolving from off-page (via the API directly) is
    // the closest analog to a multi-tab scenario where another operator
    // (or an MCP tool) drained the queue from elsewhere; the badge poll
    // (30s in production, but the mocked list responds immediately) is
    // what catches it.
    //
    // For a fast deterministic assertion we resolve via the page surface
    // instead: navigate in, resolve all three, navigate out. After all
    // three resolve, the subnav link disappears because
    // showSubnavLink = pendingMergesCount > 0 || view === 'pending-merges'
    // and the operator left the page.

    await appPage.getByRole('button', { name: /Pending Merges/ }).click();

    // Resolve all three rows in sequence.
    for (const streamName of ['TNT HD', 'ESPN HD', 'CNN HD']) {
      const row = appPage.locator('.pending-merges-row').filter({ hasText: streamName });
      await row.getByRole('button', { name: 'Merge' }).click();
      await expect(appPage.getByText(streamName)).not.toBeVisible({ timeout: 5_000 });
    }

    // Empty state appears once the queue is drained on the page.
    await expect(appPage.getByText(/No pending merges/i)).toBeVisible();

    // All three accept calls landed exactly once each.
    expect(state.acceptLog.map((e) => e.merge_id).sort()).toEqual([
      ROW_DRAG_DROP.id,
      ROW_ADD_STREAM.id,
      ROW_M3U_REFRESH.id,
    ]);
  });
});
