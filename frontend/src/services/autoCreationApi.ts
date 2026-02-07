/**
 * API service for Auto-Creation Pipeline.
 *
 * Provides functions for managing auto-creation rules, executions, and YAML import/export.
 */
import type {
  AutoCreationRule,
  CreateRuleData,
  UpdateRuleData,
  RulesListResponse,
  ExecutionsListResponse,
  AutoCreationExecution,
  ValidationResult,
  RunPipelineResponse,
  RollbackResponse,
  SchemaResponse,
  ConditionSchema,
  ActionSchema,
  TemplateVariableSchema,
  YAMLExportResponse,
  YAMLImportResponse,
} from '../types/autoCreation';
import { logger } from '../utils/logger';

const API_BASE = '/api';

/**
 * Build a query string from an object of parameters.
 */
function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.set(key, String(value));
    }
  }
  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

/**
 * Fetch JSON with error handling.
 */
async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const method = options?.method || 'GET';
  logger.debug(`Auto-Creation API request: ${method} ${url}`);

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      let errorDetail = response.statusText;
      try {
        const errorBody = await response.json();
        if (errorBody.detail) {
          errorDetail = errorBody.detail;
        }
      } catch {
        // Response body isn't JSON
      }
      logger.error(`Auto-Creation API error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new Error(errorDetail);
    }

    const data = await response.json();
    logger.info(`Auto-Creation API success: ${method} ${url} - ${response.status}`);
    return data;
  } catch (error) {
    logger.exception(`Auto-Creation API request failed: ${method} ${url}`, error as Error);
    throw error;
  }
}

/**
 * Fetch text content (for YAML export).
 */
async function fetchText(url: string, options?: RequestInit): Promise<string> {
  const method = options?.method || 'GET';
  logger.debug(`Auto-Creation API request (text): ${method} ${url}`);

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include',
    });

    if (!response.ok) {
      let errorDetail = response.statusText;
      try {
        const errorBody = await response.json();
        if (errorBody.detail) {
          errorDetail = errorBody.detail;
        }
      } catch {
        // Response body isn't JSON
      }
      logger.error(`Auto-Creation API error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new Error(errorDetail);
    }

    const text = await response.text();
    logger.info(`Auto-Creation API success: ${method} ${url} - ${response.status}`);
    return text;
  } catch (error) {
    logger.exception(`Auto-Creation API request failed: ${method} ${url}`, error as Error);
    throw error;
  }
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
 * Run the auto-creation pipeline.
 */
export async function runAutoCreationPipeline(options?: {
  dryRun?: boolean;
  ruleIds?: number[];
}): Promise<RunPipelineResponse> {
  return fetchJson<RunPipelineResponse>(`${API_BASE}/auto-creation/run`, {
    method: 'POST',
    body: JSON.stringify({
      dry_run: options?.dryRun ?? false,
      rule_ids: options?.ruleIds,
    }),
  });
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
