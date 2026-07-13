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
/** One provider-scoped group: a group id + optional M3U account (provider) id
 *  (null/absent = the whole group / any provider). bead 3p2af. */
export interface EventSyncGroupScope {
  group_id: number;
  m3u_account_id: number | null;
}

export interface EventSyncConfig {
  /**
   * bead 3p2af / 38dzi: canonical provider-scoped shape. The editor (P4)
   * reads and writes these nested scopes; the backend validator derives the
   * flat `master_group_id` / `secondary_group_ids` from them, so a stored
   * config carries both. The flat keys are OPTIONAL on the client: the editor
   * no longer emits them (the backend fills them), but they remain readable on
   * a fetched config and are the migration fallback for a legacy rule authored
   * before the nested shape existed.
   */
  master?: EventSyncGroupScope;
  secondary?: EventSyncGroupScope[];
  master_group_id?: number;
  secondary_group_ids?: number[];
  /** Shared pattern variants; omit to use the matcher's built-in defaults. */
  patterns?: EventSyncPattern[];
  /** Per-group overrides keyed by group ID (JSON object keys are strings). */
  group_patterns?: Record<string, EventSyncPattern[]>;
  /** 1..1440; backend default 30. */
  time_window_minutes?: number;
  /**
   * Enforce the time-window candidacy gate (bead krkm4). Backend default
   * true — parsed start times must be within ±time_window_minutes to become
   * candidate pairs. Set false to disable the gate entirely: streams match
   * on title/team score alone, ignoring time. Safe only for a
   * single-provider, same-day master group — recurring/serial titles risk
   * cross-day matches. The 0.90 no-teams floor and team/numeric rails still
   * route borderline pairs to the review queue.
   */
  enforce_time_window?: boolean;
  /** Hard-clamped >= 0.80 by the backend schema; backend default 0.80. */
  attach_threshold?: number;
  /**
   * Per-run attach cap (1..1000; backend default 100) — Phase 1B blast-radius
   * control (bead ti939.2.1). No editor UI yet; preserved on save so an
   * API-set value survives a UI edit.
   */
  max_attach_per_run?: number;
  enabled?: boolean;
  /**
   * Phase 2 opt-in (bead ti939.3.1): when true, the rule also runs
   * UNATTENDED from the refresh-watermark task. Backend default false —
   * absent means manual-run-only.
   */
  auto_run?: boolean;
  /**
   * Phase 2 (bead ti939.3.3): dummy EPG profile auto-assigned to master
   * event channels on every run. OPTIONAL — absent means off (the backend
   * never default-fills it). Must reference an existing dummy EPG profile.
   */
  dummy_epg_profile_id?: number;
  /**
   * bead 6xxmp: when true, the MASTER group's own streams are also matched
   * to the master channels (the resolver drops those already attached). The
   * sanctioned path for a single channel group (global, unique-by-name)
   * carrying two providers' streams — one auto-synced, one not — so the
   * unsynced provider's streams attach to the synced provider's channels.
   * Backend default false.
   */
  include_master_group_streams?: boolean;
  /**
   * bead assume-current-date: when true, a listing that carries a time but
   * NO date (a dateless "today's schedule") is placed on the CURRENT date so
   * it becomes matchable — deliberately relaxing the never-guess-the-date
   * rail, accepting the cross-day match risk. Backend default false.
   */
  assume_current_date?: boolean;
  /**
   * bead parse-from-stream: when true, each master channel's event identity
   * (title + time) is read from its FIRST attached stream's name instead of
   * the channel name — so master channels can be named freely. Backend
   * default false.
   */
  parse_master_from_stream?: boolean;
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
  /**
   * ti939.3.2: subset of `would_attach` reached via a prior review-queue
   * accept (fingerprint-keyed decision) rather than the score threshold.
   */
  would_attach_via_review: number;
  /** ti939.3.2: rendered candidate pairings currently pending review. */
  candidates_pending_review: number;
}

/**
 * ti939.3.2: review-queue state of one exact (stream, master) pairing —
 * `'pending'` (awaiting the operator), `'accepted'` / `'rejected'`
 * (durable fingerprint-keyed decision), or `null` (never queued).
 */
export type EventSyncReviewStatus = 'pending' | 'accepted' | 'rejected' | null;

/**
 * ti939.3.2: how a `would_attach` disposition was reached — the matcher's
 * own score admission or a prior operator accept from the review queue.
 */
export type EventSyncAttachSource = 'threshold' | 'review_queue' | null;

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
  review_status: EventSyncReviewStatus;
}

/**
 * S5 (bead sf8dj): diagnostic provenance for a `would_attach` row — the
 * optional relaxation (`key`) that admitted it, with a short display `label`.
 * Keys: `assume_current_date` | `time_window_ignored` | `lowered_threshold` |
 * `master_from_stream`. Empty for a plain in-window default-threshold match.
 */
export interface EventSyncMatchedVia {
  key: string;
  label: string;
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
  attach_source: EventSyncAttachSource;
  would_attach_master: { channel_id: number | null; name: string } | null;
  candidates: EventSyncCandidate[];
  /** S5 (bead sf8dj): provenance chips for a would-attach row (may be absent
   * on older payloads → treat as empty). */
  matched_via?: EventSyncMatchedVia[];
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

// =============================================================================
// Review queue (bead ti939.3.2) — /api/event-sync-reviews
// =============================================================================

/**
 * Display-only evidence snapshot on one review row. Everything the operator
 * needs to judge the pairing without an opaque aggregate score: both raw
 * names, both parsed identities, per-candidate score/band/verdict/delta.
 * The snapshot channel/stream ids are NOT identity — the backend re-verifies
 * them against live Dispatcharr before any use (fingerprints are the key).
 */
export interface EventSyncReviewEvidence {
  rule_name?: string;
  stream_name?: string;
  provider?: string | null;
  secondary_group_id?: number;
  stream_id?: number | null;
  stream_parsed_title?: string | null;
  stream_parsed_start?: string | null;
  master_channel_name?: string;
  master_channel_id?: number | null;
  master_parsed_title?: string | null;
  master_parsed_start?: string | null;
  score?: number;
  band?: EventSyncBand;
  team_verdict?: EventSyncTeamVerdict;
  time_delta_minutes?: number;
  ambiguous_reason?: string | null;
}

/**
 * One event_sync_reviews row, as projected by `GET /api/event-sync-reviews`.
 * Identity is the content fingerprint (`rule_id`, `provider_id`,
 * `stream_name_hash`, `event_key`) — never channel/stream IDs — so decisions
 * survive Dispatcharr refreshes (epic ti939.3 keying constraint).
 */
export interface EventSyncReviewRecord {
  id: number;
  rule_id: number;
  provider_id: number;
  stream_name_hash: string;
  event_key: string;
  status: 'pending' | 'accepted' | 'rejected' | 'superseded';
  created_at: number;
  last_seen_at: number;
  resolved_at: number | null;
  resolution_source: string | null;
  evidence: EventSyncReviewEvidence;
}

/** Paginated envelope for `GET /api/event-sync-reviews`. */
export interface EventSyncReviewsListResponse {
  reviews: EventSyncReviewRecord[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/** Flat outcome for `POST /api/event-sync-reviews/{id}/accept`. */
export interface AcceptEventSyncReviewOutcome {
  status: 'accepted';
  attached: boolean;
  already_attached: boolean;
  attach_deferred_reason: string | null;
  superseded_siblings: number;
}

/** Flat outcome for `POST /api/event-sync-reviews/{id}/reject`. */
export interface RejectEventSyncReviewOutcome {
  status: 'rejected';
}
