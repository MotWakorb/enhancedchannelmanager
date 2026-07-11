/**
 * TypeScript types for the Channel Pipeline.
 *
 * These types mirror the backend schema and are used throughout the frontend
 * for type safety when working with channel pipeline rules, conditions, and actions.
 */
import type { EventSyncConfig } from './eventSync';

// =============================================================================
// Condition Types
// =============================================================================

/**
 * Available condition types that can be evaluated against streams.
 */
export type ConditionType =
  // Stream metadata conditions
  | 'stream_name_matches'
  | 'stream_name_contains'
  | 'stream_group_contains'
  | 'stream_group_matches'
  | 'stream_group_is'
  | 'tvg_id_exists'
  | 'tvg_id_matches'
  | 'logo_exists'
  | 'provider_is'
  | 'quality_min'
  | 'quality_max'
  | 'codec_is'
  | 'has_audio_tracks'
  // Channel conditions
  | 'has_channel'
  | 'channel_exists_with_name'
  | 'channel_exists_matching'
  | 'channel_in_group'
  | 'channel_has_streams'
  | 'normalized_name_in_group'
  | 'normalized_name_not_in_group'
  | 'normalized_name_exists'
  | 'normalized_name_not_exists'
  // Logical operators
  | 'and'
  | 'or'
  | 'not'
  // Special
  | 'always'
  | 'never';

/**
 * A condition to evaluate against a stream.
 */
export interface Condition {
  type: ConditionType;
  value?: string | number | boolean | string[] | number[];
  conditions?: Condition[]; // For AND/OR/NOT operators (legacy)
  connector?: 'and' | 'or'; // How this condition relates to the previous one
  case_sensitive?: boolean;
  negate?: boolean;
}

/**
 * Schema definition for a condition type (for UI generation).
 */
export interface ConditionSchema {
  type: ConditionType;
  label: string;
  description: string;
  category: 'stream' | 'channel' | 'logical' | 'special';
  value_type: 'string' | 'number' | 'boolean' | 'regex' | 'array' | 'none';
  value_label?: string;
  value_placeholder?: string;
  supports_negate?: boolean;
  supports_case_sensitive?: boolean;
}

// =============================================================================
// Action Types
// =============================================================================

/**
 * Available action types that can be executed.
 */
export type ActionType =
  | 'create_channel'
  | 'create_group'
  | 'merge_streams'
  | 'assign_logo'
  | 'assign_tvg_id'
  | 'assign_epg'
  | 'assign_profile'
  | 'assign_channel_profile'
  | 'set_channel_number'
  | 'set_variable'
  | 'remove_from_channel'
  | 'set_stream_priority'
  | 'probe_streams'
  | 'sort_group'
  | 'skip'
  | 'stop_processing'
  | 'log_match';

/**
 * Behavior when a channel/group already exists.
 */
export type IfExistsBehavior = 'skip' | 'merge' | 'merge_only' | 'update' | 'use_existing';

/**
 * An action to execute when conditions match.
 */
export interface Action {
  type: ActionType;
  name_template?: string;
  group_id?: number;
  if_exists?: IfExistsBehavior;
  channel_number?: string | number;
  value?: string;
  epg_id?: number;
  profile_id?: number;
  channel_profile_ids?: number[];
  target?: 'auto' | 'existing_channel' | 'new_channel';
  find_channel_by?: 'name_exact' | 'name_regex' | 'tvg_id';
  find_channel_value?: string;
  max_streams_per_channel?: number;
  /** When true, remove streams from the target channel that no longer match this rule run. */
  remove_non_matching?: boolean;
  /**
   * merge_streams target=auto matching mode (bd-0emgo.1). When false/undefined
   * (default), the stream merges into an existing channel only on EXACT
   * normalized-name equality. When true, restores the legacy fuzzy cascade
   * (core-name / deparen / word-prefix containment / call-sign).
   */
  loose_name_match?: boolean;
  /**
   * merge_streams TARGET-channel group filter (bd-0emgo.3). After the merge
   * target is resolved, the merge is SKIPPED if the resolved channel's group
   * is in this list. This is the "keep merges OUT of group N" guard — distinct
   * from the stream-side normalized_name_not_in_group *condition* (which only
   * gates whether the rule fires). Absent/empty = no filter (back-compat).
   */
  target_channel_not_in_group?: number[];
  /**
   * merge_streams TARGET-channel group filter complement (bd-0emgo.3). When
   * non-empty, only merges when the resolved channel's group IS in this list
   * ("only merge into group N"). Absent/empty = no restriction.
   */
  target_channel_in_group?: number[];
  message?: string;
  // Name transform (for create_channel and create_group)
  name_transform_pattern?: string;
  name_transform_replacement?: string;
  // Set variable
  variable_name?: string;
  variable_mode?: 'regex_extract' | 'regex_replace' | 'literal';
  source_field?: string;
  pattern?: string;
  replacement?: string;
  template?: string;
  // EPG assignment
  set_tvg_id?: boolean;
  // Stream priority
  priority?: 'lowest' | 'highest';
  /**
   * sort_group (enhancedchannelmanager-vy4fl) — GROUP-level post-run pass,
   * NOT per-stream: alphabetically sorts and renumbers every channel in a
   * group once per run, after all streams are processed. Sort order
   * ('asc'/'desc'), starting_number, strip_numbers, and ignore_country
   * mirror the manual Sort & Renumber modal exactly (shared semantics in
   * utils/channelSort.ts; backend port in
   * backend/channel_pipeline_sort.py). ``group_id`` (reusing the field
   * above) optionally overrides the group resolved from the stream's
   * current channel/group context or the rule's target_group_id.
   */
  order?: 'asc' | 'desc';
  starting_number?: number;
  strip_numbers?: boolean;
  ignore_country?: boolean;
}

