/**
 * TypeScript types for the Auto-Creation Pipeline.
 *
 * These types mirror the backend schema and are used throughout the frontend
 * for type safety when working with auto-creation rules, conditions, and actions.
 */

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
  | 'set_channel_number'
  | 'set_variable'
  | 'skip'
  | 'stop_processing'
  | 'log_match';

/**
 * Behavior when a channel/group already exists.
 */
export type IfExistsBehavior = 'skip' | 'merge' | 'update' | 'use_existing';

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
  target?: 'auto' | 'existing_channel' | 'new_channel';
  find_channel_by?: 'name_exact' | 'name_regex' | 'tvg_id';
  find_channel_value?: string;
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
}

/**
 * Schema definition for an action type (for UI generation).
 */
export interface ActionSchema {
  type: ActionType;
  label: string;
  description: string;
  category: 'creation' | 'assignment' | 'control';
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
 * An auto-creation rule.
 */
export interface AutoCreationRule {
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
  normalize_names?: boolean;
  orphan_action?: 'delete' | 'move_uncategorized' | 'delete_and_cleanup_groups' | 'none';
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
  normalize_names?: boolean;
  orphan_action?: string;
}

/**
 * Data for updating an existing rule.
 */
export interface UpdateRuleData extends Partial<CreateRuleData> {}

// =============================================================================
// Execution
// =============================================================================

/**
 * Status of a pipeline execution.
 */
export type ExecutionStatus = 'running' | 'completed' | 'failed' | 'rolled_back';

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
export interface AutoCreationExecution {
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
  error_message?: string;
  created_entities: CreatedEntity[];
  modified_entities: ModifiedEntity[];
  dry_run_results?: DryRunResult[];
  execution_log?: ExecutionLogEntry[];
  rolled_back_at?: string;
  rolled_back_by?: string;
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
}

// =============================================================================
// Conflicts
// =============================================================================

/**
 * A conflict detected during execution.
 */
export interface AutoCreationConflict {
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
  rules: AutoCreationRule[];
}

export interface ExecutionsListResponse {
  executions: AutoCreationExecution[];
  total: number;
  page: number;
  page_size: number;
}

export interface ConflictsListResponse {
  conflicts: AutoCreationConflict[];
  total: number;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings?: string[];
}

export interface RunPipelineResponse {
  success: boolean;
  execution_id: number;
  mode: ExecutionMode;
  duration_seconds: number;
  streams_evaluated: number;
  streams_matched: number;
  channels_created: number;
  channels_updated: number;
  groups_created: number;
  streams_merged: number;
  streams_skipped: number;
  channels_removed: number;
  channels_moved: number;
  created_entities: CreatedEntity[];
  modified_entities: ModifiedEntity[];
  dry_run_results?: DryRunResult[];
  conflicts?: {
    stream_id: number;
    stream_name: string;
    winning_rule_id: number;
    losing_rule_ids: number[];
  }[];
}

export interface RollbackResponse {
  success: boolean;
  entities_removed: number;
  entities_restored: number;
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

export interface YAMLImportResponse {
  success: boolean;
  imported: string[];
  skipped: string[];
  errors: string[];
}
