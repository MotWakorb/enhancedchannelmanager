/**
 * Tests for the module-scoped EPG guide-download watch
 * (beads enhancedchannelmanager-3vtim and -1twap).
 *
 * THE REGRESSION THAT MATTERS
 *
 * `3vtim`'s fix published `epg-data` from EPG Manager's own poller. Drill run
 * 2026-08-09-run18 walked the ordinary fresh-install sequence — install, open
 * the UI, add a source, go and link channels — which navigates AWAY from EPG
 * Manager while the guide is still parsing. The tab unmounted, its poller died
 * with it, the publish never fired, and the Edit Channel guide picker kept the
 * empty snapshot it loaded at app start: "Guide data has not loaded yet", with
 * ZERO backend requests, for a source that had reached `status=success` with
 * 14,663 rows. Only a full reload fixed it, and a reload signs the operator
 * out.
 *
 * So the test that counts is "completes with nobody feeding it" below: it
 * registers a watch, then never calls `noteGuideDownloadRows` again — exactly
 * what an unmounted tab does — and asserts the publish still happens.
 *
 * The other half of the contract is bead enhancedchannelmanager-5z7c9's
 * standing condition: no polling except while a download is genuinely in
 * flight, and no refetch-on-focus. "issues no requests while nothing is
 * downloading" and "stands down while another poller is feeding it" hold that
 * line.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { EPGSource } from '../types';

const getEPGSources = vi.fn();
vi.mock('./api', () => ({ getEPGSources: () => getEPGSources() }));

const invalidateServerData = vi.fn();
vi.mock('../hooks/useServerDataInvalidation', () => ({
  invalidateServerData: (key: string) => invalidateServerData(key),
}));

import {
  EXTERNAL_FEED_GRACE_MS,
  WATCH_MAX_DURATION_MS,
  WATCH_POLL_INTERVAL_MS,
  guideDownloadWatchCount,
  hasGuideDownloadWatch,
  noteGuideDownloadRows,
  resetGuideDownloadWatchesForTest,
  watchGuideDownload,
} from './epgGuideWatch';

function source(over: Partial<EPGSource> = {}): EPGSource {
  return {
    id: 1,
    name: 'US Guide',
    url: 'https://example.invalid/guide.xml.gz',
    source_type: 'xmltv',
    is_active: true,
    priority: 0,
    status: 'success',
    updated_at: '2026-08-09T03:26:26Z',
    ...over,
  } as EPGSource;
}

/** Let the module's interval fire `ticks` times, draining each poll's promise. */
async function advanceTicks(ticks: number): Promise<void> {
  for (let i = 0; i < ticks; i += 1) {
    await vi.advanceTimersByTimeAsync(WATCH_POLL_INTERVAL_MS);
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  resetGuideDownloadWatchesForTest();
});

afterEach(() => {
  resetGuideDownloadWatchesForTest();
  vi.useRealTimers();
});

describe('epgGuideWatch — completion detection', () => {
  it('publishes epg-data when a watched download finishes', () => {
    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));

    noteGuideDownloadRows([source({ status: 'fetching', updated_at: 'T0' })]);
    expect(invalidateServerData).not.toHaveBeenCalled();

    noteGuideDownloadRows([source({ status: 'success', updated_at: 'T1' })]);
    expect(invalidateServerData).toHaveBeenCalledWith('epg-data');
    expect(guideDownloadWatchCount()).toBe(0);
  });

  it('ignores the PREVIOUS run\'s success reading', () => {
    // Dispatcharr has not necessarily flipped off `success` by the time the
    // first poll after the click lands. Status alone cannot tell the stale
    // reading from a fresh one; `updated_at` can.
    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));

    noteGuideDownloadRows([source({ status: 'success', updated_at: 'T0' })]);

    expect(invalidateServerData).not.toHaveBeenCalled();
    expect(hasGuideDownloadWatch(1)).toBe(true);
  });

  it('ends the wait on error without publishing', () => {
    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));

    noteGuideDownloadRows([source({ status: 'fetching', updated_at: 'T0' })]);
    noteGuideDownloadRows([source({ status: 'error', updated_at: 'T1' })]);

    expect(invalidateServerData).not.toHaveBeenCalled();
    expect(guideDownloadWatchCount()).toBe(0);
  });

  it('adopts a download nobody here started', () => {
    // A scheduled refresh, or one already running when the app opened.
    noteGuideDownloadRows([source({ id: 4, status: 'parsing', updated_at: 'T0' })]);
    expect(hasGuideDownloadWatch(4)).toBe(true);

    noteGuideDownloadRows([source({ id: 4, status: 'success', updated_at: 'T1' })]);
    expect(invalidateServerData).toHaveBeenCalledWith('epg-data');
  });
});