/**
 * Schema definition for an action type (for UI generation).
 */
export interface ActionSchema {
  type: ActionType;
  label: string;
  description: string;
  category: 'creation' | 'assignment' | 'management' | 'control';
  params: ActionParamSchema[];
}

export interface ActionParamSchema {
  name: string;
  label: string;
  type: 'string' | 'number' | 'select' | 'template' | 'boolean';
  required?: boolean;
  default?: string | number | boolean;
  options?: { value: string; label: string }[];
  placeholder?: string;
}

// =============================================================================
// Template Variables
// =============================================================================

/**
 * Available template variables for name templates.
 */
export type TemplateVariable =
  | '{stream_name}'
  | '{stream_group}'
  | '{tvg_id}'
  | '{tvg_name}'
  | '{quality}'
  | '{quality_raw}'
  | '{provider}'
  | '{provider_id}'
  | '{normalized_name}';

export interface TemplateVariableSchema {
  name: TemplateVariable;
  description: string;
  example: string;
}

// =============================================================================
// Rules
// =============================================================================

/**
 * A channel pipeline rule.
 */
export interface ChannelPipelineRule {
  id: number;
  name: string;
  description?: string;
  enabled: boolean;
  priority: number;
  conditions: Condition[];
  actions: Action[];
  m3u_account_id?: number;
  target_group_id?: number;
  run_on_refresh: boolean;
  stop_on_first_match: boolean;
  sort_field?: string | null;
  sort_order?: 'asc' | 'desc';
  probe_on_sort?: boolean;
  sort_regex?: string | null;
  stream_sort_field?: string | null;
  stream_sort_order?: 'asc' | 'desc';
  /** When stream sort is Quality: tie-break equal resolution using M3U account priorities (same as Provider Order). */
  quality_tie_break_order?: 'asc' | 'desc';
  /** When false, equal-resolution streams keep probe/m3u-independent order (stream id tie-break only). */
  quality_m3u_tie_break_enabled?: boolean;
  normalization_group_ids?: number[];
  skip_struck_streams?: boolean;
  orphan_action?: 'delete' | 'move_uncategorized' | 'delete_and_cleanup_groups' | 'none';
  // When true, the executor's existing-channel name lookup during
  // create_channel is scoped to the rule's target group so two rules
  // targeting different groups can create separate channels with the
  // same name instead of merging into a foreign group (GH-92).
  match_scope_target_group?: boolean;
  // Explicit rule-level scope group for merge lookups (GH #298). When
  // match_scope_target_group is on, this pins the group that name lookups
  // are restricted to across both create_channel and merge_streams. null/
  // undefined = "Auto" (create_channel falls back to the action's target
  // group; merge_streams stays group-agnostic).
  match_scope_group_id?: number | null;
  // Manual-channel isolation opt-out (PR #547). Default false = hand-built
  // (manual) channels are protected: a matching manual merge target is
  // treated as "not found" (the block is journaled + shown in the execution
  // log) and the rule may create a new auto channel instead. True = this
  // rule may merge into manual channels; each adoption is journaled.
  allow_manual_channel_merge?: boolean;
  /**
   * Event Sync rule kind (epic ti939). Non-null = this rule IS an event_sync
   * rule: its conditions/actions are placeholders ignored by the engine, and
   * it is excluded from pipeline execution entirely in Phase 1A
   * (preview-only — see docs/event_sync.md).
   */
  event_sync_config?: EventSyncConfig | null;
  last_run_at?: string;
  match_count: number;
  created_at: string;
  updated_at: string;
}

