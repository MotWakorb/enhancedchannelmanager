/**
 * Watches EPG guide downloads to completion and publishes `epg-data` when one
 * finishes (beads enhancedchannelmanager-3vtim and -1twap).
 *
 * WHY THIS IS A MODULE AND NOT A HOOK
 *
 * `App.epgData` is the catalogue behind the Edit Channel "EPG Data" picker and
 * it is loaded exactly once, at app start. A source added afterwards therefore
 * leaves the picker rendering an empty snapshot until a full page reload — and
 * a reload signs the operator out. The fix for `3vtim` published the
 * invalidation from EPG Manager's own poller, which works only while EPG
 * Manager is mounted.
 *
 * Drill run 2026-08-09-run18 walked the ordinary fresh-install sequence —
 * install, open the UI, add a source, go and link channels — and so navigated
 * away from EPG Manager while the guide was still parsing. The tab unmounted,
 * its poller died with it, the publish never fired, and the picker showed
 * "Guide data has not loaded yet" for a source that had reached
 * `status=success` with 14,663 rows. The search fired ZERO backend requests
 * (bead enhancedchannelmanager-1twap).
 *
 * So the watch outlives the component. It lives here, at module scope.
 *
 * WHAT THIS IS NOT
 *
 * Not a standing poller, and not refetch-on-focus — bead
 * enhancedchannelmanager-5z7c9's closing condition still holds. The interval
 * exists only while a download this module is watching is genuinely in flight,
 * it is bounded by {@link WATCH_MAX_DURATION_MS}, and it stands down entirely
 * while another poller (EPG Manager's own, when mounted) is feeding it rows
 * through {@link noteGuideDownloadRows}. With no download in flight this
 * module issues no requests at all.
 */
import type { EPGSource } from '../types';
import * as api from './api';
import { invalidateServerData } from '../hooks/useServerDataInvalidation';
import { logger } from '../utils/logger';

/** How often the fallback poller re-reads source status. */
export const WATCH_POLL_INTERVAL_MS = 2000;

/** Hard stop, so a download that never terminates cannot poll forever. */
export const WATCH_MAX_DURATION_MS = 300_000;

/**
 * A tick is skipped if someone else fed us rows more recently than this. EPG
 * Manager polls every {@link WATCH_POLL_INTERVAL_MS} while mounted, so a
 * grace slightly above that interval keeps the request rate unchanged from
 * before this module existed whenever the tab is on screen.
 */
export const EXTERNAL_FEED_GRACE_MS = 3000;

/** A source Dispatcharr is still downloading or parsing a guide for. */
export function isDownloadingSource(source: EPGSource): boolean {
  return source.status === 'fetching' || source.status === 'parsing';
}

interface Watch {
  /**
   * The `updated_at` the row carried when the watch started. A status test
   * alone is not enough: Dispatcharr has not necessarily flipped off `success`
   * by the time the first poll after the click lands, so the STALE success is
   * indistinguishable from a fresh one by status. `updated_at` tells them
   * apart — measured on the live instance, a refresh bumps it.
   */
  updatedAt: string | null;
  /** Set once the row has actually been seen in a downloading state. */
  sawDownloading: boolean;
}

const watches = new Map<number, Watch>();

let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollStartedAt = 0;
let lastExternalFeedAt = 0;

/** Number of downloads currently being watched. */
export function guideDownloadWatchCount(): number {
  return watches.size;
}

/** Whether `sourceId`'s download is still being waited on. */
export function hasGuideDownloadWatch(sourceId: number): boolean {
  return watches.has(sourceId);
}

/** Start waiting for `source` to finish downloading a guide. */
export function watchGuideDownload(source: EPGSource): void {
  watches.set(source.id, {
    updatedAt: source.updated_at ?? null,
    sawDownloading: false,
  });
  startPolling();
}

/**
 * Feed freshly-fetched source rows to the completion detector.
 *
 * Hangs off the ROWS rather than off one fetch wrapper, because the per-source
 * refresh and "Refresh All" each run their own poller and neither goes through
 * EPG Manager's `loadSources`. The first cut put the detection inside
 * `loadSources`, and a manual refresh of an already-successful source then
 * issued 50 status polls and never once refetched the guide (live re-drive
 * 2026-08-09).
 */
export function noteGuideDownloadRows(rows: EPGSource[]): void {
  lastExternalFeedAt = Date.now();
  applyRows(rows);
}

function applyRows(rows: EPGSource[]): void {
  let completed = false;
  for (const row of rows) {
    if (isDownloadingSource(row)) {
      // Adopt a download nobody here started: a scheduled refresh, or one
      // already running when this module first saw the row. Its completion
      // changes the guide just as much as one the operator clicked.
      const existing = watches.get(row.id);
      if (existing) existing.sawDownloading = true;
      else watches.set(row.id, { updatedAt: row.updated_at ?? null, sawDownloading: true });
      continue;
    }
    const watch = watches.get(row.id);
    if (!watch) continue;
    if (row.status !== 'success' && row.status !== 'error') continue;
    // Still the pre-click reading — Dispatcharr has not picked the job up yet.
    const isFreshResult = watch.sawDownloading || (row.updated_at ?? null) !== watch.updatedAt;
    if (!isFreshResult) continue;
    watches.delete(row.id);
    // An `error` ends the wait without publishing: a failed download leaves no
    // new guide rows to go and fetch.
    if (row.status === 'success') completed = true;
  }

  if (completed) invalidateServerData('epg-data');
  // Adopting a row above can start a watch, so re-arm before standing down.
  if (watches.size === 0) stopPolling();
  else startPolling();
}

function startPolling(): void {
  if (pollTimer !== null) return;
  pollStartedAt = Date.now();
  pollTimer = setInterval(() => {
    void tick();
  }, WATCH_POLL_INTERVAL_MS);
}

function stopPolling(): void {
  if (pollTimer === null) return;
  clearInterval(pollTimer);
  pollTimer = null;
}

async function tick(): Promise<void> {
  if (watches.size === 0) {
    stopPolling();
    return;
  }
  if (Date.now() - pollStartedAt > WATCH_MAX_DURATION_MS) {
    logger.warn(
      '[epgGuideWatch] Giving up on %d guide download(s) after %ds',
      watches.size,
      Math.round(WATCH_MAX_DURATION_MS / 1000),
    );
    watches.clear();
    stopPolling();
    return;
  }
  // EPG Manager is mounted and polling; its rows already reach us.
  if (Date.now() - lastExternalFeedAt < EXTERNAL_FEED_GRACE_MS) return;

  try {
    applyRows(await api.getEPGSources());
  } catch (err) {
    // Transient — the next tick retries, and the deadline above bounds it.
    logger.debug('[epgGuideWatch] Source poll failed, will retry:', err);
  }
}

/** Test seam: drop all watches and stop polling. */
export function resetGuideDownloadWatchesForTest(): void {
  watches.clear();
  stopPolling();
  pollStartedAt = 0;
  lastExternalFeedAt = 0;
}
