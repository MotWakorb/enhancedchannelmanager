/**
 * API service for the Channel Pipeline.
 *
 * Provides functions for managing channel pipeline rules, executions, and YAML import/export.
 */
import type {
  ChannelPipelineRule,
  CreateRuleData,
  UpdateRuleData,
  BulkUpdateRulesPatch,
  BulkUpdateRulesResponse,
  AnalyzeRuleBodyRequest,
  RuleAnalyzerResponse,
  RulesListResponse,
  ExecutionsListResponse,
  ChannelPipelineExecution,
  ValidationResult,
  RunPipelineEnqueuedResponse,
  RollbackResponse,
  RestoreSnapshotResponse,
  SchemaResponse,
  ConditionSchema,
  ActionSchema,
  TemplateVariableSchema,
  YAMLImportResponse,
} from '../types/channelPipeline';
import type {
  EventSyncPreviewRequest,
  EventSyncPreviewResponse,
} from '../types/eventSync';
import { fetchJson as _fetchJson, fetchText as _fetchText, buildQuery } from './httpClient';

const API_BASE = '/api';

// Wrap shared utilities with channel pipeline log prefix
function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  return _fetchJson<T>(url, options, 'Channel Pipeline API');
}

function fetchText(url: string, options?: RequestInit): Promise<string> {
  return _fetchText(url, options, 'Channel Pipeline API');
}

// =============================================================================
// Rules CRUD
// =============================================================================

/**
 * Get all channel pipeline rules.
 */
export async function getChannelPipelineRules(): Promise<ChannelPipelineRule[]> {
  const response = await fetchJson<RulesListResponse>(`${API_BASE}/channel-pipeline/rules`);
  return response.rules;
}

/**
 * Get a single channel pipeline rule by ID.
 */
export async function getChannelPipelineRule(id: number): Promise<ChannelPipelineRule> {
  return fetchJson<ChannelPipelineRule>(`${API_BASE}/channel-pipeline/rules/${id}`);
}

/**
 * Create a new channel pipeline rule.
 */