/**
 * Data for creating a new rule.
 */
export interface CreateRuleData {
  name: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
  conditions: Condition[];
  actions: Action[];
  m3u_account_id?: number;
  target_group_id?: number;
  run_on_refresh?: boolean;
  stop_on_first_match?: boolean;
  sort_field?: string | null;
  sort_order?: string;
  probe_on_sort?: boolean;
  sort_regex?: string | null;
  stream_sort_field?: string | null;
  stream_sort_order?: string;
  quality_tie_break_order?: string;
  quality_m3u_tie_break_enabled?: boolean;
  normalization_group_ids?: number[];
  skip_struck_streams?: boolean;
  orphan_action?: string;
  match_scope_target_group?: boolean;
  // Explicit rule-level scope group for merge lookups (GH #298). null = "Auto".
  match_scope_group_id?: number | null;
  // Allow this rule to merge into hand-built manual channels (default false).
  allow_manual_channel_merge?: boolean;
  // Event Sync rule kind (epic ti939). Validated by the backend's
  // validate_event_sync_config at save time; null explicitly clears the kind.
  event_sync_config?: EventSyncConfig | null;
}

/**
 * Data for updating an existing rule.
 */
export type UpdateRuleData = Partial<CreateRuleData>;

/**
 * Bulk update payload (fields omitted are left unchanged on the server).
 * `merge_streams_remove_non_matching` applies to all `merge_streams` actions on each rule.
 */
export type BulkUpdateRulesPatch = UpdateRuleData & {
  merge_streams_remove_non_matching?: boolean;
};

/** Response from POST /channel-pipeline/rules/bulk-update */
export interface BulkUpdateRulesResponse {
  rules: ChannelPipelineRule[];
  updated_count: number;
}

// =============================================================================
// Execution
// =============================================================================

/**
 * Status of a pipeline execution.
 *
 * Terminal statuses: completed, failed, rolled_back, capped, abandoned.
 * - capped: the pipeline hit the per-run created-channel cap and stopped early.
 * - abandoned: the pipeline was abandoned (e.g. OOM crash); trips the
 *   run-on-refresh circuit breaker until an operator resets it.
 */
export type ExecutionStatus = 'running' | 'completed' | 'failed' | 'rolled_back' | 'capped' | 'abandoned';

/**
 * How a pipeline was triggered.
 */
export type ExecutionTrigger = 'manual' | 'scheduled' | 'm3u_refresh';

/**
 * Mode of execution.
 */
export type ExecutionMode = 'execute' | 'dry_run';

/**
 * A record of a pipeline execution.
 */
export interface ChannelPipelineExecution {
  id: number;
  mode: ExecutionMode;
  triggered_by: ExecutionTrigger;
  started_at: string;
  completed_at?: string;
  duration_seconds?: number;
  status: ExecutionStatus;
  streams_evaluated: number;
  streams_matched: number;
  channels_created: number;
  channels_updated: number;
  groups_created: number;
  streams_merged: number;
  streams_skipped: number;
  streams_excluded: number;
  error_message?: string;
  created_entities: CreatedEntity[];
  modified_entities: ModifiedEntity[];
  dry_run_results?: DryRunResult[];
  execution_log?: ExecutionLogEntry[];
  rolled_back_at?: string;
  rolled_back_by?: string;
  /**
   * True when a pre-run snapshot exists for this execution (ADR-010 §D6).
   * Derived by the backend from the existence of a ChannelPipelineSnapshot row;
   * gates the snapshot-restore affordance in the UI. False / absent for
   * dry-run executions, legacy runs, and runs where snapshot capture failed.
   */
  has_snapshot?: boolean;
  /**
   * Advisory, non-fatal run warnings (enhancedchannelmanager-e8p1h). Currently
   * carries rules that reference DISABLED/missing normalization groups, which
   * makes normalization silently apply nothing. Distinct from error_message —
   * the run still completes. Always present (empty array when none).
   */
  warnings?: NormalizationWarning[];
}

/**
 * A rule that references normalization groups that are disabled or missing, so
 * normalization applied no changes (enhancedchannelmanager-e8p1h).
 */
export interface NormalizationWarning {
  rule_id: number;
  rule_name: string;
  disabled_groups: DisabledNormalizationGroup[];
}

