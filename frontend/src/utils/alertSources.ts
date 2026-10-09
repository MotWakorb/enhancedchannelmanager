/**
 * Alert categories and the per-destination `alert_sources` filter (GH #1026).
 *
 * Every alert ECM raises carries one category, and each alert method's
 * `alert_sources` can switch categories off (or, for EPG / M3U refreshes and
 * stream probes, narrow them). The backend's list is
 * `alert_methods.ALERT_CATEGORIES`; this is the UI's copy, with labels. A
 * category the backend adds before this list learns it simply stays enabled
 * (a missing section means enabled), and any section this UI does not know is
 * carried through a save untouched.
 */
import type { AlertMethod, AlertSourceFilterMode, AlertSourceSection, AlertSources } from '../services/api';

export type AlertCategoryKind = 'toggle' | 'entities' | 'threshold';

export interface AlertCategoryInfo {
  key: string;
  label: string;
  description: string;
  kind: AlertCategoryKind;
  /** For `entities` categories: which `alert_sources` key holds the ids. */
  idsField?: 'source_ids' | 'account_ids';
}

export const ALERT_CATEGORIES: readonly AlertCategoryInfo[] = [
  {
    key: 'epg_refresh', label: 'EPG refreshes', kind: 'entities', idsField: 'source_ids',
    description: 'EPG source refresh results, failures and timeouts.',
  },
  {
    key: 'm3u_refresh', label: 'M3U refreshes', kind: 'entities', idsField: 'account_ids',
    description: 'M3U account refresh results, failures and timeouts.',
  },
  {
    key: 'probe_failures', label: 'Stream probes', kind: 'threshold',
    description: 'Stream probe runs, including scheduled probes and failed-stream re-probes.',
  },
  {
    key: 'task_results', label: 'Scheduled task results', kind: 'toggle',
    description: 'Start and completion alerts from the other scheduled tasks.',
  },
  {
    key: 'channel_pipeline', label: 'Channel pipeline', kind: 'toggle',
    description: 'Auto-creation runs, caps and event sync warnings.',
  },
  {
    key: 'dbas_backup', label: 'DBAS backup and restore', kind: 'toggle',
    description: 'Backup runs, cloud uploads and restores.',
  },
  {
    key: 'dbas_sync', label: 'Cross-instance sync', kind: 'toggle',
    description: 'Skipped or failed cross-instance sync runs.',
  },
  {
    key: 'emby', label: 'Emby', kind: 'toggle',
    description: 'Emby logo clean-up progress and failures.',
  },
  {
    key: 'profile_conflicts', label: 'Profile conflict review', kind: 'toggle',
    description: 'Channel profile conflicts that need a decision.',
  },
  {
    key: 'other', label: 'Other', kind: 'toggle',
    description: 'Anything not in a category above, including alerts posted through the API.',
  },
];

/** Editable state for one category: every field present, defaults filled. */
export interface CategoryDraft {
  enabled: boolean;
  filterMode: AlertSourceFilterMode;
  ids: number[];
  minFailures: number;
}

export type AlertSourcesDraft = Record<string, CategoryDraft>;

const DEFAULT_MIN_FAILURES = 1;

function sectionOf(sources: AlertSources | null | undefined, key: string): AlertSourceSection {
  const section = sources?.[key];
  return section && typeof section === 'object' ? section : {};
}

/** The editable draft for every known category, read from stored filters. */
export function draftFromAlertSources(sources: AlertSources | null | undefined): AlertSourcesDraft {
  const draft: AlertSourcesDraft = {};
  for (const category of ALERT_CATEGORIES) {
    const section = sectionOf(sources, category.key);
    const ids = category.idsField ? section[category.idsField] : undefined;
    draft[category.key] = {
      enabled: section.enabled !== false,
      filterMode: section.filter_mode ?? 'all',
      ids: Array.isArray(ids) ? ids : [],
      minFailures: typeof section.min_failures === 'number' ? section.min_failures : DEFAULT_MIN_FAILURES,
    };
  }
  return draft;
}

/** Whether an enabled category sends only part of its alerts. */
function isNarrowed(category: AlertCategoryInfo, entry: CategoryDraft): boolean {
  if (!entry.enabled) return false;
  return (category.kind === 'entities' && entry.filterMode !== 'all')
    || (category.kind === 'threshold' && entry.minFailures !== DEFAULT_MIN_FAILURES);
}

/**
 * The `alert_sources` payload for a draft. Only categories that differ from
 * "send everything" get a section, and sections this UI does not know are
 * kept from `original`. Returns `{}` when nothing is filtered, which the
 * backend stores as NULL (send all).
 */
export function alertSourcesFromDraft(draft: AlertSourcesDraft, original?: AlertSources | null): AlertSources {
  const known = new Set(ALERT_CATEGORIES.map((c) => c.key));
  const result: AlertSources = {};
  for (const [key, value] of Object.entries(original ?? {})) {
    if (key !== 'version' && !known.has(key)) result[key] = value;
  }
  for (const category of ALERT_CATEGORIES) {
    const entry = draft[category.key];
    if (!entry) continue;
    const section: AlertSourceSection = {};
    if (!entry.enabled) section.enabled = false;
    if (isNarrowed(category, entry)) {
      if (category.kind === 'entities' && category.idsField) {
        section.filter_mode = entry.filterMode;
        section[category.idsField] = entry.ids;
      } else {
        section.min_failures = entry.minFailures;
      }
    }
    if (Object.keys(section).length > 0) result[category.key] = section;
  }
  if (Object.keys(result).length > 0) result.version = 1;
  return result;
}

export type SeverityFlag = 'notify_error' | 'notify_warning' | 'notify_success' | 'notify_info';

/** The four per-destination severity toggles, most severe first. */
export const SEVERITIES: ReadonlyArray<readonly [SeverityFlag, string]> = [
  ['notify_error', 'Errors'],
  ['notify_warning', 'Warnings'],
  ['notify_success', 'Successes'],
  ['notify_info', 'Info'],
];

/** One-line routing summary for an alert method list row. */
export function summarizeRouting(method: AlertMethod): string {
  const severities = SEVERITIES.filter(([flag]) => method[flag]).map(([, label]) => label.toLowerCase());
  let severityText = 'All severities';
  if (severities.length === 0) {
    severityText = 'No severities';
  } else if (severities.length < SEVERITIES.length) {
    const joined = severities.join(', ');
    severityText = joined.charAt(0).toUpperCase() + joined.slice(1);
  }
  const draft = draftFromAlertSources(method.alert_sources);
  const off = ALERT_CATEGORIES.filter((c) => !draft[c.key].enabled).length;
  const narrowed = ALERT_CATEGORIES.filter((c) => isNarrowed(c, draft[c.key])).length;
  const parts = [severityText];
  if (off > 0) parts.push(`${off} ${off === 1 ? 'category' : 'categories'} off`);
  if (narrowed > 0) parts.push(`${narrowed} filtered`);
  if (off === 0 && narrowed === 0) parts.push('all categories');
  return parts.join(' · ');
}