export async function createChannelPipelineRule(data: CreateRuleData): Promise<ChannelPipelineRule> {
  return fetchJson<ChannelPipelineRule>(`${API_BASE}/channel-pipeline/rules`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update an existing channel pipeline rule.
 */
export async function updateChannelPipelineRule(id: number, data: UpdateRuleData): Promise<ChannelPipelineRule> {
  return fetchJson<ChannelPipelineRule>(`${API_BASE}/channel-pipeline/rules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a channel pipeline rule.
 */
export async function deleteChannelPipelineRule(id: number): Promise<void> {
  await fetchJson<{ status: string }>(`${API_BASE}/channel-pipeline/rules/${id}`, {
    method: 'DELETE',
  });
}

/**
 * Toggle the enabled state of a rule.
 */
export async function toggleChannelPipelineRule(id: number): Promise<ChannelPipelineRule> {
  return fetchJson<ChannelPipelineRule>(`${API_BASE}/channel-pipeline/rules/${id}/toggle`, {
    method: 'POST',
  });
}

/**
 * Apply the same settings changes to multiple rules. Only include fields to change.
 */
export async function bulkUpdateChannelPipelineRules(
  ruleIds: number[],
  patch: BulkUpdateRulesPatch
): Promise<BulkUpdateRulesResponse> {
  return fetchJson<BulkUpdateRulesResponse>(`${API_BASE}/channel-pipeline/rules/bulk-update`, {
    method: 'POST',
    body: JSON.stringify({ rule_ids: ruleIds, ...patch }),
  });
}

// =============================================================================
// Rule analyzer (advisory — never gates a save)
// =============================================================================

/**
 * Analyze an UNSAVED rule body and return advisory findings without saving it.
 *
 * Backs live authoring feedback in the rule builder. The rule is NOT persisted;
 * findings are warnings/info only and never block a save (bd-0gntx contract).
 * Reuses the same analyzer as the saved-rule and from-bundle endpoints, so the
 * response shape is identical. Debouncing is the caller's responsibility
 * (bd-m1s38.3 owns the rail + debounce).
 */
export async function analyzeChannelPipelineRuleBody(
  body: AnalyzeRuleBodyRequest
): Promise<RuleAnalyzerResponse> {
  return fetchJson<RuleAnalyzerResponse>(`${API_BASE}/channel-pipeline/rules/analyze-body`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// =============================================================================
// Event Sync preview (epic ti939, Phase 1A — preview only)
// =============================================================================

/**
 * Dry-run event matching against live master channels — ZERO writes.
 *
 * Accepts either a saved rule id or an inline event_sync_config (so the rule
 * editor can preview before saving). The backend never mutates channels,
 * never merges streams, and never toggles Dispatcharr group settings.
 * A pre-flight failure does NOT fail the preview — it is surfaced in the
 * response's `preflight.failures` alongside the match results.
 */
export async function previewEventSync(
  request: EventSyncPreviewRequest
): Promise<EventSyncPreviewResponse> {
  return fetchJson<EventSyncPreviewResponse>(`${API_BASE}/channel-pipeline/event-sync-preview`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

// =============================================================================
// Validation & Schema
// =============================================================================

/**
 * Validate a rule's conditions and actions.
 */
export async function validateChannelPipelineRule(data: {
  conditions: object[];
  actions: object[];
}): Promise<ValidationResult> {
  return fetchJson<ValidationResult>(`${API_BASE}/channel-pipeline/validate`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Get the condition schema (available condition types and their parameters).
 */
export async function getConditionSchema(): Promise<ConditionSchema[]> {
  const response = await fetchJson<SchemaResponse>(`${API_BASE}/channel-pipeline/schema/conditions`);
  return response.conditions || [];
}

/**
 * Get the action schema (available action types and their parameters).
 */
export async function getActionSchema(): Promise<ActionSchema[]> {
  const response = await fetchJson<SchemaResponse>(`${API_BASE}/channel-pipeline/schema/actions`);
  return response.actions || [];
}

/**
 * Get available template variables.
 */
export async function getTemplateVariables(): Promise<TemplateVariableSchema[]> {
  const response = await fetchJson<SchemaResponse>(`${API_BASE}/channel-pipeline/schema/template-variables`);
  return response.variables || [];
}

// =============================================================================
// Execution
// =============================================================================

/**
 * Enqueue a channel pipeline run (bd-enfsy: 202+poll background-task pattern).
 *
 * The handler now returns ``202 Accepted`` with ``{ execution_id, status: 'running' }``
 * after queuing the work. Callers (see ``useChannelPipelineExecution.runPipeline``)
 * are expected to poll ``getChannelPipelineExecution(execution_id)`` until
 * ``status`` is terminal (``completed`` / ``failed`` / ``rolled_back``).
 */
export async function runChannelPipeline(options?: {
  dryRun?: boolean;
  ruleIds?: number[];
}): Promise<RunPipelineEnqueuedResponse> {
  return fetchJson<RunPipelineEnqueuedResponse>(`${API_BASE}/channel-pipeline/run`, {
    method: 'POST',
    body: JSON.stringify({
      dry_run: options?.dryRun ?? false,
      rule_ids: options?.ruleIds,
    }),
  });
}

/**
 * Enqueue a single-rule channel pipeline run (bd-enfsy 202+poll, see
 * ``runChannelPipeline`` for the contract).
 */
export async function runChannelPipelineRule(
  ruleId: number,
  options?: { dryRun?: boolean }
): Promise<RunPipelineEnqueuedResponse> {
  const query = buildQuery({ dry_run: options?.dryRun });
  return fetchJson<RunPipelineEnqueuedResponse>(
    `${API_BASE}/channel-pipeline/rules/${ruleId}/run${query}`,
    { method: 'POST' }
  );
}

/**
 * Get execution history.
 */
export async function getChannelPipelineExecutions(params?: {
  limit?: number;
  offset?: number;
  status?: string;
}): Promise<ExecutionsListResponse> {
  const query = buildQuery({
    limit: params?.limit,
    offset: params?.offset,
    status: params?.status,
  });
  return fetchJson<ExecutionsListResponse>(`${API_BASE}/channel-pipeline/executions${query}`);
}

/**
 * Get a single execution by ID.
 */
export async function getChannelPipelineExecution(id: number): Promise<ChannelPipelineExecution> {
  return fetchJson<ChannelPipelineExecution>(`${API_BASE}/channel-pipeline/executions/${id}`);
}

/**
 * Get full execution details including entities and execution log.
 */
export async function getExecutionDetails(id: number): Promise<ChannelPipelineExecution> {
  return fetchJson<ChannelPipelineExecution>(
    `${API_BASE}/channel-pipeline/executions/${id}?include_entities=true&include_log=true`
  );
}

/**
 * Rollback an execution.
 */
export async function rollbackChannelPipelineExecution(id: number): Promise<RollbackResponse> {
  return fetchJson<RollbackResponse>(`${API_BASE}/channel-pipeline/executions/${id}/rollback`, {
    method: 'POST',
  });
}

/**
 * Restore an execution from its pre-run snapshot (ADR-010 §D8).
 *
 * The ``confirm=true`` query param is the API-level acknowledgement of the
 * ADR-010 §D5 optimistic-overwrite warning — the UI MUST have shown the
 * warning before calling this. The backend enforces the param; calls without
 * it return 400.
 *
 * Returns ``RestoreSnapshotResponse`` with ``success``, ``removed_channels``,
 * ``restored_channels``, and a ``failed_channels`` list for partial failures.
 * A 200 with ``success: false`` is a partial-failure result — the operation
 * attempted every channel and at least one failed. The caller must NOT present
 * this as plain success.
 *
 * Status codes:
 *   - 200 — restore attempted (check ``success`` and ``failed_channels``).
 *   - 400 — ``confirm`` not set, or the execution is a dry-run / already reverted.
 *   - 404 — no snapshot for this execution (use ``/rollback`` instead).
 */
export async function restoreChannelPipelineSnapshot(id: number): Promise<RestoreSnapshotResponse> {
  return fetchJson<RestoreSnapshotResponse>(
    `${API_BASE}/channel-pipeline/executions/${id}/restore-snapshot?confirm=true`,
    { method: 'POST' },
  );
}

// =============================================================================
// YAML Import/Export
// =============================================================================

/**
 * Export all rules as YAML.
 */
export async function exportChannelPipelineRulesYAML(): Promise<string> {
  return fetchText(`${API_BASE}/channel-pipeline/export/yaml`);
}

/**
 * Import rules from YAML.
 */
export async function importChannelPipelineRulesYAML(
  yamlContent: string,
  overwrite?: boolean
): Promise<YAMLImportResponse> {
  return fetchJson<YAMLImportResponse>(`${API_BASE}/channel-pipeline/import/yaml`, {
    method: 'POST',
    body: JSON.stringify({
      yaml_content: yamlContent,
      overwrite: overwrite ?? false,
    }),
  });
}

// =============================================================================
// Circuit breaker (bd-exo4j / GH #473)
// =============================================================================

/**
 * State of the run-on-refresh circuit breaker.
 *
 * - disabled: true → the channel pipeline will NOT fire after M3U refresh.
 * - reason: 'abandoned_run' → tripped automatically by startup crash-sentinel;
 *   null → manually disabled by the user (or not disabled at all).
 */
export interface CircuitBreakerState {
  disabled: boolean;
  reason: 'abandoned_run' | null;
}

/**
 * Get the current run-on-refresh circuit-breaker state.
 */
export async function getCircuitBreakerState(): Promise<CircuitBreakerState> {
  return fetchJson<CircuitBreakerState>(`${API_BASE}/channel-pipeline/circuit-breaker`);
}

/**
 * Reset (clear) the run-on-refresh circuit breaker. Admin-only.
 *
 * Re-enables the post-refresh auto-fire chain. Returns ``was_disabled`` so the
 * caller can distinguish a no-op reset from an active one.
 */
export async function resetCircuitBreaker(): Promise<{ success: boolean; was_disabled: boolean; disabled: boolean }> {
  return fetchJson<{ success: boolean; was_disabled: boolean; disabled: boolean }>(
    `${API_BASE}/channel-pipeline/reset-circuit-breaker`,
    { method: 'POST' },
  );
}

// =============================================================================
// Debug bundle (bd-cns7j: 202+poll, replaces the old single-shot GET that
// timed out on large catalogs)
// =============================================================================

export interface DebugBundleEnqueuedResponse {
  job_id: string;
  status: 'running';
  message?: string;
}

interface DebugBundleStatusJson {
  job_id: string;
  status: 'running' | 'failed';
  error?: string;
}

/** Enqueue debug bundle generation; returns the job id. */
export async function startDebugBundle(): Promise<DebugBundleEnqueuedResponse> {
  return fetchJson<DebugBundleEnqueuedResponse>(`${API_BASE}/channel-pipeline/debug-bundle`, {
    method: 'POST',
  });
}

/**
 * Poll the debug-bundle job until the artifact is ready, then return it as a
 * Blob. Throws on failed status, 404, or signal abort.
 */
export async function pollDebugBundle(
  jobId: string,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  const POLL_INTERVAL_MS = 1000;
  const MAX_POLL_DURATION_MS = 30 * 60 * 1000;
  const startedAt = Date.now();
  const url = `${API_BASE}/channel-pipeline/debug-bundle/${encodeURIComponent(jobId)}`;

  while (true) {
    if (signal?.aborted) throw new Error('Debug bundle download cancelled');

    const response = await fetch(url, { credentials: 'include', signal });
    if (response.status === 404) {
      throw new Error('Debug bundle job not found (it may have expired)');
    }
    if (!response.ok) {
      throw new Error(`Debug bundle poll failed (${response.status})`);
    }

    const contentType = response.headers.get('Content-Type') || '';
    // Binary artifact → completed.
    if (!contentType.includes('application/json')) {
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition');
      const filename = disposition?.match(/filename="(.+)"/)?.[1] || 'ecm-debug-bundle.tar.gz';
      return { blob, filename };
    }

    const status = (await response.json()) as DebugBundleStatusJson;
    if (status.status === 'failed') {
      throw new Error(status.error || 'Debug bundle generation failed');
    }
    // status === 'running' → wait then poll again.
    if (Date.now() - startedAt > MAX_POLL_DURATION_MS) {
      throw new Error('Debug bundle generation timed out');
    }
    await new Promise<void>((resolve) => {
      const t = window.setTimeout(resolve, POLL_INTERVAL_MS);
      signal?.addEventListener('abort', () => {
        window.clearTimeout(t);
        resolve();
      }, { once: true });
    });
  }
}

/** Convenience: enqueue + poll + return the downloadable Blob. */
export async function generateAndFetchDebugBundle(
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  const enqueued = await startDebugBundle();
  return pollDebugBundle(enqueued.job_id, signal);
}

// =============================================================================
// Event Sync team-alias dictionary (bead ti939.4.2)
// =============================================================================

/**
 * One group of KNOWN-equivalent team spellings consulted by the Event Sync
 * matcher's team-token layer ("Man Utd" == "Manchester United" == "MUFC").
 * Defined here (with the endpoint pair below) rather than in
 * types/eventSync.ts to keep clear of the in-flight exclusions PR's edits
 * to that file; follows the CircuitBreakerState precedent above.
 */
export interface EventSyncTeamAliasGroup {
  terms: string[];
  note?: string | null;
}

export interface EventSyncTeamAliasesResponse {
  groups: EventSyncTeamAliasGroup[];
}

/** Get the operator team-alias dictionary. */
export async function getEventSyncTeamAliases(): Promise<EventSyncTeamAliasesResponse> {
  return fetchJson<EventSyncTeamAliasesResponse>(`${API_BASE}/event-sync/team-aliases`);
}

/**
 * Replace the operator team-alias dictionary (full-replace PUT; the backend
 * validates terms against the matcher's own normalization and journals the
 * change).
 */
export async function updateEventSyncTeamAliases(
  groups: EventSyncTeamAliasGroup[],
): Promise<EventSyncTeamAliasesResponse> {
  return fetchJson<EventSyncTeamAliasesResponse>(`${API_BASE}/event-sync/team-aliases`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ groups }),
  });
}
