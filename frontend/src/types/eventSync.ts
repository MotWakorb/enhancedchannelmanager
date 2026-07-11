/**
 * TypeScript types for Event Sync (epic ti939) — Phase 1A preview-only.
 *
 * Mirrors the backend contract:
 * - config shape: `validate_event_sync_config` in
 *   `backend/channel_pipeline_schema.py` (see docs/event_sync.md)
 * - preview response: `POST /api/channel-pipeline/event-sync-preview` in
 *   `backend/routers/channel_pipeline.py` (see docs/api.md)
 */

// =============================================================================
// Rule configuration (event_sync_config)
// =============================================================================

/**
 * One parse-pattern variant (title/time/date regexes with named capture
 * groups). Same shape as `DEFAULT_EVENT_PATTERNS` in
 * `backend/services/event_sync_matcher.py`.
 */
export interface EventSyncPattern {
  name?: string;
  title_pattern: string;
  time_pattern?: string;
  date_pattern?: string;
}

/**
 * The event_sync rule kind's config. A rule carrying this JSON object IS an
 * event_sync rule; its conditions/actions are placeholders ignored by the
 * engine (docs/event_sync.md).
 */
export interface EventSyncConfig {
  master_group_id: number;
  secondary_group_ids: number[];
  /** Shared pattern variants; omit to use the matcher's built-in defaults. */
  patterns?: EventSyncPattern[];
  /** Per-group overrides keyed by group ID (JSON object keys are strings). */
  group_patterns?: Record<string, EventSyncPattern[]>;
  /** 1..1440; backend default 30. */
  time_window_minutes?: number;
  /** Hard-clamped >= 0.80 by the backend schema; backend default 0.80. */
  attach_threshold?: number;
  enabled?: boolean;
}

// =============================================================================
// Preview response
// =============================================================================

/** Confidence band of one scored candidate (never rendered as color alone). */
export type EventSyncBand = 'attach' | 'ambiguous' | 'reject';

/** Team-token verdict of one scored candidate. */
export type EventSyncTeamVerdict = 'agree' | 'conflict' | 'uncertain' | 'absent';

/** Exactly one disposition per secondary stream; the four sum to the total. */
export type EventSyncDisposition =
  | 'would_attach'
  | 'ambiguous'
  | 'unmatched'
  | 'parse_failed';

export interface EventSyncPreflightFailure {
  group_id: number;
  role: 'master' | 'secondary';
  check: string;
  expected: string;
  got: string;
  message: string;
}

export interface EventSyncPreflight {
  ok: boolean;
  failures: EventSyncPreflightFailure[];
}

export interface EventSyncPreviewSummary {
  secondary_streams: number;
  would_attach: number;
  ambiguous_skipped: number;
  unmatched: number;
  parse_failed: number;
  master_channels: number;
  master_channels_unparsed: number;
}

export interface EventSyncCandidate {
  master_channel_name: string;
  master_channel_id: number | null;
  master_parsed_title: string | null;
  master_parsed_start: string | null;
  score: number;
  band: EventSyncBand;
  team_verdict: EventSyncTeamVerdict;
  time_delta_minutes: number;
  reject_reason: string | null;
}

export interface EventSyncStreamRow {
  stream_id: number | null;
  stream_name: string;
  group_id: number;
  provider: string | null;
  parsed_title: string | null;
  parsed_start: string | null;
  matched_pattern: string | null;
  disposition: EventSyncDisposition;
  unmatchable_reason: string | null;
  would_attach_master: { channel_id: number | null; name: string } | null;
  candidates: EventSyncCandidate[];
}

export interface EventSyncUnmatchedStream {
  stream_id: number | null;
  stream_name: string;
  group_id: number;
  provider: string | null;
  parsed_title: string | null;
  parsed_start: string | null;
  best_candidate: {
    master_channel_name: string;
    score: number;
    band: EventSyncBand;
    reject_reason: string | null;
  } | null;
}

export interface EventSyncParseFailureGroup {
  group_id: number;
  group_name: string | null;
  reason: string | null;
  count: number;
  stream_names: string[];
}

export interface EventSyncPreviewResponse {
  preflight: EventSyncPreflight;
  summary: EventSyncPreviewSummary;
  streams: EventSyncStreamRow[];
  unmatched_streams: EventSyncUnmatchedStream[];
  parse_failures: EventSyncParseFailureGroup[];
  unparsed_master_channels: string[];
  truncated: boolean;
}

/** Request body: exactly one of rule_id / event_sync_config. */
export type EventSyncPreviewRequest =
  | { rule_id: number }
  | { event_sync_config: EventSyncConfig };