export interface DisabledNormalizationGroup {
  id: number;
  /** null when the group no longer exists (missing reference). */
  name: string | null;
  /** True when the referenced group id no longer exists. */
  missing: boolean;
}

export interface CreatedEntity {
  type: 'channel' | 'group';
  id: number;
  name: string;
}

export interface ModifiedEntity {
  type: 'channel' | 'group' | 'stream';
  id: number;
  name?: string;
  previous?: Record<string, unknown>;
}

export interface DryRunResult {
  stream_id: number;
  stream_name: string;
  rule_id: number;
  rule_name: string;
  action: string;
  would_create: boolean;
  would_modify: boolean;
}

// =============================================================================
// Execution Log (per-stream detail)
// =============================================================================

export interface ExecutionLogEntry {
  stream_id: number;
  stream_name: string;
  m3u_account_id?: number;
  rules_evaluated: RuleEvaluation[];
  actions_executed: ActionLogEntry[];
}

export interface RuleEvaluation {
  rule_id: number;
  rule_name: string;
  conditions: ConditionLogEntry[];
  matched: boolean;
  was_winner: boolean;
}

export interface ConditionLogEntry {
  type: string;
  value?: string;
  matched: boolean;
  details?: string;
  connector?: 'and' | 'or';
}

export interface ActionLogEntry {
  type: string;
  description: string;
  success: boolean;
  entity_id?: number;
  error?: string;
  details?: string[];
}

// =============================================================================
// Conflicts
// =============================================================================

/**
 * A conflict detected during execution.
 */
export interface ChannelPipelineConflict {
  id: number;
  execution_id: number;
  stream_id: number;
  stream_name: string;
  winning_rule_id: number;
  losing_rule_ids: number[];
  conflict_type: 'duplicate_match' | 'channel_exists' | 'group_exists';
  resolution: string;
  description: string;
  created_at: string;
}

// =============================================================================
// API Response Types
// =============================================================================

export interface RulesListResponse {
  rules: ChannelPipelineRule[];
}

export interface ExecutionsListResponse {
  executions: ChannelPipelineExecution[];
  total: number;
  page: number;
  page_size: number;
}

export interface ConflictsListResponse {
  conflicts: ChannelPipelineConflict[];
  total: number;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings?: string[];
}

/**
 * 202-Accepted response from POST /api/channel-pipeline/run and
 * POST /api/channel-pipeline/rules/{id}/run (bd-enfsy: background-task pattern).
 *
 * Pipeline runs are now enqueued and the caller polls
 * GET /api/channel-pipeline/executions/{id} until status is terminal.
 */
export interface RunPipelineEnqueuedResponse {
  execution_id: number;
  status: 'running';
  rule_id?: number;
  message: string;
}

/**
 * Result returned by the polling-based ``runPipeline`` hook helper —
 * the terminal ChannelPipelineExecution row once polling resolves, or
 * ``undefined`` if the request errored out before completion.
 */
export type RunPipelineResponse = ChannelPipelineExecution;

export interface RollbackResponse {
  success: boolean;
  entities_removed: number;
  entities_restored: number;
  error?: string;
}

/**
 * A channel that failed to restore during snapshot-restore (ADR-010 §D8 step 6).
 * Partial failures are always surfaced; a restore that fails on some channels
 * is never presented as plain success.
 */
export interface FailedChannel {
  id: number;
  name: string;
  error: string;
}

/**
 * Response from POST /channel-pipeline/executions/{id}/restore-snapshot (ADR-010 §D8).
 *
 * ``success`` is false when any channel failed (partial failures are surfaced
 * in ``failed_channels``). A 200 with ``success: false`` is a partial-failure
 * result, NOT a server error.
 */
export interface RestoreSnapshotResponse {
  success: boolean;
  removed_channels: number;
  restored_channels: number;
  failed_channels: FailedChannel[];
  error?: string;
}

export interface SchemaResponse {
  conditions?: ConditionSchema[];
  actions?: ActionSchema[];
  variables?: TemplateVariableSchema[];
}

export interface YAMLExportResponse {
  yaml: string;
}

export interface YAMLImportRequest {
  yaml_content: string;
  overwrite?: boolean;
}

export interface YAMLImportedItem {
  name: string;
  action: 'created' | 'updated';
}

export interface YAMLImportError {
  rule_index: number;
  rule_name: string;
  errors: string[];
}

export interface YAMLImportResponse {
  success: boolean;
  imported: YAMLImportedItem[];
  errors: YAMLImportError[];
  warnings?: string[];
}
