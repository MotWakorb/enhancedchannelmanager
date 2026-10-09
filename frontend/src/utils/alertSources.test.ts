/**
 * The alert_sources draft <-> payload round trip (GH #1026).
 *
 * The backend treats a missing section as "send everything" and stores `{}`
 * as NULL, so the payload must only carry what differs from that, and must
 * not drop sections this UI does not know.
 */
import { describe, it, expect } from 'vitest';
import type { AlertMethod } from '../services/api';
import {
  ALERT_CATEGORIES,
  alertSourcesFromDraft,
  draftFromAlertSources,
  summarizeRouting,
} from './alertSources';

describe('draftFromAlertSources', () => {
  it('treats null as every category enabled with no filter', () => {
    const draft = draftFromAlertSources(null);

    expect(Object.keys(draft)).toEqual(ALERT_CATEGORIES.map((c) => c.key));
    for (const entry of Object.values(draft)) {
      expect(entry).toEqual({ enabled: true, filterMode: 'all', ids: [], minFailures: 1 });
    }
  });

  it('reads stored sections', () => {
    const draft = draftFromAlertSources({
      version: 1,
      emby: { enabled: false },
      m3u_refresh: { filter_mode: 'all_except', account_ids: [4, 5] },
      probe_failures: { min_failures: 3 },
    });

    expect(draft.emby.enabled).toBe(false);
    expect(draft.m3u_refresh).toMatchObject({ enabled: true, filterMode: 'all_except', ids: [4, 5] });
    expect(draft.probe_failures.minFailures).toBe(3);
  });
});

describe('alertSourcesFromDraft', () => {
  it('returns {} (send everything) for an untouched draft', () => {
    expect(alertSourcesFromDraft(draftFromAlertSources(null))).toEqual({});
  });

  it('writes only the sections that differ from the default', () => {
    const draft = draftFromAlertSources(null);
    draft.task_results.enabled = false;
    draft.epg_refresh = { ...draft.epg_refresh, filterMode: 'only_selected', ids: [7] };
    draft.probe_failures.minFailures = 5;

    expect(alertSourcesFromDraft(draft)).toEqual({
      version: 1,
      task_results: { enabled: false },
      epg_refresh: { filter_mode: 'only_selected', source_ids: [7] },
      probe_failures: { min_failures: 5 },
    });
  });

  it('drops an entity filter whose mode is back to "all"', () => {
    const draft = draftFromAlertSources({ m3u_refresh: { filter_mode: 'only_selected', account_ids: [1] } });
    draft.m3u_refresh.filterMode = 'all';

    expect(alertSourcesFromDraft(draft)).toEqual({});
  });

  it('keeps sections this UI does not know about', () => {
    const original = { version: 1, future_category: { enabled: false } };

    expect(alertSourcesFromDraft(draftFromAlertSources(original), original)).toEqual({
      version: 1,
      future_category: { enabled: false },
    });
  });

  it('round-trips a stored filter unchanged', () => {
    const stored = {
      version: 1,
      emby: { enabled: false },
      m3u_refresh: { filter_mode: 'all_except' as const, account_ids: [4, 5] },
    };

    expect(alertSourcesFromDraft(draftFromAlertSources(stored), stored)).toEqual(stored);
  });
});

describe('summarizeRouting', () => {
  const base: AlertMethod = {
    id: 1, name: 'A', method_type: 'discord', enabled: true, config: {},
    notify_info: false, notify_success: true, notify_warning: true, notify_error: true,
  };

  it('names the severities and says when every category is on', () => {
    expect(summarizeRouting(base)).toBe('Errors, warnings, successes · all categories');
  });

  it('counts switched-off and filtered categories', () => {
    expect(summarizeRouting({
      ...base,
      notify_info: true,
      alert_sources: { emby: { enabled: false }, other: { enabled: false }, probe_failures: { min_failures: 2 } },
    })).toBe('All severities · 2 categories off · 1 filtered');
  });

  it('says when no severity is enabled', () => {
    expect(summarizeRouting({
      ...base, notify_success: false, notify_warning: false, notify_error: false,
    })).toBe('No severities · all categories');
  });
});
