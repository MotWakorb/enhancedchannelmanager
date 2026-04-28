/**
 * API service for Auto-Creation Pipeline.
 *
 * Provides functions for managing auto-creation rules, executions, and YAML import/export.
 */
import type {
  AutoCreationRule,
  CreateRuleData,
  UpdateRuleData,
  BulkUpdateRulesPatch,
  BulkUpdateRulesResponse,
  RulesListResponse,
  ExecutionsListResponse,
  AutoCreationExecution,
  ValidationResult,
  RunPipelineEnqueuedResponse,
  RollbackResponse,
  SchemaResponse,
  ConditionSchema,
  ActionSchema,
  TemplateVariableSchema,
  YAMLImportResponse,
} from '../types/autoCreation';
import { fetchJson as _fetchJson, fetchText as _fetchText, buildQuery } from './httpClient';

const API_BASE = '/api';

// Wrap shared utilities with auto-creation log prefix
function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  return _fetchJson<T>(url, options, 'Auto-Creation API');
}

function fetchText(url: string, options?: RequestInit): Promise<string> {
  return _fetchText(url, options, 'Auto-Creation API');
}

// =============================================================================
// Rules CRUD
// =============================================================================

/**
 * Get all auto-creation rules.
 */
export async function getAutoCreationRules(): Promise<AutoCreationRule[]> {
  const response = await fetchJson<RulesListResponse>(`${API_BASE}/auto-creation/rules`);
  return response.rules;
}

/**
 * Get a single auto-creation rule by ID.
 */
export async function getAutoCreationRule(id: number): Promise<AutoCreationRule> {
  return fetchJson<AutoCreationRule>(`${API_BASE}/auto-creation/rules/${id}`);
}

/**
 * Create a new auto-creation rule.
 */
