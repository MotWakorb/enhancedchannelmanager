/**
 * Event Sync shipped parse patterns + display metadata (bead ti939.1.5).
 *
 * KEEP IN SYNC: the two `builtin: true` patterns below are verbatim copies of
 * `DEFAULT_EVENT_PATTERNS` in `backend/services/event_sync_matcher.py`
 * (same `name` strings, same regex text). When the editor's selection is
 * exactly the built-ins, the config omits the `patterns` key entirely so the
 * backend's own defaults apply and future matcher improvements flow through
 * without editing saved rules. The two generic (no "@" separator) variants
 * are frontend-shipped extras for providers that drop the "@" — they are sent
 * explicitly when selected.
 *
 * All regexes use Python named-group syntax `(?P<name>...)`; the backend's
 * `extract_groups` accepts it directly (and converts JS-style itself).
 */
import type { EventSyncBand, EventSyncDisposition, EventSyncPattern, EventSyncTeamVerdict } from '../../types/eventSync';

/** Mirror of EVENT_ATTACH_FLOOR in backend/services/event_sync_matcher.py. */
export const EVENT_ATTACH_FLOOR = 0.80;

/** Mirror of DEFAULT_TIME_WINDOW_MINUTES (backend default). */
export const DEFAULT_TIME_WINDOW_MINUTES = 30;

/** Backend schema ceiling for time_window_minutes (24 hours). */
export const MAX_TIME_WINDOW_MINUTES = 1440;

/**
 * Clamp an operator-entered attach threshold into the schema-legal range.
 * The 0.80 floor is a hard rail (precision over recall; 1,341-incident trust
 * benchmark) — the backend rejects anything below it, so the input never
 * offers an illegal value. Non-finite input falls back to the floor.
 */
export function clampAttachThreshold(value: number): number {
  if (!Number.isFinite(value)) return EVENT_ATTACH_FLOOR;
  return Math.min(1.0, Math.max(EVENT_ATTACH_FLOOR, value));
}

// --- Verbatim copies of the matcher's default regexes ----------------------

const TITLE_AT_DATE = String.raw`^(?:[^@:]{0,40}?(?<!\d)\d{2}\s*:\s*)?\s*(?P<title>.+?)\s*(?:@\s*(?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{1,2}:\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*,?\s*\d{4})?\s+\d{1,2}:\d{2}).*)?$`;

const TIME_PATTERN = String.raw`(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?\s*(?:E[SD]?T)?\s*$`;

const DATE_DAY_FIRST_AT = String.raw`@\s*(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\s+\d{1,2}:\d{2}`;

const DATE_MONTH_FIRST_AT = String.raw`@\s*(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:\s*,?\s*(?P<year>\d{4}))?\s+\d{1,2}:\d{2}`;

// --- Frontend-shipped generic variants (no "@" date delimiter) -------------

const TITLE_GENERIC = String.raw`^(?:[^@:]{0,40}?(?<!\d)\d{2}\s*:\s*)?\s*(?P<title>.+?)\s*(?:(?:\d{1,2}\s+[A-Za-z]{3,9}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*,?\s*\d{4})?)\s+\d{1,2}:\d{2}.*)?$`;

const DATE_DAY_FIRST_GENERIC = String.raw`(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\s+\d{1,2}:\d{2}`;

const DATE_MONTH_FIRST_GENERIC = String.raw`(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:\s*,?\s*(?P<year>\d{4}))?\s+\d{1,2}:\d{2}`;

/** One shipped pattern choice in the editor. */
export interface ShippedEventSyncPattern {
  /** Stable id used for selection state; equals pattern.name. */
  id: string;
  /** true = verbatim copy of a backend built-in default. */
  builtin: boolean;
  label: string;
  description: string;
  example: string;
  pattern: EventSyncPattern;
}

