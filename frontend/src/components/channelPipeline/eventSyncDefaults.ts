/**
 * Event Sync shipped parse patterns + display metadata (bead ti939.1.5).
 *
 * The pattern set (regexes, examples, expected parses) lives in
 * `eventSyncShippedPatterns.json` — the single source of truth shared with
 * `backend/tests/services/test_event_sync_shipped_frontend_patterns.py`,
 * which runs every entry's example through the REAL matcher
 * (`parse_event_name`) and pins the expected title/start, plus verbatim
 * equality of the `builtin: true` entries against `DEFAULT_EVENT_PATTERNS`
 * in `backend/services/event_sync_matcher.py`. A shipped pattern that cannot
 * parse its own example fails the backend suite (PR #614 review blocker:
 * the original generic no-"@" variants let the slot-prefix branch consume
 * through the time's hour-colon, collapsing all same-minute events to one
 * garbage title).
 *
 * When the editor's selection is exactly the built-ins, the config omits the
 * `patterns` key entirely so the backend's own defaults apply and future
 * matcher improvements flow through without editing saved rules.
 *
 * All regexes use Python named-group syntax `(?P<name>...)`; the backend's
 * `extract_groups` accepts it directly (and converts JS-style itself).
 */
import type { EventSyncBand, EventSyncDisposition, EventSyncPattern, EventSyncTeamVerdict } from '../../types/eventSync';
import shippedPatternsFixture from './eventSyncShippedPatterns.json';

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

export const SHIPPED_EVENT_SYNC_PATTERNS: ShippedEventSyncPattern[] =
  shippedPatternsFixture.patterns.map(entry => ({
    id: entry.id,
    builtin: entry.builtin,
    label: entry.label,
    description: entry.description,
    example: entry.example,
    pattern: {
      name: entry.id,
      title_pattern: entry.title_pattern,
      time_pattern: entry.time_pattern,
      date_pattern: entry.date_pattern,
    },
  }));

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

/**
 * ti939.3.2: review-queue state of one candidate pairing (text + icon,
 * never color alone — same accessibility baseline as the other badges).
 */
export const REVIEW_STATUS_META: Record<
  'pending' | 'accepted' | 'rejected',
  { label: string; icon: string }
> = {
  pending: { label: 'Pending review', icon: 'pending_actions' },
  accepted: { label: 'Accepted (auto-attaches)', icon: 'task_alt' },
  rejected: { label: 'Rejected (suppressed)', icon: 'do_not_disturb_on' },
};
