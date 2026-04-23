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

/** URL for the debug bundle download (used directly with fetch). */
export function getDebugBundleUrl(): string {
  return `${API_BASE}/auto-creation/debug-bundle`;
}