export const SHIPPED_EVENT_SYNC_PATTERNS: ShippedEventSyncPattern[] = [
  {
    id: 'slot-title-day-first-date',
    builtin: true,
    label: 'Title @ Day-first date (built-in)',
    description:
      'Optional "Slot NN :" prefix, then title, then "@ <day> <month> <time>". Matches the most common live shape.',
    example: 'Fubo Sports Network 07 : Yankees vs Red Sox @ 11 Jul 06:00 PM ET',
    pattern: {
      name: 'slot-title-day-first-date',
      title_pattern: TITLE_AT_DATE,
      time_pattern: TIME_PATTERN,
      date_pattern: DATE_DAY_FIRST_AT,
    },
  },
  {
    id: 'slot-title-month-first-date',
    builtin: true,
    label: 'Title @ Month-first date (built-in)',
    description:
      'Optional "Slot NN :" prefix, then title, then "@ <month> <day> [year] <time>".',
    example: 'Peacock 14: Lyon vs Marseille @ Jan 17 02:45 PM ET',
    pattern: {
      name: 'slot-title-month-first-date',
      title_pattern: TITLE_AT_DATE,
      time_pattern: TIME_PATTERN,
      date_pattern: DATE_MONTH_FIRST_AT,
    },
  },
  {
    id: 'title-day-first-date-no-at',
    builtin: false,
    label: 'Title Day-first date (no "@")',
    description:
      'For providers that drop the "@" separator: title followed directly by "<day> <month> <time>".',
    example: 'Yankees vs Red Sox 11 Jul 06:00 PM ET',
    pattern: {
      name: 'title-day-first-date-no-at',
      title_pattern: TITLE_GENERIC,
      time_pattern: TIME_PATTERN,
      date_pattern: DATE_DAY_FIRST_GENERIC,
    },
  },
  {
    id: 'title-month-first-date-no-at',
    builtin: false,
    label: 'Title Month-first date (no "@")',
    description:
      'For providers that drop the "@" separator: title followed directly by "<month> <day> [year] <time>".',
    example: 'Lyon vs Marseille Jan 17 02:45 PM ET',
    pattern: {
      name: 'title-month-first-date-no-at',
      title_pattern: TITLE_GENERIC,
      time_pattern: TIME_PATTERN,
      date_pattern: DATE_MONTH_FIRST_GENERIC,
    },
  },
];

/** The default selection = exactly the backend built-ins. */
export const DEFAULT_PATTERN_IDS = SHIPPED_EVENT_SYNC_PATTERNS
  .filter(p => p.builtin)
  .map(p => p.id);

/**
 * True when the selection is exactly the built-in defaults (order-insensitive)
 * — in that case the config omits `patterns` so the backend defaults apply.
 */
export function selectionIsBuiltinDefaults(selectedIds: string[]): boolean {
  return (
    selectedIds.length === DEFAULT_PATTERN_IDS.length &&
    DEFAULT_PATTERN_IDS.every(id => selectedIds.includes(id))
  );
}

// --- Display metadata (text label + icon — never color alone) --------------

export const BAND_META: Record<EventSyncBand, { label: string; icon: string }> = {
  attach: { label: 'Attach', icon: 'check_circle' },
  ambiguous: { label: 'Ambiguous', icon: 'help' },
  reject: { label: 'Reject', icon: 'cancel' },
};

export const DISPOSITION_META: Record<EventSyncDisposition, { label: string; icon: string }> = {
  would_attach: { label: 'Would attach', icon: 'check_circle' },
  ambiguous: { label: 'Ambiguous (skipped)', icon: 'help' },
  unmatched: { label: 'Unmatched', icon: 'search_off' },
  parse_failed: { label: 'Parse failure', icon: 'error_outline' },
};

export const TEAM_VERDICT_META: Record<EventSyncTeamVerdict, { label: string; icon: string }> = {
  agree: { label: 'Teams agree', icon: 'group' },
  conflict: { label: 'Team conflict (hard reject)', icon: 'block' },
  uncertain: { label: 'Teams uncertain', icon: 'help_outline' },
  absent: { label: 'No team tokens', icon: 'remove' },
};