export async function createAutoCreationRule(data: CreateRuleData): Promise<AutoCreationRule> {
  return fetchJson<AutoCreationRule>(`${API_BASE}/auto-creation/rules`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update an existing auto-creation rule.
 */
export async function updateAutoCreationRule(id: number, data: UpdateRuleData): Promise<AutoCreationRule> {
  return fetchJson<AutoCreationRule>(`${API_BASE}/auto-creation/rules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/**
 * Delete an auto-creation rule.
 */
export async function deleteAutoCreationRule(id: number): Promise<void> {
  await fetchJson<{ status: string }>(`${API_BASE}/auto-creation/rules/${id}`, {
    method: 'DELETE',
  });
}

/**
 * Toggle the enabled state of a rule.
 */
export async function toggleAutoCreationRule(id: number): Promise<AutoCreationRule> {
  return fetchJson<AutoCreationRule>(`${API_BASE}/auto-creation/rules/${id}/toggle`, {
    method: 'POST',
  });
}

/**
 * Apply the same settings changes to multiple rules. Only include fields to change.
 */
export async function bulkUpdateAutoCreationRules(
  ruleIds: number[],
  patch: BulkUpdateRulesPatch
): Promise<BulkUpdateRulesResponse> {
  return fetchJson<BulkUpdateRulesResponse>(`${API_BASE}/auto-creation/rules/bulk-update`, {
    method: 'POST',
    body: JSON.stringify({ rule_ids: ruleIds, ...patch }),
  });
}

// =============================================================================
// Validation & Schema
// =============================================================================

/**
 * Validate a rule's conditions and actions.
 */
export async function validateAutoCreationRule(data: {
  conditions: object[];
  actions: object[];
}): Promise<ValidationResult> {
  return fetchJson<ValidationResult>(`${API_BASE}/auto-creation/validate`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Get the condition schema (available condition types and their parameters).
 */
export async function getConditionSchema(): Promise<ConditionSchema[]> {
  const response = await fetchJson<SchemaResponse>(`${API_BASE}/auto-creation/schema/conditions`);
  return response.conditions || [];
}

/**
 * Get the action schema (available action types and their parameters).
 */
export async function getActionSchema(): Promise<ActionSchema[]> {
  const response = await fetchJson<SchemaResponse>(`${API_BASE}/auto-creation/schema/actions`);
  return response.actions || [];
}

/**
 * Get available template variables.
 */
export async function getTemplateVariables(): Promise<TemplateVariableSchema[]> {
  const response = await fetchJson<SchemaResponse>(`${API_BASE}/auto-creation/schema/template-variables`);
  return response.variables || [];
}

// =============================================================================
// Execution
// =============================================================================

/**
 * Enqueue an auto-creation pipeline run (bd-enfsy: 202+poll background-task pattern).
 *
 * The handler now returns ``202 Accepted`` with ``{ execution_id, status: 'running' }``
 * after queuing the work. Callers (see ``useAutoCreationExecution.runPipeline``)
 * are expected to poll ``getAutoCreationExecution(execution_id)`` until
 * ``status`` is terminal (``completed`` / ``failed`` / ``rolled_back``).
 */
export async function runAutoCreationPipeline(options?: {
  dryRun?: boolean;
  ruleIds?: number[];
}): Promise<RunPipelineEnqueuedResponse> {
  return fetchJson<RunPipelineEnqueuedResponse>(`${API_BASE}/auto-creation/run`, {
    method: 'POST',
    body: JSON.stringify({
      dry_run: options?.dryRun ?? false,
      rule_ids: options?.ruleIds,
    }),
  });
}

/**
 * Enqueue a single-rule auto-creation run (bd-enfsy 202+poll, see
 * ``runAutoCreationPipeline`` for the contract).
 */
export async function runAutoCreationRule(
  ruleId: number,
  options?: { dryRun?: boolean }
): Promise<RunPipelineEnqueuedResponse> {
  const query = buildQuery({ dry_run: options?.dryRun });
  return fetchJson<RunPipelineEnqueuedResponse>(
    `${API_BASE}/auto-creation/rules/${ruleId}/run${query}`,
    { method: 'POST' }
  );
}

/**
 * Get execution history.
 */
export async function getAutoCreationExecutions(params?: {
  limit?: number;
  offset?: number;
  status?: string;
}): Promise<ExecutionsListResponse> {
  const query = buildQuery({
    limit: params?.limit,
    offset: params?.offset,
    status: params?.status,
  });
  return fetchJson<ExecutionsListResponse>(`${API_BASE}/auto-creation/executions${query}`);
}

/**
 * Get a single execution by ID.
 */
export async function getAutoCreationExecution(id: number): Promise<AutoCreationExecution> {
  return fetchJson<AutoCreationExecution>(`${API_BASE}/auto-creation/executions/${id}`);
}

/**
 * Get full execution details including entities and execution log.
 */
export async function getExecutionDetails(id: number): Promise<AutoCreationExecution> {
  return fetchJson<AutoCreationExecution>(
    `${API_BASE}/auto-creation/executions/${id}?include_entities=true&include_log=true`
  );
}

/**
 * Rollback an execution.
 */
export async function rollbackAutoCreationExecution(id: number): Promise<RollbackResponse> {
  return fetchJson<RollbackResponse>(`${API_BASE}/auto-creation/executions/${id}/rollback`, {
    method: 'POST',
  });
}

// =============================================================================
// YAML Import/Export
// =============================================================================

/**
 * Export all rules as YAML.
 */
export async function exportAutoCreationRulesYAML(): Promise<string> {
  return fetchText(`${API_BASE}/auto-creation/export/yaml`);
}

/**
 * Import rules from YAML.
 */
export async function importAutoCreationRulesYAML(
  yamlContent: string,
  overwrite?: boolean
): Promise<YAMLImportResponse> {
  return fetchJson<YAMLImportResponse>(`${API_BASE}/auto-creation/import/yaml`, {
    method: 'POST',
    body: JSON.stringify({
      yaml_content: yamlContent,
      overwrite: overwrite ?? false,
    }),
  });
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
  return fetchJson<DebugBundleEnqueuedResponse>(`${API_BASE}/auto-creation/debug-bundle`, {
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
  const url = `${API_BASE}/auto-creation/debug-bundle/${encodeURIComponent(jobId)}`;

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