describe('epgGuideWatch — surviving the EPG Manager tab unmounting (bd-1twap)', () => {
  it('completes with nobody feeding it', async () => {
    // The drill's sequence: add a source, then leave the tab. Nothing calls
    // noteGuideDownloadRows again — the watch has to finish the job itself.
    getEPGSources
      .mockResolvedValueOnce([source({ status: 'fetching', updated_at: 'T0' })])
      .mockResolvedValue([source({ status: 'success', updated_at: 'T1' })]);

    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));

    // Past the grace window that defers to an external poller, then two ticks.
    await vi.advanceTimersByTimeAsync(EXTERNAL_FEED_GRACE_MS);
    await advanceTicks(2);

    expect(getEPGSources).toHaveBeenCalled();
    expect(invalidateServerData).toHaveBeenCalledWith('epg-data');
    expect(guideDownloadWatchCount()).toBe(0);
  });

  it('stops polling once the download it was watching is done', async () => {
    getEPGSources.mockResolvedValue([source({ status: 'success', updated_at: 'T1' })]);

    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));
    await vi.advanceTimersByTimeAsync(EXTERNAL_FEED_GRACE_MS);
    await advanceTicks(2);

    const callsAtCompletion = getEPGSources.mock.calls.length;
    await advanceTicks(5);
    expect(getEPGSources.mock.calls.length).toBe(callsAtCompletion);
  });

  it('issues no requests while nothing is downloading', async () => {
    // bd-5z7c9's standing condition: no polling, no refetch-on-focus.
    await advanceTicks(10);
    expect(getEPGSources).not.toHaveBeenCalled();
  });

  it('stands down while another poller is feeding it rows', async () => {
    // EPG Manager mounted and polling: its rows already reach us, so the
    // fallback must not double the request rate against Dispatcharr.
    getEPGSources.mockResolvedValue([source({ status: 'fetching', updated_at: 'T0' })]);
    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));

    for (let i = 0; i < 4; i += 1) {
      noteGuideDownloadRows([source({ status: 'fetching', updated_at: 'T0' })]);
      await advanceTicks(1);
    }

    expect(getEPGSources).not.toHaveBeenCalled();
    expect(hasGuideDownloadWatch(1)).toBe(true);
  });

  it('gives up rather than polling forever', async () => {
    getEPGSources.mockResolvedValue([source({ status: 'fetching', updated_at: 'T0' })]);
    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));

    await vi.advanceTimersByTimeAsync(WATCH_MAX_DURATION_MS + WATCH_POLL_INTERVAL_MS * 2);

    expect(guideDownloadWatchCount()).toBe(0);
    const callsAfterGivingUp = getEPGSources.mock.calls.length;
    await advanceTicks(5);
    expect(getEPGSources.mock.calls.length).toBe(callsAfterGivingUp);
  });

  it('keeps waiting when a poll fails', async () => {
    getEPGSources
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue([source({ status: 'success', updated_at: 'T1' })]);

    watchGuideDownload(source({ status: 'success', updated_at: 'T0' }));
    await vi.advanceTimersByTimeAsync(EXTERNAL_FEED_GRACE_MS);
    await advanceTicks(3);

    expect(invalidateServerData).toHaveBeenCalledWith('epg-data');
  });
});
