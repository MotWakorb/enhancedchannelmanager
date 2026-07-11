import type {
  Channel,
  ChannelGroup,
  ChannelProfile,
  MergeChannelsRequest,
  Stream,
  StreamGroupInfo,
  M3UAccount,
  M3UAccountProfile,
  M3UAccountCreateRequest,
  M3UGroupSetting,
  M3UFilter,
  M3UFilterCreateRequest,
  ServerGroup,
  ChannelGroupM3UAccount,
  Logo,
  PaginatedResponse,
  EPGSource,
  EPGData,
  EPGProgram,
  StreamStats,
  StreamProfile,
  DummyEPGCustomProperties,
  SDCustomProperties,
  SDLineup,
  SDLineupsResponse,
  JournalQueryParams,
  JournalResponse,
  JournalStats,
  ChannelStatsResponse,
  SystemEventsResponse,
  NormalizationRuleGroup,
  NormalizationRule,
  CreateRuleGroupRequest,
  UpdateRuleGroupRequest,
  CreateRuleRequest,
  UpdateRuleRequest,
  TestRuleRequest,
  TestRuleResult,
  NormalizationBatchResponse,
  TagGroup,
  Tag,
  CreateTagGroupRequest,
  UpdateTagGroupRequest,
  AddTagsRequest,
  AddTagsResponse,
  UpdateTagRequest,
  // M3U Change Tracking
  M3UChangesResponse,
  M3UChangeSummary,
  M3UDigestSettings,
  M3UDigestSettingsUpdate,
  M3UChangeType,
  // Authentication
  AuthStatus,
  LoginResponse,
  MeResponse,
  LogoutResponse,
  RefreshResponse,
  SetupRequiredResponse,
  SetupRequest,
  SetupResponse,
  // Admin Auth Settings
  AuthSettingsPublic,
  AuthSettingsUpdate,
  UserListResponse,
  UserUpdateRequest,
  UserUpdateResponse,
  // User Profile
  UpdateProfileRequest,
  UpdateProfileResponse,
  ChangePasswordRequest,
  ChangePasswordResponse,
  // Linked Identities (Account Linking)
  LinkedIdentitiesResponse,
  LinkIdentityRequest,
  LinkIdentityResponse,
  UnlinkIdentityResponse,
  // TLS Certificate Management
  TLSStatus,
  TLSSettings,
  TLSConfigureRequest,
  CertificateRequestResponse,
  DNSProviderTestRequest,
  DNSProviderTestResponse,
  // Dummy EPG
  DummyEPGProfile,
  DummyEPGProfileCreateRequest,
  DummyEPGProfileUpdateRequest,
  DummyEPGPreviewRequest,
  DummyEPGPreviewResult,
  DummyEPGBatchPreviewRequest,
  DummyEPGChannelAssignment,
} from '../types';
import { logger } from '../utils/logger';
import { fetchJson, fetchText, buildQuery, HttpError } from './httpClient';
import {
  type TimezonePreference,
  type NumberSeparator,
  getStreamQualityPriority,
  sortStreamsByQuality,
  stripQualitySuffixes,
  stripNetworkPrefix,
  hasNetworkPrefix,
  detectNetworkPrefixes,
  stripNetworkSuffix,
  hasNetworkSuffix,
  detectNetworkSuffixes,
  getCountryPrefix,
  stripCountryPrefix,
  detectCountryPrefixes,
  getUniqueCountryPrefixes,
  getRegionalSuffix,
  detectRegionalVariants,
  filterStreamsByTimezone,
  normalizeStreamNamesWithBackend,
} from './streamNormalization';
// Re-export stream normalization utilities for backward compatibility
export type PrefixOrder = 'number-first' | 'country-first';
export type {
  TimezonePreference,
  NumberSeparator,
};
export {
  getStreamQualityPriority,
  sortStreamsByQuality,
  stripQualitySuffixes,
  stripNetworkPrefix,
  hasNetworkPrefix,
  detectNetworkPrefixes,
  stripNetworkSuffix,
  hasNetworkSuffix,
  detectNetworkSuffixes,
  getCountryPrefix,
  stripCountryPrefix,
  detectCountryPrefixes,
  getUniqueCountryPrefixes,
  getRegionalSuffix,
  detectRegionalVariants,
  filterStreamsByTimezone,
  normalizeStreamNamesWithBackend,
};

const API_BASE = '/api';

// fetchJson and buildQuery imported from ./httpClient

// Channels
export async function getChannels(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  channelGroup?: number;
  signal?: AbortSignal;
}): Promise<PaginatedResponse<Channel>> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.pageSize,
    search: params?.search,
    channel_group: params?.channelGroup,
  });
  return fetchJson(`${API_BASE}/channels${query}`, { signal: params?.signal });
}

export async function getChannelStreams(channelId: number): Promise<Stream[]> {
  return fetchJson(`${API_BASE}/channels/${channelId}/streams`);
}

// -----------------------------------------------------------------------------
// bd-eio04.13 — would-normalize preview for channel rows
// -----------------------------------------------------------------------------

export interface NormalizePreviewTransformation {
  rule_id: number;
  before: string;
  after: string;
}

export interface NormalizePreviewResult {
  channel_id: number;
  current_name: string;
  proposed_name: string;
  would_change: boolean;
  transformations: NormalizePreviewTransformation[];
}

/**
 * bd-eio04.13 — Preview the normalized name for a single channel.
 * Returns `would_change=true` if the current name differs from what the
 * active NormalizationEngine would produce.
 */
export async function getChannelNormalizePreview(
  channelId: number,
  options?: { signal?: AbortSignal },
): Promise<NormalizePreviewResult> {
  return fetchJson(`${API_BASE}/channels/${channelId}/normalize-preview`, {
    signal: options?.signal,
  });
}

/**
 * bd-eio04.13 — Batch preview for currently-visible channel rows.
 *
 * Prefer the `channels` form ({id, name}): the frontend already knows the
 * names, so passing them avoids one Dispatcharr roundtrip per row. Capped
 * at 100 rows per request — the caller is responsible for paging beyond
 * that window. Backend silently skips rows it can't resolve, so the
 * result set may be smaller than the input.
 */
export async function getChannelsNormalizePreviewBatch(
  channels: Array<{ id: number; name: string }>,
  options?: { signal?: AbortSignal },
): Promise<{ results: NormalizePreviewResult[] }> {
  return fetchJson(`${API_BASE}/channels/normalize-preview-batch`, {
    method: 'POST',
    body: JSON.stringify({
      channels: channels.map(c => ({ channel_id: c.id, name: c.name })),
    }),
    signal: options?.signal,
  });
}

export async function updateChannel(id: number, data: Partial<Channel>): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function addStreamToChannel(channelId: number, streamId: number): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${channelId}/add-stream`, {
    method: 'POST',
    body: JSON.stringify({ stream_id: streamId }),
  });
}

export async function removeStreamFromChannel(channelId: number, streamId: number): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${channelId}/remove-stream`, {
    method: 'POST',
    body: JSON.stringify({ stream_id: streamId }),
  });
}

export async function reorderChannelStreams(channelId: number, streamIds: number[]): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${channelId}/reorder-streams`, {
    method: 'POST',
    body: JSON.stringify({ stream_ids: streamIds }),
  });
}

export async function bulkAssignChannelNumbers(
  channelIds: number[],
  startingNumber?: number
): Promise<void> {
  return fetchJson(`${API_BASE}/channels/assign-numbers`, {
    method: 'POST',
    body: JSON.stringify({ channel_ids: channelIds, starting_number: startingNumber }),
  });
}

export async function deleteChannel(channelId: number): Promise<void> {
  return fetchJson(`${API_BASE}/channels/${channelId}`, {
    method: 'DELETE',
  });
}

export async function createChannel(data: {
  name: string;
  channel_number?: number;
  channel_group_id?: number;
  logo_id?: number;
  tvg_id?: string;
  normalize?: boolean;  // Apply normalization rules to channel name
}): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function mergeChannels(request: MergeChannelsRequest): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/merge`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

// -----------------------------------------------------------------------------
// Channel-merges dedup lookup (ADR-008 §D1 / BD-D)
// -----------------------------------------------------------------------------

/**
 * Single dedup candidate returned by GET /api/channel-merges/candidates.
 *
 * `channel_id` is a string because `pending_merges.candidate_channel_id` is
 * TEXT (ADR-008 §D8 channel-id-type note). The backend casts the Dispatcharr
 * channel id to string before returning; the modal and consumers must do the
 * same when comparing or passing it back.
 */
export interface ChannelMergeCandidate {
  channel_id: string;
  channel_name: string;
  /** Normalized confidence score, 0.0–1.0. Always >= 0.60 per §D2 floor. */
  confidence: number;
}

export interface ChannelMergeCandidatesResponse {
  stream_name: string;
  candidates: ChannelMergeCandidate[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/**
 * Synchronous top-1 dedup candidate lookup (BD-D / ADR-008 §D1).
 *
 * Used by the BD-H drag-drop and BD-I "Add Stream" flows to decide whether to
 * prompt the operator with `StreamDedupModal` before creating a new channel.
 * Returns the response envelope verbatim; callers consult `candidates[0]` for
 * the top-1 match (the v0.17.1 matcher returns at most one) and fall through
 * to the original create-channel path when the array is empty.
 *
 * The matcher's §D2 hard floor (60%) is enforced server-side, so a candidate
 * present in the response is always above the floor — the client never has to
 * filter sub-floor matches itself.
 *
 * @param streamName Raw incoming stream name; required, non-blank.
 * @param groupId Optional target channel-group filter. Omit to search all groups.
 */
export async function getChannelMergeCandidates(
  streamName: string,
  groupId?: number | null,
): Promise<ChannelMergeCandidatesResponse> {
  const query = buildQuery({
    stream_name: streamName,
    group_id: groupId ?? undefined,
  });
  return fetchJson(`${API_BASE}/channel-merges/candidates${query}`);
}

// Find & merge duplicate channels
export interface DuplicateGroup {
  normalized_name: string;
  channels: {
    id: number;
    name: string;
    normalized_name: string;
    channel_number: number | null;
    stream_count: number;
    channel_group_id: number | null;
    channel_group_name: string;
  }[];
}

export interface FindDuplicatesResponse {
  groups: DuplicateGroup[];
  total_groups: number;
  total_duplicate_channels: number;
}

export interface BulkMergeItem {
  target_channel_id: number;
  source_channel_ids: number[];
}

export interface BulkMergeResponse {
  merged: number;
  failed: number;
  results: { target_channel_id: number; target_name?: string; sources_deleted?: number; total_streams?: number; success: boolean; error?: string }[];
}

export async function findDuplicateChannels(channelIds?: number[]): Promise<FindDuplicatesResponse> {
  return fetchJson(`${API_BASE}/channels/find-duplicates`, {
    method: 'POST',
    ...(channelIds !== undefined ? { body: JSON.stringify({ channel_ids: channelIds }) } : {}),
  });
}

export async function bulkMergeChannels(merges: BulkMergeItem[]): Promise<BulkMergeResponse> {
  return fetchJson(`${API_BASE}/channels/bulk-merge`, {
    method: 'POST',
    body: JSON.stringify({ merges }),
  });
}

// Bulk operation types for bulk commit
export interface BulkOperation {
  type: string;
  [key: string]: unknown;
}

export interface BulkCommitRequest {
  operations: BulkOperation[];
  groupsToCreate?: { name: string }[];
  /** If true, only validate without executing (returns validation issues) */
  validateOnly?: boolean;
  /** If true, continue processing even when individual operations fail */
  continueOnError?: boolean;
  /** If true, server consolidates redundant operations before executing */
  consolidate?: boolean;
}

export interface ValidationIssue {
  type: 'missing_channel' | 'missing_stream' | 'invalid_operation';
  severity: 'error' | 'warning';
  message: string;
  operationIndex?: number;
  channelId?: number;
  channelName?: string;
  streamId?: number;
  streamName?: string;
}

export interface BulkCommitError {
  operationId: string;
  operationType?: string;
  error: string;
  channelId?: number;
  channelName?: string;
  streamId?: number;
  streamName?: string;
  entityName?: string;
}

export interface BulkCommitResponse {
  success: boolean;
  operationsApplied: number;
  operationsFailed: number;
  errors: BulkCommitError[];
  tempIdMap: Record<number, number>;
  groupIdMap: Record<string, number>;
  /** Validation issues found during pre-validation */
  validationIssues?: ValidationIssue[];
  /** Whether validation passed (no errors, may have warnings) */
  validationPassed?: boolean;
  /**
   * bd-5xciq: true when some operations committed AND some failed. Lets the UI
   * render a distinct partial-success state ("X applied, Y failed") so the
   * operator reconciles via tempIdMap instead of retrying and creating
   * duplicates. False for full success and for total failure (nothing applied).
   */
  partial?: boolean;
}

// 202+poll envelope for the async bulk-commit path (bd-ggxks). validateOnly
// stays synchronous on the backend, so this typing applies only to the
// non-validateOnly POST + the subsequent status polls.

interface BulkCommitJobAccepted {
  job_id: string;
  status: 'running';
  message?: string;
}

type BulkCommitJobStatus =
  | { job_id: string; status: 'running' }
  | { job_id: string; status: 'failed'; error: string }
  | { job_id: string; status: 'completed'; result: BulkCommitResponse };

const BULK_COMMIT_POLL_INTERVAL_MS = 750;
// Hard ceiling well above the backend's 30-min job TTL — a job that genuinely
// hangs longer than this needs operator attention, not silent waiting.
const BULK_COMMIT_POLL_MAX_DURATION_MS = 60 * 60 * 1000;

async function pollBulkCommitJob(jobId: string): Promise<BulkCommitResponse> {
  const started = Date.now();
  while (Date.now() - started < BULK_COMMIT_POLL_MAX_DURATION_MS) {
    const status = await fetchJson<BulkCommitJobStatus>(
      `${API_BASE}/channels/bulk-commit/${encodeURIComponent(jobId)}`,
    );
    if (status.status === 'completed') {
      return status.result;
    }
    if (status.status === 'failed') {
      throw new Error(`Bulk commit failed: ${status.error}`);
    }
    await new Promise((resolve) => setTimeout(resolve, BULK_COMMIT_POLL_INTERVAL_MS));
  }
  throw new Error(`Bulk commit polling exceeded ${BULK_COMMIT_POLL_MAX_DURATION_MS / 1000}s`);
}

/**
 * Commit multiple channel operations.
 *
 * Two paths (bd-ggxks):
 * - validateOnly: synchronous POST returning the full response in one round-trip.
 *   Used by useEditMode.validate() for instant pre-commit feedback.
 * - default (validateOnly=false): POST returns 202 + {job_id}; this function
 *   polls GET /api/channels/bulk-commit/{job_id} until the job terminates,
 *   so callers still receive the same BulkCommitResponse on success and a
 *   thrown Error on failure. The hop is invisible to existing callers.
 */
export async function bulkCommit(request: BulkCommitRequest): Promise<BulkCommitResponse> {
  if (request.validateOnly) {
    return fetchJson(`${API_BASE}/channels/bulk-commit`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  const accepted = await fetchJson<BulkCommitJobAccepted>(`${API_BASE}/channels/bulk-commit`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
  return pollBulkCommitJob(accepted.job_id);
}

// Stream-to-channel deduplication (ADR-008 / bd-1v4ht epic) ----------------
// Re-exports the DedupCandidate shape from the modal so consumers don't pull
// it from a component module. The modal owns the operator-facing fields;
// this layer owns the wire contract.

/** Single candidate returned by GET /api/channel-merges/candidates (BD-D). */
export interface DedupCandidate {
  channel_id: string;
  channel_name: string;
  /** Normalized confidence 0.0–1.0. Backend already enforces the ADR-008 §D2 floor. */
  confidence: number;
}

/** Envelope for GET /api/channel-merges/candidates (BD-D). */
export interface DedupCandidatesResponse {
  stream_name: string;
  candidates: DedupCandidate[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/**
 * Look up dedup candidates for an incoming stream name (BD-D).
 *
 * Synchronous top-1 candidate lookup. The backend returns an empty
 * `candidates` array when nothing clears the §D2 confidence floor — that is
 * the signal to proceed with normal channel creation. When a candidate is
 * present, callers should surface the StreamDedupModal for operator decision.
 */
export async function getDedupCandidates(
  streamName: string,
  groupId?: number | null,
): Promise<DedupCandidatesResponse> {
  const query = buildQuery({ stream_name: streamName, group_id: groupId ?? undefined });
  return fetchJson(`${API_BASE}/channel-merges/candidates${query}`);
}

// Channel Groups
export async function getChannelGroups(): Promise<ChannelGroup[]> {
  return fetchJson(`${API_BASE}/channel-groups`);
}

export async function createChannelGroup(name: string): Promise<ChannelGroup> {
  return fetchJson(`${API_BASE}/channel-groups`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function updateChannelGroup(id: number, data: Partial<ChannelGroup>): Promise<ChannelGroup> {
  return fetchJson(`${API_BASE}/channel-groups/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteChannelGroup(id: number): Promise<void> {
  await fetchJson(`${API_BASE}/channel-groups/${id}`, { method: 'DELETE' });
}

export async function getOrphanedChannelGroups(): Promise<{
  orphaned_groups: { id: number; name: string }[];
  total_groups: number;
  m3u_associated_groups: number;
}> {
  return fetchJson(`${API_BASE}/channel-groups/orphaned`);
}

export async function deleteOrphanedChannelGroups(groupIds?: number[]): Promise<{
  status: string;
  message: string;
  deleted_groups: { id: number; name: string }[];
  failed_groups: { id: number; name: string; error: string }[];
}> {
  // Always send a body with group_ids field (either array or null)
  // This ensures Pydantic can validate the request properly
  return fetchJson(`${API_BASE}/channel-groups/orphaned`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      group_ids: (groupIds && groupIds.length > 0) ? groupIds : null
    }),
  });
}

export async function getHiddenChannelGroups(): Promise<{ id: number; name: string; hidden_at: string }[]> {
  return fetchJson(`${API_BASE}/channel-groups/hidden`);
}

export async function restoreChannelGroup(id: number): Promise<void> {
  await fetchJson(`${API_BASE}/channel-groups/${id}/restore`, {
    method: 'POST',
  });
}

export interface AutoCreatedGroup {
  id: number;
  name: string;
  auto_created_count: number;
  sample_channels: Array<{
    id: number;
    name: string;
    channel_number: number | null;
    auto_created_by: number | null;
    auto_created_by_name: string | null;
  }>;
}

export async function getGroupsWithAutoCreatedChannels(): Promise<{
  groups: AutoCreatedGroup[];
  total_auto_created_channels: number;
}> {
  return fetchJson(`${API_BASE}/channel-groups/auto-created`);
}

export async function clearAutoCreatedFlag(groupIds: number[]): Promise<{
  status: string;
  message: string;
  updated_count: number;
  updated_channels: Array<{ id: number; name: string; channel_number: number | null }>;
  failed_channels: Array<{ id: number; name: string; error: string }>;
}> {
  return fetchJson(`${API_BASE}/channels/clear-auto-created`, {
    method: 'POST',
    body: JSON.stringify({ group_ids: groupIds }),
  });
}

/** Diagnostic report shape from `build_channel_groups_diagnostic` (backend/routers/channel_groups.py). */
export interface ChannelGroupsDiagnostic {
  dispatcharr_group_count: number;
  duplicate_group_names: Record<string, number>;
  hidden_records: Array<{ id: number; stored_name: string; live_name: string | null; status: string }>;
  channel_count: number;
  channels_by_group_id: Record<string, { live_name: string | null; count: number; sample: string[] }>;
  channels_by_group_name_count: Record<string, number>;
  orphaned_channel_group_id_count: number;
  orphaned_sample: Array<{ id: number; name: string; channel_number: number | null; channel_group_name: string | null; channel_group_id: number }>;
  null_id_with_name_count: number;
  null_id_with_name_sample: Array<{ id: number; name: string; channel_number: number | null; channel_group_name: string | null }>;
}

/**
 * Run the Channel Manager group/channel diagnostic (bd-hq3de.b). Read-only —
 * same computation the debug-bundle generator uses for
 * channel_groups_diagnostic.json.
 */
export async function getChannelGroupsDiagnostic(): Promise<ChannelGroupsDiagnostic> {
  return fetchJson(`${API_BASE}/channel-groups/diagnostic`);
}

/** Channel groups that have at least one channel with a stream (probeable groups). */
export async function getChannelGroupsWithStreams(): Promise<{
  groups: { id: number; name: string }[];
  total_groups: number;
}> {
  return fetchJson(`${API_BASE}/channel-groups/with-streams`);
}

// Streams
export async function getStreams(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  channelGroup?: string;
  m3uAccount?: number;
  bypassCache?: boolean;
  signal?: AbortSignal;
}): Promise<PaginatedResponse<Stream>> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.pageSize,
    search: params?.search,
    channel_group_name: params?.channelGroup,
    m3u_account: params?.m3uAccount,
    bypass_cache: params?.bypassCache,
  });
  return fetchJson(`${API_BASE}/streams${query}`, { signal: params?.signal });
}

export async function getStreamGroups(bypassCache?: boolean, m3uAccountId?: number | null): Promise<StreamGroupInfo[]> {
  const queryParams: string[] = [];
  if (bypassCache) queryParams.push('bypass_cache=true');
  if (m3uAccountId !== undefined && m3uAccountId !== null) queryParams.push(`m3u_account_id=${m3uAccountId}`);
  const query = queryParams.length > 0 ? `?${queryParams.join('&')}` : '';
  return fetchJson(`${API_BASE}/stream-groups${query}`);
}

// M3U Accounts (Providers)
export async function getM3UAccounts(): Promise<M3UAccount[]> {
  const accounts = await fetchJson<M3UAccount[]>(`${API_BASE}/providers`);
  logger.debug(`Received ${accounts.length} M3U accounts from API`);
  accounts.forEach((account, index) => {
    logger.debug(`  M3U Account ${index + 1}: id=${account.id}, name=${account.name}`);
  });
  return accounts;
}

export async function getProviderGroupSettings(): Promise<Record<number, M3UGroupSetting>> {
  return fetchJson(`${API_BASE}/providers/group-settings`);
}

// M3U Account CRUD
export async function getM3UAccount(id: number): Promise<M3UAccount> {
  return fetchJson(`${API_BASE}/m3u/accounts/${id}`);
}

export async function createM3UAccount(data: M3UAccountCreateRequest): Promise<M3UAccount> {
  return fetchJson(`${API_BASE}/m3u/accounts`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function uploadM3UFile(file: File): Promise<{ file_path: string; original_name: string; size: number }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/m3u/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorText = await response.text();
    let errorMessage = response.statusText;
    try {
      const errorJson = JSON.parse(errorText);
      errorMessage = errorJson.detail || errorMessage;
    } catch {
      // Use raw text if not JSON
      errorMessage = errorText || errorMessage;
    }
    throw new Error(errorMessage);
  }

  return response.json();
}

export async function updateM3UAccount(id: number, data: Partial<M3UAccount>): Promise<M3UAccount> {
  return fetchJson(`${API_BASE}/m3u/accounts/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function patchM3UAccount(id: number, data: Partial<M3UAccount>): Promise<M3UAccount> {
  return fetchJson(`${API_BASE}/m3u/accounts/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteM3UAccount(id: number): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/m3u/accounts/${id}`, {
    method: 'DELETE',
  });
}

// M3U Refresh
export async function refreshM3UAccount(id: number): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/m3u/refresh/${id}`, {
    method: 'POST',
  });
}

// M3U Stream Metadata - parsed directly from M3U file
export interface M3UStreamMetadataEntry {
  'tvc-guide-stationid'?: string;
  'tvg-name'?: string;
  'tvg-logo'?: string;
  'group-title'?: string;
}

export interface M3UStreamMetadataResponse {
  metadata: Record<string, M3UStreamMetadataEntry>;  // keyed by tvg-id
  count: number;
}

export async function getM3UStreamMetadata(accountId: number): Promise<M3UStreamMetadataResponse> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/stream-metadata`);
}

// M3U Filters
export async function getM3UFilters(accountId: number): Promise<M3UFilter[]> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/filters`);
}

export async function createM3UFilter(accountId: number, data: M3UFilterCreateRequest): Promise<M3UFilter> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/filters`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateM3UFilter(accountId: number, filterId: number, data: Partial<M3UFilter>): Promise<M3UFilter> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/filters/${filterId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteM3UFilter(accountId: number, filterId: number): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/filters/${filterId}`, {
    method: 'DELETE',
  });
}

// M3U Profiles
export interface M3UProfileCreateRequest {
  name: string;
  max_streams?: number;
  is_active?: boolean;
  search_pattern?: string;
  replace_pattern?: string;
}

export async function getM3UProfiles(accountId: number): Promise<M3UAccountProfile[]> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/profiles/`);
}

export async function createM3UProfile(accountId: number, data: M3UProfileCreateRequest): Promise<M3UAccountProfile> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/profiles/`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateM3UProfile(accountId: number, profileId: number, data: Partial<M3UAccountProfile>): Promise<M3UAccountProfile> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/profiles/${profileId}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteM3UProfile(accountId: number, profileId: number): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/profiles/${profileId}/`, {
    method: 'DELETE',
  });
}

// M3U Group Settings
export async function updateM3UGroupSettings(
  accountId: number,
  data: { group_settings: Partial<ChannelGroupM3UAccount>[] }
): Promise<{ message: string }> {
  // Dispatcharr expects 'group_settings' key, not 'channel_groups'
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/group-settings`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

// Server Groups
export async function getServerGroups(): Promise<ServerGroup[]> {
  return fetchJson(`${API_BASE}/m3u/server-groups`);
}

export async function createServerGroup(data: { name: string; account_ids?: number[] }): Promise<ServerGroup> {
  return fetchJson(`${API_BASE}/m3u/server-groups`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateServerGroup(
  groupId: number,
  data: { name?: string; account_ids?: number[] },
): Promise<ServerGroup> {
  return fetchJson(`${API_BASE}/m3u/server-groups/${groupId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteServerGroup(groupId: number): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/m3u/server-groups/${groupId}`, {
    method: 'DELETE',
  });
}

/** Refresh VOD content for an XtreamCodes M3U account (enhancedchannelmanager-hq3de.d). */
export async function refreshM3UVod(accountId: number): Promise<Record<string, unknown>> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/refresh-vod`, {
    method: 'POST',
  });
}

// Health check
export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  release_channel: string;
  git_commit: string;
}

export async function getHealth(): Promise<HealthResponse> {
  return fetchJson(`${API_BASE}/health`);
}

// Version check types
export interface UpdateInfo {
  updateAvailable: boolean;
  latestVersion?: string;
  latestCommit?: string;
  releaseUrl?: string;
  releaseNotes?: string;
}

const GITHUB_REPO = 'MotWakorb/enhancedchannelmanager';

// Compare versions to determine if an update is available
// Handles build suffixes like "0.10.0-0001" properly
// Returns true if latestVersion is newer than currentVersion
function isNewerVersion(latestVersion: string, currentVersion: string): boolean {
  // Extract base version (before any - suffix)
  const getBaseVersion = (v: string) => v.split('-')[0];
  const getBuildNumber = (v: string) => {
    const parts = v.split('-');
    return parts.length > 1 ? parseInt(parts[1], 10) || 0 : 0;
  };

  const latestBase = getBaseVersion(latestVersion);
  const currentBase = getBaseVersion(currentVersion);

  // Parse semver parts
  const parseVersion = (v: string) => {
    const parts = v.split('.').map(p => parseInt(p, 10) || 0);
    return { major: parts[0] || 0, minor: parts[1] || 0, patch: parts[2] || 0 };
  };

  const latest = parseVersion(latestBase);
  const current = parseVersion(currentBase);

  // Compare major.minor.patch
  if (latest.major !== current.major) return latest.major > current.major;
  if (latest.minor !== current.minor) return latest.minor > current.minor;
  if (latest.patch !== current.patch) return latest.patch > current.patch;

  // Base versions are equal - check build numbers
  // If current has a build number (e.g., 0.10.0-0001) and latest doesn't (0.10.0),
  // then current is at or after latest, so no update available
  const latestBuild = getBuildNumber(latestVersion);
  const currentBuild = getBuildNumber(currentVersion);

  // Only newer if latest has a higher build number
  return latestBuild > currentBuild;
}

// Check for updates based on release channel
export async function checkForUpdates(
  currentVersion: string,
  releaseChannel: string
): Promise<UpdateInfo> {
  try {
    if (releaseChannel === 'dev') {
      // For dev channel, check package.json version on dev branch
      const response = await fetch(
        `https://raw.githubusercontent.com/${GITHUB_REPO}/dev/frontend/package.json`,
        { cache: 'no-store' }  // Always fetch fresh
      );
      if (!response.ok) {
        throw new Error(`GitHub fetch error: ${response.status}`);
      }
      const packageJson = await response.json();
      const latestVersion = packageJson.version || 'unknown';

      // Compare versions using semantic version comparison
      const updateAvailable = currentVersion !== 'unknown' &&
        latestVersion !== 'unknown' &&
        isNewerVersion(latestVersion, currentVersion);

      return {
        updateAvailable,
        latestVersion,
        releaseUrl: `https://github.com/${GITHUB_REPO}/tree/dev`,
      };
    } else {
      // For latest/stable channel, check GitHub releases
      const response = await fetch(
        `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`,
        { headers: { 'Accept': 'application/vnd.github.v3+json' } }
      );
      if (!response.ok) {
        if (response.status === 404) {
          // No releases yet
          return { updateAvailable: false };
        }
        throw new Error(`GitHub API error: ${response.status}`);
      }
      const data = await response.json();
      const latestVersion = data.tag_name?.replace(/^v/, '') || 'unknown';

      // Compare versions using semantic version comparison
      const updateAvailable = currentVersion !== 'unknown' &&
        latestVersion !== 'unknown' &&
        isNewerVersion(latestVersion, currentVersion);

      return {
        updateAvailable,
        latestVersion,
        releaseUrl: data.html_url,
        releaseNotes: data.body,
      };
    }
  } catch (error) {
    logger.warn('Failed to check for updates:', error);
    return { updateAvailable: false };
  }
}

// Settings
export type Theme = 'dark' | 'light' | 'high-contrast';

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'WARNING' | 'ERROR' | 'CRITICAL';

// Sort criteria for stream sorting
export type SortCriterion = 'resolution' | 'bitrate' | 'framerate' | 'video_codec' | 'm3u_priority' | 'audio_channels' | 'custom_streams';
export type SortEnabledMap = Record<SortCriterion, boolean>;

// Deprioritized stream categories for ordering within the "failed" group
export type FailedStreamCategory = 'failed' | 'black_screen' | 'low_fps';

// M3U account priorities for sorting - maps account ID (as string) to priority value
export type M3UAccountPriorities = Record<string, number>;

export type GracenoteConflictMode = 'ask' | 'skip' | 'overwrite';

export type DispatcharrAuthMethod = 'password' | 'api_key';

export interface SettingsResponse {
  url: string;
  auth_method: DispatcharrAuthMethod;
  username: string;
  // bd-jmi1c (GH #273): canonical indicator for Dispatcharr REST API token.
  // Older bundles read ``api_key_configured`` — backend responds with both
  // for one release of overlap.
  // Optional: older backends (pre-v0.17.1) omit this field — frontend
  // fallbacks (`?? api_key_configured`) in SettingsModal.tsx and
  // tabs/SettingsTab.tsx handle the undefined case. Remove the `?` (and
  // the legacy alias below) when removing the legacy field in v0.19.0
  // per bd-ewm4h.
  dispatcharr_api_key_configured?: boolean;  // True if a Dispatcharr REST API key is stored (value never returned)
  // Legacy alias retained for the back-compat window — newer backends
  // still emit it for one release, but a future client running against
  // a v0.19.0+ backend may not see it, so this is also optional.
  api_key_configured?: boolean;  // DEPRECATED — alias for dispatcharr_api_key_configured (remove with bd-ewm4h)
  configured: boolean;
  auto_rename_channel_number: boolean;
  include_channel_number_in_name: boolean;
  channel_number_separator: string;
  remove_country_prefix: boolean;
  include_country_in_name: boolean;
  country_separator: string;
  timezone_preference: string;
  show_stream_urls: boolean;
  hide_auto_sync_groups: boolean;
  hide_ungrouped_streams: boolean;
  hide_epg_urls: boolean;
  hide_m3u_urls: boolean;
  gracenote_conflict_mode: GracenoteConflictMode;
  theme: Theme;
  date_format: string;  // Global UI date format: "auto", "mdy", "dmy", or "iso" (bd-8j47e)
  default_channel_profile_ids: number[];
  linked_m3u_accounts: number[][];  // List of link groups, each is a list of account IDs
  // bd-dgs64 (GH #591): opt out of the M3UGroupsModal single-owner auto-sync
  // guard. Admin-only on the backend (install-wide duplicate-channel risk).
  // Default False preserves today's locked behavior.
  allow_multi_provider_auto_sync: boolean;
  epg_auto_match_threshold: number;  // 0-100, confidence score threshold for auto-matching
  custom_network_prefixes: string[];  // User-defined network prefixes to strip
  custom_network_suffixes: string[];  // User-defined network suffixes to strip
  stats_poll_interval: number;  // Seconds between stats polling (default 10)
  user_timezone: string;  // IANA timezone name (e.g. "America/Los_Angeles")
  backend_log_level: string;  // Backend log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  frontend_log_level: string;  // Frontend log level (DEBUG, INFO, WARN, ERROR)
  vlc_open_behavior: string;  // VLC open behavior: "protocol_only", "m3u_fallback", "m3u_only"
  // Stream probe settings (scheduled probing is controlled by Task Engine)
  stream_probe_timeout: number;  // Timeout in seconds for each probe
  stream_probe_schedule_time: string;  // Time of day to run probes (HH:MM, 24h format)
  bitrate_sample_duration: number;  // Duration in seconds to sample stream for bitrate (10, 20, or 30)
  parallel_probing_enabled: boolean;  // Probe streams from different M3Us simultaneously
  max_concurrent_probes: number;  // Max simultaneous probes when parallel probing is enabled (1-16)
  profile_distribution_strategy: string;  // How to distribute probes across M3U profiles: fill_first, round_robin, least_loaded
  skip_recently_probed_hours: number;  // Skip streams probed within last N hours (0 = always probe)
  refresh_m3us_before_probe: boolean;  // Refresh all M3U accounts before starting probe
  auto_reorder_after_probe: boolean;  // Automatically reorder streams in channels after probe completes
  push_stream_stats_to_dispatcharr: boolean;  // Push probe stats back to Dispatcharr after probe
  probe_retry_count: number;   // Retries on transient ffprobe failure (0 = no retry, max 5)
  probe_retry_delay: number;   // Seconds between retries (1-30)
  stream_fetch_page_limit: number;  // Max pages when fetching streams (pages * 500 = max streams)
  stream_sort_priority: SortCriterion[];  // Priority order for Smart Sort (e.g., ['resolution', 'bitrate', 'framerate'])
  stream_sort_enabled: SortEnabledMap;  // Which sort criteria are enabled (e.g., { resolution: true, bitrate: true, framerate: false })
  m3u_account_priorities: M3UAccountPriorities;  // M3U account priorities for sorting (account_id -> priority)
  black_screen_detection_enabled: boolean;  // Run ffmpeg blackdetect after successful probe
  black_screen_sample_duration: number;  // Seconds to sample for black screen detection (3-30)
  low_fps_threshold: number;  // FPS below this value is considered "low FPS"
  deprioritize_failed_streams: boolean;  // When enabled, failed/timeout/pending streams sort to bottom
  deprioritize_black_screen: boolean;  // When disabled, black screen streams sort by quality stats
  deprioritize_low_fps: boolean;  // When disabled, low FPS streams sort by quality stats
  failed_stream_sort_order: FailedStreamCategory[];  // Order of deprioritized categories (first = sorted higher)
  strike_threshold: number;  // Consecutive failures before flagging stream (0 = disabled)
  normalize_on_channel_create: boolean;  // Default state for normalization toggle when creating channels
  // Shared SMTP settings
  smtp_configured: boolean;  // Whether shared SMTP is configured
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_from_email: string;
  smtp_from_name: string;
  smtp_use_tls: boolean;
  smtp_use_ssl: boolean;
  // Shared Discord settings
  discord_configured: boolean;  // Whether shared Discord webhook is configured
  discord_webhook_url: string;
  // Shared Telegram settings
  telegram_configured: boolean;  // Whether shared Telegram bot is configured
  telegram_bot_token: string;
  telegram_chat_id: string;
  // Stream preview mode: "passthrough", "transcode", or "video_only"
  stream_preview_mode: StreamPreviewMode;
  // Auto-creation pipeline exclusion settings
  auto_creation_excluded_terms: string[];
  auto_creation_excluded_groups: string[];
  auto_creation_exclude_auto_sync_groups: boolean;
  // GH #473 auto-creation OOM safety-valve caps (skg35). <= 0 disables.
  max_auto_created_channels_per_run: number;
  max_auto_creation_log_entries: number;
  // MCP integration
  mcp_api_key_configured: boolean;
  // Frontend error telemetry toggle (ADR-006 §10, bd-i6a1m).
  // Default ON; operator can flip via /api/settings to disable reporting.
  telemetry_client_errors_enabled: boolean;
  // Dedup settings (BD-B / BD-K). Server-side clamped to [CONFIDENCE_FLOOR=0.60, 1.00].
  dedup_threshold: number;  // 0.60-1.00 float; UI shows as integer percent 60-100
  dedup_m3u_toast_suppressed: boolean;  // When true, suppress the toast after M3U refresh queues dedup items
  // Emby integration (bd-8wc6q, epic bd-2cenq). When ``emby_enabled`` is
  // true and ``emby_base_url`` + ``emby_api_key`` are configured, the
  // Stats v2 pipeline cross-references active streams against the
  // operator's Emby /Sessions feed to attribute real Emby usernames.
  // The API key itself is NEVER returned — only ``emby_api_key_configured``.
  emby_enabled: boolean;
  emby_base_url: string;
  emby_api_key_configured: boolean;
  // Plex integration (bd-r5f0c.2 / W2). Token uses preserve-on-omit.
  plex_enabled: boolean;
  plex_base_url: string;
  plex_token_configured: boolean;
  // Jellyfin integration (bd-r5f0c.3 / W3). Key uses preserve-on-omit.
  jellyfin_enabled: boolean;
  jellyfin_base_url: string;
  jellyfin_api_key_configured: boolean;
  // bd-mlcla: operator-configured trusted media/proxy networks (CIDRs or
  // bare IPs). Used ONLY to RANK media-server attribution candidates,
  // never to gate. Default empty.
  trusted_media_networks: string[];
  // nngkg / bead 0i2vt.5: DBAS outbound-destination policy mode. Read by the
  // first-run wizard + Settings > Security section. The always-on denylist is
  // enforced unconditionally in the backend regardless of this value.
  ssrf_outbound_mode: OutboundPolicyMode;
}

/**
 * DBAS outbound-destination policy mode (nngkg).
 *
 * `lan_friendly` (DEFAULT) lets ECM reach private/home-network addresses, so
 * backups can go to a NAS on your own network. `public_only` blocks those and
 * allows only public internet destinations. (Plain-language copy lives in the
 * UI; these are the wire values consumed by the backend SSRF chokepoint.)
 */
export type OutboundPolicyMode = 'lan_friendly' | 'public_only';

/**
 * Persist the outbound-policy mode (nngkg). Dedicated endpoint so the
 * Settings > Security section and the first-run wizard can save the operator's
 * choice without a full settings round-trip. Returns the saved mode.
 */
export async function saveSecurityMode(
  mode: OutboundPolicyMode,
): Promise<{ ssrf_outbound_mode: OutboundPolicyMode }> {
  return fetchJson(`${API_BASE}/settings/security`, {
    method: 'PATCH',
    body: JSON.stringify({ ssrf_outbound_mode: mode }),
  });
}

// Stream preview mode for browser playback
export type StreamPreviewMode = 'passthrough' | 'transcode' | 'video_only';

export interface TestConnectionResult {
  success: boolean;
  message: string;
}

export async function getSettings(): Promise<SettingsResponse> {
  return fetchJson(`${API_BASE}/settings`);
}

export async function saveSettings(settings: {
  url: string;
  auth_method: DispatcharrAuthMethod;
  username: string;
  password?: string;  // Optional - only required when changing URL or username
  // bd-jmi1c (GH #273): canonical Dispatcharr REST API key field. The
  // backend accepts ``api_key`` as a deprecated alias for one release.
  // New frontend code should always send ``dispatcharr_api_key``.
  dispatcharr_api_key?: string;   // Optional - only required when (re)setting Dispatcharr API key mode
  api_key?: string;   // DEPRECATED — legacy alias for dispatcharr_api_key (bd-jmi1c)
  auto_rename_channel_number: boolean;
  include_channel_number_in_name: boolean;
  channel_number_separator: string;
  remove_country_prefix: boolean;
  include_country_in_name: boolean;
  country_separator: string;
  timezone_preference: string;
  show_stream_urls?: boolean;  // Optional - defaults to true
  hide_auto_sync_groups?: boolean;  // Optional - defaults to false
  hide_ungrouped_streams?: boolean;  // Optional - defaults to true
  hide_epg_urls?: boolean;  // Optional - defaults to false
  hide_m3u_urls?: boolean;  // Optional - defaults to false
  gracenote_conflict_mode?: GracenoteConflictMode;  // Optional - defaults to 'ask'
  theme?: Theme;  // Optional - defaults to 'dark'
  date_format?: string;  // Optional - "auto" | "mdy" | "dmy" | "iso", defaults to 'auto' (bd-8j47e)
  default_channel_profile_ids?: number[];  // Optional - empty array means no defaults
  linked_m3u_accounts?: number[][];  // Optional - list of link groups
  // bd-dgs64 (GH #591): optional - admin-only on the backend, defaults to false.
  allow_multi_provider_auto_sync?: boolean;
  epg_auto_match_threshold?: number;  // Optional - 0-100, defaults to 80
  custom_network_prefixes?: string[];  // Optional - user-defined network prefixes
  custom_network_suffixes?: string[];  // Optional - user-defined network suffixes
  stats_poll_interval?: number;  // Optional - seconds between stats polling, defaults to 10
  user_timezone?: string;  // Optional - IANA timezone name (e.g. "America/Los_Angeles")
  backend_log_level?: string;  // Optional - Backend log level, defaults to INFO
  frontend_log_level?: string;  // Optional - Frontend log level, defaults to INFO
  vlc_open_behavior?: string;  // Optional - VLC open behavior: "protocol_only", "m3u_fallback", "m3u_only"
  // Stream probe settings (scheduled probing is controlled by Task Engine)
  stream_probe_timeout?: number;  // Optional - timeout in seconds, defaults to 30
  stream_probe_schedule_time?: string;  // Optional - time of day for probes (HH:MM), defaults to "03:00"
  bitrate_sample_duration?: number;  // Optional - duration in seconds to sample stream for bitrate (10, 20, or 30), defaults to 10
  parallel_probing_enabled?: boolean;  // Optional - probe streams from different M3Us simultaneously, defaults to true
  max_concurrent_probes?: number;  // Optional - max simultaneous probes when parallel probing is enabled (1-16), defaults to 8
  profile_distribution_strategy?: string;  // Optional - how to distribute probes across profiles: fill_first, round_robin, least_loaded
  skip_recently_probed_hours?: number;  // Optional - skip streams probed within last N hours, defaults to 0 (always probe)
  refresh_m3us_before_probe?: boolean;  // Optional - refresh all M3U accounts before starting probe, defaults to true
  auto_reorder_after_probe?: boolean;  // Optional - automatically reorder streams after probe, defaults to false
  push_stream_stats_to_dispatcharr?: boolean;  // Optional - reflect probe stats to Dispatcharr, defaults to false
  probe_retry_count?: number;   // Optional - retries on transient ffprobe failure (0 = no retry, max 5), defaults to 1
  probe_retry_delay?: number;   // Optional - seconds between retries (1-30), defaults to 2
  stream_fetch_page_limit?: number;  // Optional - max pages when fetching streams, defaults to 200 (100K streams)
  stream_sort_priority?: SortCriterion[];  // Optional - priority order for Smart Sort, defaults to ['resolution', 'bitrate', 'framerate']
  stream_sort_enabled?: SortEnabledMap;  // Optional - which sort criteria are enabled, defaults to all true
  m3u_account_priorities?: M3UAccountPriorities;  // Optional - M3U account priorities for sorting
  black_screen_detection_enabled?: boolean;  // Optional - run ffmpeg blackdetect after successful probe, defaults to false
  black_screen_sample_duration?: number;  // Optional - seconds to sample for black screen detection (3-30), defaults to 5
  low_fps_threshold?: number;  // Optional - FPS below this value is considered "low FPS", defaults to 20
  deprioritize_failed_streams?: boolean;  // Optional - deprioritize failed/timeout/pending streams in sort, defaults to true
  deprioritize_black_screen?: boolean;  // Optional - deprioritize black screen streams, defaults to true
  deprioritize_low_fps?: boolean;  // Optional - deprioritize low FPS streams, defaults to true
  failed_stream_sort_order?: FailedStreamCategory[];  // Optional - order of deprioritized categories
  strike_threshold?: number;  // Optional - consecutive failures before flagging stream, defaults to 3
  normalize_on_channel_create?: boolean;  // Optional - default state for normalization toggle, defaults to false
  // Shared SMTP settings
  smtp_host?: string;  // Optional - SMTP server hostname
  smtp_port?: number;  // Optional - SMTP port, defaults to 587
  smtp_user?: string;  // Optional - SMTP username
  smtp_password?: string;  // Optional - SMTP password (only send if changing)
  smtp_from_email?: string;  // Optional - From email address
  smtp_from_name?: string;  // Optional - From display name, defaults to "ECM Alerts"
  smtp_use_tls?: boolean;  // Optional - Use TLS, defaults to true
  smtp_use_ssl?: boolean;  // Optional - Use SSL, defaults to false
  // Shared Discord settings
  discord_webhook_url?: string;  // Optional - Discord webhook URL
  // Shared Telegram settings
  telegram_bot_token?: string;  // Optional - Telegram bot token
  telegram_chat_id?: string;  // Optional - Telegram chat ID
  stream_preview_mode?: StreamPreviewMode;  // Optional - Stream preview mode, defaults to "passthrough"
  // Auto-creation pipeline exclusion settings
  auto_creation_excluded_terms?: string[];
  auto_creation_excluded_groups?: string[];
  auto_creation_exclude_auto_sync_groups?: boolean;
  // GH #473 auto-creation OOM safety-valve caps (skg35). Admin-only on the
  // backend; <= 0 disables the cap. Optional so a partial save preserves them.
  max_auto_created_channels_per_run?: number;
  max_auto_creation_log_entries?: number;
  // Frontend error telemetry toggle (ADR-006 §10, bd-i6a1m)
  telemetry_client_errors_enabled?: boolean;
  // Dedup settings (BD-B / BD-K, ADR-008 §D2). Float 0.60-1.00; server clamps to floor.
  dedup_threshold?: number;
  dedup_m3u_toast_suppressed?: boolean;
  // Emby integration (bd-8wc6q, epic bd-2cenq). emby_api_key uses
  // preserve-on-omit on the backend — sending undefined keeps the stored
  // value, same as smtp_password and mcp_api_key.
  emby_enabled?: boolean;
  emby_base_url?: string;
  emby_api_key?: string;
  // Plex integration (bd-r5f0c.2 / W2). plex_token uses preserve-on-omit.
  plex_enabled?: boolean;
  plex_base_url?: string;
  plex_token?: string;
  // Jellyfin integration (bd-r5f0c.3 / W3). jellyfin_api_key uses preserve-on-omit.
  jellyfin_enabled?: boolean;
  jellyfin_base_url?: string;
  jellyfin_api_key?: string;
  // bd-mlcla: trusted media/proxy networks (ranking hint only, never gates).
  trusted_media_networks?: string[];
}): Promise<{ status: string; configured: boolean; server_changed: boolean }> {
  return fetchJson(`${API_BASE}/settings`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

// Emby Settings UI (bd-8wc6q, epic bd-2cenq). The "Test Connection" button
// sends the operator-entered (potentially unsaved) credentials to the
// backend, which constructs an EmbyClient inline and renders the outcome.
// Returns ``{ok: true}`` on a successful /Sessions probe or
// ``{ok: false, error: <msg>}`` on any auth / network / non-2xx failure.
// The backend deliberately does NOT raise — the operator wants the error
// message inline in the UI, not as a generic HTTP failure.
export interface EmbyTestConnectionResult {
  ok: boolean;
  error?: string;
}

export async function testEmbyConnection(
  baseUrl: string,
  apiKey: string,
): Promise<EmbyTestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/emby/test-connection`, {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
  });
}

// Emby Clear Logos (GH #475, bd-v9tp7). POST enqueues a background job
// (202 + {job_id}); poll the status endpoint until terminal. Reuses the saved
// Emby connection on the backend — no credentials cross the wire here.
export const EMBY_LOGO_TYPES = ['Primary', 'LogoLight', 'LogoLightColor'] as const;
export type EmbyLogoType = (typeof EMBY_LOGO_TYPES)[number];

export interface ClearEmbyLogosEnqueueResult {
  job_id: string;
  status: string;
  message?: string;
}

export interface ClearEmbyLogosSummary {
  channels_processed: number;
  images_deleted: number;
  images_missing: number;
  errors: number;
  logo_types: string[];
}

export interface ClearEmbyLogosStatusResult {
  job_id: string;
  status: 'running' | 'completed' | 'failed';
  error?: string;
  result?: ClearEmbyLogosSummary;
}

export async function clearEmbyLogos(
  logoTypes: EmbyLogoType[],
): Promise<ClearEmbyLogosEnqueueResult> {
  return fetchJson(`${API_BASE}/emby/clear-logos`, {
    method: 'POST',
    body: JSON.stringify({ logo_types: logoTypes }),
  });
}

export async function getClearEmbyLogosStatus(
  jobId: string,
): Promise<ClearEmbyLogosStatusResult> {
  return fetchJson(`${API_BASE}/emby/clear-logos/${encodeURIComponent(jobId)}`);
}

// Plex Settings UI test-connection (bd-r5f0c.5 / W5). Same shape as Emby.
export interface PlexTestConnectionResult {
  ok: boolean;
  error?: string;
}

export async function testPlexConnection(
  baseUrl: string,
  plexToken: string,
): Promise<PlexTestConnectionResult> {
  // Wire key is `token` to match the backend PlexTestConnectionRequest schema
  // (X-Plex-Token ecosystem nomenclature). The JS parameter stays `plexToken`
  // for readability at call sites. See bd-8zi93.
  return fetchJson(`${API_BASE}/settings/plex/test-connection`, {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl, token: plexToken }),
  });
}

// Jellyfin Settings UI test-connection (bd-r5f0c.5 / W5). Same shape as Emby.
export interface JellyfinTestConnectionResult {
  ok: boolean;
  error?: string;
}

export async function testJellyfinConnection(
  baseUrl: string,
  apiKey: string,
): Promise<JellyfinTestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/jellyfin/test-connection`, {
    method: 'POST',
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
  });
}

export async function generateMCPApiKey(): Promise<{ mcp_api_key: string }> {
  return fetchJson(`${API_BASE}/settings/mcp-api-key`, { method: 'POST' });
}

export async function revokeMCPApiKey(): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/settings/mcp-api-key`, { method: 'DELETE' });
}

export async function getMCPStatus(): Promise<{
  reachable: boolean;
  status?: string;
  api_key_configured?: boolean;
  // Self-diagnosing /health diagnostic (bd-ix1g6). Distinguishes the four
  // ways a key can be missing so the Settings UI Server Status panel can
  // explain WHY api_key_configured is false without operator shell access.
  // "ok" — key present and non-empty.
  // "file_not_found" — /config/settings.json missing (volume mount issue).
  // "invalid_json" — settings.json exists but is corrupted.
  // "field_missing" — JSON valid but mcp_api_key field absent (legacy file).
  // "field_empty" — field present but empty (no key generated / revoked).
  api_key_status?: 'ok' | 'file_not_found' | 'invalid_json' | 'field_missing' | 'field_empty';
  setup_hint?: string;
  tools_available?: number;
  resources_available?: number;
  error?: string;
}> {
  return fetchJson(`${API_BASE}/settings/mcp-status`);
}

export async function testConnection(settings: {
  url: string;
  auth_method: DispatcharrAuthMethod;
  username?: string;
  password?: string;
  // bd-jmi1c (GH #273): canonical Dispatcharr REST API key; legacy
  // ``api_key`` accepted for one release of back-compat.
  dispatcharr_api_key?: string;
  api_key?: string;  // DEPRECATED — legacy alias for dispatcharr_api_key
}): Promise<TestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/test`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

export interface SMTPTestRequest {
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password: string;
  smtp_from_email: string;
  smtp_from_name: string;
  smtp_use_tls: boolean;
  smtp_use_ssl: boolean;
  to_email: string;  // Test recipient email
}

export async function testSmtpConnection(settings: SMTPTestRequest): Promise<TestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/test-smtp`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

export async function testDiscordWebhook(webhookUrl: string): Promise<TestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/test-discord`, {
    method: 'POST',
    body: JSON.stringify({ webhook_url: webhookUrl }),
  });
}

export async function testTelegramBot(botToken: string, chatId: string): Promise<TestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/test-telegram`, {
    method: 'POST',
    body: JSON.stringify({ bot_token: botToken, chat_id: chatId }),
  });
}

export async function restartServices(): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/settings/restart-services`, {
    method: 'POST',
  });
}

export interface ResetStatsResult {
  success: boolean;
  message: string;
  details: {
    hidden_groups: number;
    watch_stats: number;
    bandwidth_records: number;
    stream_stats: number;
    popularity_scores: number;
  };
}

export async function resetStats(): Promise<ResetStatsResult> {
  return fetchJson(`${API_BASE}/settings/reset-stats`, {
    method: 'POST',
  });
}

// Logos
export async function getLogos(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}): Promise<PaginatedResponse<Logo>> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.pageSize,
    search: params?.search,
  });
  // Debug instrumentation (bd-nh50y): the channel-edit-modal logo picker
  // had zero observability between the API response and the rendered grid,
  // so when operators reported "logos not loading" we had no way to trace
  // what actually happened. These DEBUG lines fire on every fetch; keep them
  // at DEBUG so production INFO callers stay quiet.
  logger.debug(
    `[LogoApi] GET /logos page=${params?.page ?? 1} ` +
      `pageSize=${params?.pageSize ?? 'default'} ` +
      `search=${params?.search ?? '(none)'}`,
  );
  try {
    const result = await fetchJson<PaginatedResponse<Logo>>(
      `${API_BASE}/channels/logos${query}`,
    );
    logger.debug(
      `[LogoApi] Received ${result.results?.length ?? 0} logos, ` +
        `next=${result.next ?? 'null'}`,
    );
    return result;
  } catch (err) {
    logger.error(
      `[LogoApi] GET /logos failed (page=${params?.page ?? 1} ` +
        `pageSize=${params?.pageSize ?? 'default'} ` +
        `search=${params?.search ?? '(none)'}):`,
      err,
    );
    throw err;
  }
}

/**
 * Fetch all logos by paginating until the API reports no `next` page.
 *
 * Extracted from inline loops in App.tsx and LogoManagerTab so the
 * pagination contract — and the diagnostic log line sequence (bd-nh50y) —
 * lives in exactly one place. Tests in `api.test.ts` lock the log sequence
 * so a future refactor cannot silently drop observability.
 *
 * @param pageSize Defaults to 500 (the historical App.tsx default; the
 *   LogoManagerTab callsite previously used 1000 — both work, Dispatcharr
 *   caps at 1000/page).
 */
export async function getAllLogos(pageSize = 500): Promise<Logo[]> {
  logger.info(`[LogoLoader] Starting logo load (pagination, pageSize=${pageSize})`);
  const allLogos: Logo[] = [];
  let page = 1;
  let hasMore = true;
  try {
    while (hasMore) {
      const response = await getLogos({ page, pageSize });
      const fetchedCount = response.results?.length ?? 0;
      allLogos.push(...response.results);
      hasMore = response.next !== null;
      logger.debug(
        `[LogoLoader] Page ${page}: fetched ${fetchedCount} logos, hasMore=${hasMore}`,
      );
      page++;
    }
    logger.info(
      `[LogoLoader] Loaded ${allLogos.length} logos across ${page - 1} pages`,
    );
    return allLogos;
  } catch (err) {
    logger.error(
      `[LogoLoader] Failed on page ${page} after collecting ` +
        `${allLogos.length} partial results:`,
      err,
    );
    throw err;
  }
}

export async function createLogo(data: { name: string; url: string }): Promise<Logo> {
  return fetchJson(`${API_BASE}/channels/logos`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateLogo(id: number, data: Partial<Logo>): Promise<Logo> {
  return fetchJson(`${API_BASE}/channels/logos/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteLogo(id: number): Promise<void> {
  return fetchJson(`${API_BASE}/channels/logos/${id}`, {
    method: 'DELETE',
  });
}

export async function uploadLogo(file: File): Promise<Logo> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', file.name);

  const response = await fetch(`${API_BASE}/channels/logos/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

// EPG Sources
export async function getEPGSources(): Promise<EPGSource[]> {
  return fetchJson(`${API_BASE}/epg/sources`);
}

export async function getEPGSource(id: number): Promise<EPGSource> {
  return fetchJson(`${API_BASE}/epg/sources/${id}`);
}

export interface CreateEPGSourceRequest {
  name: string;
  source_type: 'xmltv' | 'schedules_direct' | 'dummy';
  url?: string | null;
  // Schedules Direct credentials (source_type === 'schedules_direct').
  // password is write-only: send it to create/change, never read it back.
  username?: string | null;
  password?: string | null;
  is_active?: boolean;
  refresh_interval?: number;
  priority?: number;
  custom_properties?: DummyEPGCustomProperties | SDCustomProperties | Record<string, unknown> | null;
}

export async function createEPGSource(data: CreateEPGSourceRequest): Promise<EPGSource> {
  return fetchJson(`${API_BASE}/epg/sources`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// password is write-only (not on EPGSource read type) but accepted on update for SD.
export async function updateEPGSource(
  id: number,
  data: Partial<EPGSource> & { password?: string | null },
): Promise<EPGSource> {
  return fetchJson(`${API_BASE}/epg/sources/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteEPGSource(id: number): Promise<void> {
  await fetchJson(`${API_BASE}/epg/sources/${id}`, { method: 'DELETE' });
}

export async function refreshEPGSource(id: number): Promise<void> {
  return fetchJson(`${API_BASE}/epg/sources/${id}/refresh`, {
    method: 'POST',
  });
}

export async function triggerEPGImport(): Promise<void> {
  return fetchJson(`${API_BASE}/epg/import`, {
    method: 'POST',
  });
}

// --- Schedules Direct (SD) lineup management ---
// These proxy Dispatcharr, which authenticates to SD live on each call and is
// rate-limited by SD (lineup adds ~6/24h). Do not poll/retry these in a loop.

export async function getSDLineups(sourceId: number): Promise<SDLineupsResponse> {
  return fetchJson(`${API_BASE}/epg/sources/${sourceId}/sd-lineups`);
}

export async function searchSDLineups(
  sourceId: number,
  country: string,
  postalcode: string,
): Promise<{ lineups: SDLineup[] }> {
  return fetchJson(`${API_BASE}/epg/sources/${sourceId}/sd-lineups/search`, {
    method: 'POST',
    body: JSON.stringify({ country, postalcode }),
  });
}

export async function addSDLineup(sourceId: number, lineup: string): Promise<void> {
  await fetchJson(`${API_BASE}/epg/sources/${sourceId}/sd-lineups`, {
    method: 'POST',
    body: JSON.stringify({ lineup }),
  });
}

export async function deleteSDLineup(sourceId: number, lineup: string): Promise<void> {
  await fetchJson(`${API_BASE}/epg/sources/${sourceId}/sd-lineups`, {
    method: 'DELETE',
    body: JSON.stringify({ lineup }),
  });
}

// EPG Data
export async function getEPGData(params?: {
  search?: string;
  epgSource?: number;
}): Promise<EPGData[]> {
  const query = buildQuery({
    search: params?.search,
    epg_source: params?.epgSource,
  });
  return fetchJson(`${API_BASE}/epg/data${query}`);
}

// EPG Grid (programs for previous hour + next 24 hours)
// Uses Dispatcharr's /api/epg/grid/ endpoint which automatically filters to:
// - Programs ending after 1 hour ago
// - Programs starting before 24 hours from now
export async function getEPGGrid(): Promise<EPGProgram[]> {
  return fetchJson(`${API_BASE}/epg/grid`);
}

// Get LCN (Logical Channel Number / Gracenote ID) for a TVG-ID from EPG sources
export async function getEPGLcnByTvgId(tvgId: string): Promise<{ tvg_id: string; lcn: string; source: string }> {
  return fetchJson(`${API_BASE}/epg/lcn?tvg_id=${encodeURIComponent(tvgId)}`);
}

// LCN lookup item with optional EPG source
export interface LCNLookupItem {
  tvg_id: string;
  epg_source_id: number | null;
}

// Batch fetch LCN for multiple channels at once (more efficient than individual calls)
// Each item can specify an EPG source - if provided, only that source is searched
export async function getEPGLcnBatch(items: LCNLookupItem[]): Promise<{
  results: Record<string, { lcn: string; source: string }>;
}> {
  return fetchJson(`${API_BASE}/epg/lcn/batch`, {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

// EPG Matching (server-side)
export interface EPGMatchEntry {
  epg_id: number;
  epg_name: string;
  tvg_id: string;
  epg_source: number;
  confidence: number;
  match_type: string;
}

export interface EPGMatchChannelResult {
  channel_id: number;
  channel_name: string;
  detected_country: string | null;
  status: 'exact' | 'multiple' | 'none';
  best_score: number;
  matches: EPGMatchEntry[];
}

export interface EPGMatchResponse {
  exact: EPGMatchChannelResult[];
  multiple: EPGMatchChannelResult[];
  none: EPGMatchChannelResult[];
  summary: {
    total_channels: number;
    exact_count: number;
    multiple_count: number;
    none_count: number;
    match_time_ms: number;
  };
}

export async function matchChannelsToEPG(params: {
  channel_ids?: number[];
  epg_source_ids?: number[];
  /** @deprecated EPG source priority is resolved server-side; no longer sent. Removed in v0.19.0. */
  source_order?: number[];
}): Promise<EPGMatchResponse> {
  return fetchJson(`${API_BASE}/epg/match`, {
    method: 'POST',
    body: JSON.stringify({
      channel_ids: params.channel_ids || [],
      epg_source_ids: params.epg_source_ids || [],
    }),
  });
}

// Stream Profiles
export async function getStreamProfiles(): Promise<StreamProfile[]> {
  return fetchJson(`${API_BASE}/stream-profiles`);
}

export interface StreamProfileCreateRequest {
  name: string;
  command: string;
  parameters: string;
  is_active?: boolean;
}

/** Create a new stream profile in Dispatcharr (enhancedchannelmanager-hq3de.j). */
export async function createStreamProfile(data: StreamProfileCreateRequest): Promise<StreamProfile> {
  return fetchJson(`${API_BASE}/stream-profiles`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// Channel Profiles
export async function getChannelProfiles(): Promise<ChannelProfile[]> {
  return fetchJson(`${API_BASE}/channel-profiles`);
}

export async function getChannelProfile(id: number): Promise<ChannelProfile> {
  return fetchJson(`${API_BASE}/channel-profiles/${id}`);
}

export async function createChannelProfile(data: { name: string }): Promise<ChannelProfile> {
  return fetchJson(`${API_BASE}/channel-profiles`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateChannelProfile(
  id: number,
  data: Partial<ChannelProfile>
): Promise<ChannelProfile> {
  return fetchJson(`${API_BASE}/channel-profiles/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteChannelProfile(id: number): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/channel-profiles/${id}`, {
    method: 'DELETE',
  });
}

export async function updateProfileChannel(
  profileId: number,
  channelId: number,
  data: { enabled: boolean }
): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/channel-profiles/${profileId}/channels/${channelId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Bulk enable/disable a batch of channels for a profile in one call
 * (enhancedchannelmanager-hq3de.i).
 *
 * NOTE: per dispatcharr_client.bulk_update_profile_channels, the underlying
 * Dispatcharr bulk endpoint only UPDATES existing ChannelProfileMembership
 * rows — it does not create new ones for a channel the profile has never
 * tracked before. `updateProfileChannel` (individual PATCH, used by
 * ChannelProfilesListModal's "Save Changes") is the safe path when new
 * memberships may need to be created. Prefer this bulk call only for
 * channels already known to the profile.
 */
export async function bulkUpdateProfileChannels(
  profileId: number,
  channelIds: number[],
  enabled: boolean,
): Promise<Record<string, unknown>> {
  return fetchJson(`${API_BASE}/channel-profiles/${profileId}/channels/bulk-update`, {
    method: 'PATCH',
    body: JSON.stringify({ channel_ids: channelIds, enabled }),
  });
}

// Helper function to get or create a logo by URL
// Dispatcharr enforces unique URLs, so we try to create first, then search if it already exists
export async function getOrCreateLogo(name: string, url: string, logoCache: Map<string, Logo>): Promise<Logo> {
  logger.debug(`Getting or creating logo: ${name}`, { url });

  // Check cache first
  const cached = logoCache.get(url);
  if (cached) {
    logger.debug(`Logo cache hit for: ${url}`);
    return cached;
  }

  try {
    // Try to create the logo
    const logo = await createLogo({ name, url });
    logoCache.set(url, logo);
    logger.info(`Created new logo: ${name}`, { id: logo.id, url });
    return logo;
  } catch (error) {
    logger.warn(`Logo creation failed, searching for existing logo: ${name}`, { url });
    // If creation failed, the logo might already exist - search for it
    // Fetch all logos and find by URL (search param may not support exact URL match)
    const allLogos = await getLogos({ pageSize: 10000 });
    const existingLogo = allLogos.results.find((l) => l.url === url);
    if (existingLogo) {
      logoCache.set(url, existingLogo);
      logger.info(`Found existing logo: ${name}`, { id: existingLogo.id, url });
      return existingLogo;
    }
    // If we still can't find it, re-throw the original error
    logger.error(`Logo not found and creation failed: ${name}`, { url, error });
    throw error;
  }
}

// Journal API
export async function getJournalEntries(params?: JournalQueryParams): Promise<JournalResponse> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.page_size,
    category: params?.category,
    action_type: params?.action_type,
    date_from: params?.date_from,
    date_to: params?.date_to,
    search: params?.search,
    user_initiated: params?.user_initiated,
    mutation_source: params?.mutation_source,
  });
  return fetchJson(`${API_BASE}/journal${query}`);
}

export async function getJournalStats(): Promise<JournalStats> {
  return fetchJson(`${API_BASE}/journal/stats`);
}

/** Delete journal entries older than `days` (enhancedchannelmanager-hq3de.a). */
export async function purgeJournalEntries(days: number): Promise<{ deleted: number; days: number }> {
  const query = buildQuery({ days });
  return fetchJson(`${API_BASE}/journal/purge${query}`, {
    method: 'DELETE',
  });
}

// =============================================================================
// Stats & Monitoring
// =============================================================================

/**
 * Get status of all active channels.
 * Returns summary including active channels, client counts, bitrates, speeds, etc.
 */
export async function getChannelStats(): Promise<ChannelStatsResponse> {
  return fetchJson(`${API_BASE}/stats/channels`);
}

/**
 * Get recent system events (channel start/stop, buffering, client connections).
 */
export async function getSystemEvents(params?: {
  limit?: number;
  offset?: number;
  eventType?: string;
}): Promise<SystemEventsResponse> {
  const query = buildQuery({
    limit: params?.limit,
    offset: params?.offset,
    event_type: params?.eventType,
  });
  return fetchJson(`${API_BASE}/stats/activity${query}`);
}

/**
 * Stop a channel and release all associated resources.
 */
export async function stopChannel(channelId: number | string): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/stats/channels/${channelId}/stop`, {
    method: 'POST',
  });
}

/**
 * Stop a specific client connection on a channel (enhancedchannelmanager-hq3de.h).
 *
 * NOTE: Dispatcharr's underlying `/proxy/ts/stop_client/{channel_id}` is
 * channel-scoped, not client-id-scoped — there is no per-client identifier in
 * the request. Calling this stops "a" client connected to the channel (per
 * Dispatcharr's own selection), not guaranteed to be the specific row the
 * operator clicked. Callers should word confirmation copy accordingly.
 */
export async function stopClient(channelId: number | string): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/stats/channels/${channelId}/stop-client`, {
    method: 'POST',
  });
}

/**
 * Get detailed stats for a specific channel (enhancedchannelmanager-hq3de.g):
 * per-client info, buffer status, codec details.
 *
 * NOTE endpoint id-type mismatch: this router path is typed `channel_id: int`
 * (backend/routers/stats.py get_channel_stats_detail) while its `/proxy/ts/*`
 * siblings (stop / stop-client) are typed `str` and take the stream UUID seen
 * in ChannelStatsResponse.channels[].channel_id. Passing a UUID here 422s.
 * Callers should pass the resolved integer Channel.id (from GET /channels),
 * not the Active-Channels-list channel_id, which may be a UUID.
 */
export async function getChannelStatsDetail(channelId: number): Promise<Record<string, unknown>> {
  return fetchJson(`${API_BASE}/stats/channels/${channelId}`);
}

/**
 * Get bandwidth usage summary for all time periods.
 */
export async function getBandwidthStats(): Promise<import('../types').BandwidthSummary> {
  return fetchJson(`${API_BASE}/stats/bandwidth`);
}

/**
 * Get top watched channels by watch count or watch time.
 */
export async function getTopWatchedChannels(limit: number = 10, sortBy: 'views' | 'time' = 'views'): Promise<import('../types').ChannelWatchStats[]> {
  return fetchJson(`${API_BASE}/stats/top-watched?limit=${limit}&sort_by=${sortBy}`);
}

// =============================================================================
// Enhanced Statistics (v0.11.0)
// =============================================================================

/**
 * Get unique viewer statistics for the specified period.
 *
 * ``groupBy`` controls the Top Viewers bucketing: 'ip' (default) groups by
 * client IP; 'user' groups by COALESCE(username, ip) so resolved viewers
 * collapse across IPs and unresolved viewers fall back to their IP.
 */
export async function getUniqueViewersSummary(
  days: number = 7,
  groupBy: 'ip' | 'user' = 'ip'
): Promise<import('../types').UniqueViewersSummary> {
  return fetchJson(`${API_BASE}/stats/unique-viewers?days=${days}&group_by=${groupBy}`);
}

/**
 * Get per-channel bandwidth statistics.
 */
export async function getChannelBandwidthStats(
  days: number = 7,
  limit: number = 20,
  sortBy: 'bytes' | 'connections' | 'watch_time' = 'bytes'
): Promise<import('../types').ChannelBandwidthStats[]> {
  return fetchJson(`${API_BASE}/stats/channel-bandwidth?days=${days}&limit=${limit}&sort_by=${sortBy}`);
}

/**
 * Get unique viewer counts per channel.
 */
export async function getUniqueViewersByChannel(
  days: number = 7,
  limit: number = 20,
  groupBy: 'ip' | 'user' = 'ip'
): Promise<import('../types').ChannelUniqueViewers[]> {
  return fetchJson(`${API_BASE}/stats/unique-viewers-by-channel?days=${days}&limit=${limit}&group_by=${groupBy}`);
}

// =============================================================================
// Popularity (v0.11.0)
// =============================================================================

/**
 * Get channel popularity rankings.
 */
export async function getPopularityRankings(
  limit: number = 50,
  offset: number = 0
): Promise<import('../types').PopularityRankingsResponse> {
  return fetchJson(`${API_BASE}/stats/popularity/rankings?limit=${limit}&offset=${offset}`);
}

/**
 * Get channels that are trending up or down.
 */
export async function getTrendingChannels(
  direction: 'up' | 'down' = 'up',
  limit: number = 10
): Promise<import('../types').ChannelPopularityScore[]> {
  return fetchJson(`${API_BASE}/stats/popularity/trending?direction=${direction}&limit=${limit}`);
}

/**
 * Get the popularity score for a single channel, keyed by the channel UUID
 * (enhancedchannelmanager-hq3de.g) — same `channel_id` shape as
 * ChannelStatsResponse.channels[].channel_id, NOT the integer Channel.id.
 * Returns null when no score has been calculated yet for the channel.
 */
export async function getChannelPopularity(
  channelId: string
): Promise<import('../types').ChannelPopularityScore | null> {
  try {
    return await fetchJson(`${API_BASE}/stats/popularity/channel/${channelId}`);
  } catch (err) {
    if (err instanceof HttpError && err.status === 404) return null;
    throw err;
  }
}

/**
 * Trigger popularity score calculation.
 */
export async function calculatePopularity(periodDays: number = 7): Promise<import('../types').PopularityCalculationResult> {
  return fetchJson(`${API_BASE}/stats/popularity/calculate?period_days=${periodDays}`, {
    method: 'POST',
  });
}

// =============================================================================
// Watch History (v0.11.0)
// =============================================================================

/**
 * Get watch history log - all channel viewing sessions.
 */
export async function getWatchHistory(options: {
  page?: number;
  pageSize?: number;
  channelId?: string;
  ipAddress?: string;
  days?: number;
} = {}): Promise<import('../types').WatchHistoryResponse> {
  const params = new URLSearchParams();
  if (options.page) params.set('page', String(options.page));
  if (options.pageSize) params.set('page_size', String(options.pageSize));
  if (options.channelId) params.set('channel_id', options.channelId);
  if (options.ipAddress) params.set('ip_address', options.ipAddress);
  if (options.days) params.set('days', String(options.days));

  const queryString = params.toString();
  return fetchJson(`${API_BASE}/stats/watch-history${queryString ? `?${queryString}` : ''}`);
}

// =============================================================================
// Watch-Time by User (v0.17.0 — GH-62, bd-skqln.5/.6)
// =============================================================================
//
// Admin-only endpoints. Non-admin callers receive 403 — callers must surface
// that gracefully (see UserStatsPanel).

/**
 * Get watch-time totals across all users (admin-only).
 *
 * When `groupBy="total"` the row shape is per-user totals; when `groupBy="day"`
 * the rows are (user, day) pairs. `from`/`to` are ISO-8601 UTC strings.
 */
export async function getWatchTimeByUser(options: {
  from?: string;
  to?: string;
  userId?: number;
  groupBy?: 'total' | 'day';
} = {}): Promise<import('../types').WatchTimeTotalsResponse | import('../types').WatchTimeDailyResponse> {
  const params = new URLSearchParams();
  if (options.from) params.set('from', options.from);
  if (options.to) params.set('to', options.to);
  if (options.userId !== undefined) params.set('user_id', String(options.userId));
  if (options.groupBy) params.set('group_by', options.groupBy);
  const queryString = params.toString();
  return fetchJson(`${API_BASE}/stats/watch-time${queryString ? `?${queryString}` : ''}`);
}

/**
 * Get per-user watch-time breakdown by channel (admin-only).
 */
export async function getWatchTimeForUser(
  userId: number,
  options: { from?: string; to?: string } = {},
): Promise<import('../types').WatchTimeChannelBreakdownResponse> {
  const params = new URLSearchParams();
  if (options.from) params.set('from', options.from);
  if (options.to) params.set('to', options.to);
  const queryString = params.toString();
  return fetchJson(`${API_BASE}/stats/watch-time/${userId}${queryString ? `?${queryString}` : ''}`);
}

// =============================================================================
// Per-Provider Stats (v0.17.0 — GH-59, bd-skqln.16/.18)
// =============================================================================
//
// Admin-only endpoints. Non-admin callers receive 403 — callers must surface
// that gracefully (see ProvidersPanel).

/**
 * Per-provider buffer-event time-series.
 * Default window=7d, bucket=hour.
 */
export async function getProvidersBuffering(options: {
  window?: import('../types').ProviderStatsWindow;
  bucket?: import('../types').ProviderStatsBucket;
} = {}): Promise<import('../types').ProviderBufferingResponse> {
  const params = new URLSearchParams();
  if (options.window) params.set('window', options.window);
  if (options.bucket) params.set('bucket', options.bucket);
  const qs = params.toString();
  return fetchJson(`${API_BASE}/stats/providers/buffering${qs ? `?${qs}` : ''}`);
}

/**
 * Total watch time per provider over a window.
 */
export async function getProvidersWatchTime(options: {
  window?: import('../types').ProviderStatsWindow;
} = {}): Promise<import('../types').ProviderWatchTimeResponse> {
  const params = new URLSearchParams();
  if (options.window) params.set('window', options.window);
  const qs = params.toString();
  return fetchJson(`${API_BASE}/stats/providers/watch-time${qs ? `?${qs}` : ''}`);
}

/**
 * Provider × top-N channel byte heatmap.
 * Default window=7d, top_n=50 (backend caps at 500).
 */
export async function getProvidersChannelHeatmap(options: {
  window?: import('../types').ProviderStatsWindow;
  topN?: number;
} = {}): Promise<import('../types').ProviderHeatmapResponse> {
  const params = new URLSearchParams();
  if (options.window) params.set('window', options.window);
  if (options.topN !== undefined) params.set('top_n', String(options.topN));
  const qs = params.toString();
  return fetchJson(`${API_BASE}/stats/providers/channel-heatmap${qs ? `?${qs}` : ''}`);
}

/**
 * Per-provider derived bitrate time-series.
 * Default window=7d, bucket=hour.
 */
export async function getProvidersBitrate(options: {
  window?: import('../types').ProviderStatsWindow;
  bucket?: import('../types').ProviderStatsBucket;
} = {}): Promise<import('../types').ProviderBitrateResponse> {
  const params = new URLSearchParams();
  if (options.window) params.set('window', options.window);
  if (options.bucket) params.set('bucket', options.bucket);
  const qs = params.toString();
  return fetchJson(`${API_BASE}/stats/providers/bitrate${qs ? `?${qs}` : ''}`);
}

// =============================================================================
// Stream Stats / Probing
// =============================================================================

/**
 * Get probe stats for multiple streams by their IDs.
 */
export async function getStreamStatsByIds(streamIds: number[]): Promise<Record<number, StreamStats>> {
  return fetchJson(`${API_BASE}/stream-stats/by-ids`, {
    method: 'POST',
    body: JSON.stringify({ stream_ids: streamIds }),
  });
}

/**
 * Compute sort orders for streams without applying them.
 * Uses server-side sort settings as the single source of truth.
 */
export async function computeSort(
  channels: { channel_id: number; stream_ids: number[] }[],
  mode: string = 'smart'
): Promise<{ results: { channel_id: number; sorted_stream_ids: number[]; changed: boolean }[] }> {
  return fetchJson(`${API_BASE}/stream-stats/compute-sort`, {
    method: 'POST',
    body: JSON.stringify({ channels, mode }),
  });
}

/**
 * Probe multiple streams on-demand.
 */
export async function probeBulkStreams(streamIds: number[]): Promise<import('../types').BulkProbeResult> {
  logger.debug(`[Probe] probeBulkStreams called with ${streamIds.length} stream IDs:`, streamIds);

  try {
    const result = await fetchJson(`${API_BASE}/stream-stats/probe/bulk`, {
      method: 'POST',
      body: JSON.stringify({ stream_ids: streamIds }),
    }) as import('../types').BulkProbeResult;
    logger.debug(`[Probe] probeBulkStreams succeeded, probed ${result.probed} streams`);
    return result;
  } catch (error) {
    logger.error(`[Probe] probeBulkStreams failed:`, error);
    throw error;
  }
}

/**
 * Start background probe of all streams.
 * @param channelGroups - Optional list of channel group names to filter by
 * @param skipM3uRefresh - If true, skip M3U refresh (use for on-demand probes from UI)
 * @param streamIds - Optional list of specific stream IDs to probe (useful for re-probing failed streams)
 */
export async function probeAllStreams(channelGroups?: string[], skipM3uRefresh?: boolean, streamIds?: number[]): Promise<{ status: string; message: string }> {
  logger.debug('[Probe] probeAllStreams called with groups:', channelGroups, 'skipM3uRefresh:', skipM3uRefresh, 'streamIds:', streamIds?.length);

  try {
    const result = await fetchJson(`${API_BASE}/stream-stats/probe/all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel_groups: channelGroups || [],
        skip_m3u_refresh: skipM3uRefresh ?? false,
        stream_ids: streamIds || []
      }),
    }) as { status: string; message: string };
    logger.debug('[Probe] probeAllStreams request succeeded:', result);
    return result;
  } catch (error) {
    logger.error('[Probe] probeAllStreams failed:', error);
    throw error;
  }
}

/**
 * Get current probe all streams progress.
 */
export async function getProbeProgress(): Promise<{
  in_progress: boolean;
  total: number;
  current: number;
  status: string;
  current_stream: string;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  black_screen_count: number;
  low_fps_count: number;
  percentage: number;
  rate_limited?: boolean;
  rate_limited_hosts?: Array<{ host: string; backoff_remaining: number; consecutive_429s: number }>;
  max_backoff_remaining?: number;
}> {
  return fetchJson(`${API_BASE}/stream-stats/probe/progress`, {
    method: 'GET',
  }) as Promise<{
    in_progress: boolean;
    total: number;
    current: number;
    status: string;
    current_stream: string;
    success_count: number;
    failed_count: number;
    skipped_count: number;
    black_screen_count: number;
    low_fps_count: number;
    percentage: number;
    rate_limited?: boolean;
    rate_limited_hosts?: Array<{ host: string; backoff_remaining: number; consecutive_429s: number }>;
    max_backoff_remaining?: number;
  }>;
}

/**
 * Clear (delete) probe stats for the specified streams.
 * Streams will appear as 'pending' (never probed) until re-probed.
 */
export async function clearStreamStats(streamIds: number[]): Promise<{ cleared: number; stream_ids: number[] }> {
  return fetchJson(`${API_BASE}/stream-stats/clear`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stream_ids: streamIds }),
  }) as Promise<{ cleared: number; stream_ids: number[] }>;
}

/**
 * Clear all probe stats for all streams.
 * All streams will appear as 'pending' (never probed) until re-probed.
 */
export async function clearAllStreamStats(): Promise<{ cleared: number }> {
  return fetchJson(`${API_BASE}/stream-stats/clear-all`, {
    method: 'POST',
  }) as Promise<{ cleared: number }>;
}

// Strike Rule API

export interface StruckOutStream extends StreamStats {
  channels: { id: number; name: string }[];
}

export interface StruckOutResponse {
  streams: StruckOutStream[];
  threshold: number;
  enabled: boolean;
}

export async function getStruckOutStreams(): Promise<StruckOutResponse> {
  return fetchJson(`${API_BASE}/stream-stats/struck-out`);
}

export async function removeStruckOutStreams(streamIds: number[]): Promise<{ removed_from_channels: number; stream_ids: number[] }> {
  return fetchJson(`${API_BASE}/stream-stats/struck-out/remove`, {
    method: 'POST',
    body: JSON.stringify({ stream_ids: streamIds }),
  });
}

// Stale Streams API
//
// A stream is stale when either signal fires:
// - not_probed_recently: ECM hasn't ffprobed it within the `days` threshold (or ever)
// - provider_stale: Dispatcharr's own M3U refresh no longer re-matched it in the source playlist
export type StaleReason = 'not_probed_recently' | 'provider_stale';

export interface StaleStream {
  stream_id: number;
  stream_name: string | null;
  last_probed: string | null;
  provider_last_seen: string | null;
  reasons: StaleReason[];
  channels: { id: number; name: string }[];
}

export interface StaleStreamsResponse {
  streams: StaleStream[];
  threshold_days: number;
}

export async function getStaleStreams(days = 7): Promise<StaleStreamsResponse> {
  return fetchJson(`${API_BASE}/stream-stats/stale?days=${days}`);
}

export interface SortConfig {
  priority: string[];
  enabled: Record<string, boolean>;
  deprioritize_failed: boolean;
}

export interface ProbeHistoryEntry {
  timestamp: string;
  end_timestamp: string;
  duration_seconds: number;
  total: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  status: string;
  error?: string;
  success_streams: Array<{ id: number; name: string; url?: string }>;
  failed_streams: Array<{ id: number; name: string; url?: string; error?: string }>;
  skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
  black_screen_count: number;
  black_screen_streams: Array<{ id: number; name: string; url?: string }>;
  low_fps_count: number;
  low_fps_streams: Array<{ id: number; name: string; url?: string }>;
  reordered_channels?: Array<{
    channel_id: number;
    channel_name: string;
    stream_count: number;
    streams_before: Array<{
      id: number;
      name: string;
      position: number;
      status: string;
      resolution?: string;
      bitrate?: number;
    }>;
    streams_after: Array<{
      id: number;
      name: string;
      position: number;
      status: string;
      resolution?: string;
      bitrate?: number;
    }>;
  }>;
  sort_config?: SortConfig | null;
}

export async function getProbeHistory(): Promise<ProbeHistoryEntry[]> {
  return fetchJson(`${API_BASE}/stream-stats/probe/history`, {
    method: 'GET',
  }) as Promise<ProbeHistoryEntry[]>;
}

export async function cancelProbe(): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/stream-stats/probe/cancel`, {
    method: 'POST',
  }) as Promise<{ status: string; message: string }>;
}

export async function pauseProbe(): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/stream-stats/probe/pause`, {
    method: 'POST',
  }) as Promise<{ status: string; message: string }>;
}

export async function resumeProbe(): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/stream-stats/probe/resume`, {
    method: 'POST',
  }) as Promise<{ status: string; message: string }>;
}

export async function resetProbeState(): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/stream-stats/probe/reset`, {
    method: 'POST',
  }) as Promise<{ status: string; message: string }>;
}

// -------------------------------------------------------------------------
// Scheduled Tasks API
// -------------------------------------------------------------------------

export interface TaskScheduleConfig {
  schedule_type: 'interval' | 'cron' | 'manual';
  interval_seconds: number;
  cron_expression: string;
  schedule_time: string;
  timezone: string;
}

// New multi-schedule types
export type TaskScheduleType = 'interval' | 'daily' | 'weekly' | 'biweekly' | 'monthly';

export interface TaskSchedule {
  id: number;
  task_id: string;
  name: string | null;
  enabled: boolean;
  schedule_type: TaskScheduleType;
  interval_seconds: number | null;
  schedule_time: string | null;
  timezone: string | null;
  days_of_week: number[] | null;  // 0=Sunday, 6=Saturday
  day_of_month: number | null;  // 1-31, or -1 for last day
  week_parity: number | null;  // For biweekly: 0 or 1
  parameters: Record<string, unknown>;  // Task-specific parameters
  next_run_at: string | null;
  last_run_at: string | null;
  description: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface TaskScheduleCreate {
  name?: string | null;
  enabled?: boolean;
  schedule_type: TaskScheduleType;
  interval_seconds?: number | null;
  schedule_time?: string | null;
  timezone?: string | null;
  days_of_week?: number[] | null;
  day_of_month?: number | null;
  parameters?: Record<string, unknown>;  // Task-specific parameters
}

export interface TaskScheduleUpdate {
  name?: string | null;
  enabled?: boolean;
  schedule_type?: TaskScheduleType;
  interval_seconds?: number | null;
  schedule_time?: string | null;
  timezone?: string | null;
  days_of_week?: number[] | null;
  day_of_month?: number | null;
  parameters?: Record<string, unknown>;  // Task-specific parameters
}

// Task parameter schema types
export interface TaskParameterSchema {
  name: string;
  type: 'number' | 'string' | 'boolean' | 'string_array' | 'number_array';
  label: string;
  description: string;
  default?: unknown;
  min?: number;
  max?: number;
  source?: string;  // e.g., 'channel_groups', 'm3u_accounts', 'epg_sources'
}

export interface TaskParameterSchemaResponse {
  task_id: string;
  description: string;
  parameters: TaskParameterSchema[];
}

export interface TaskProgress {
  total: number;
  current: number;
  percentage: number;
  status: string;
  current_item: string;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  started_at: string | null;
}

export interface TaskStatus {
  task_id: string;
  task_name: string;
  task_description: string;
  status: 'idle' | 'scheduled' | 'running' | 'paused' | 'cancelled' | 'completed' | 'failed';
  enabled: boolean;
  progress: TaskProgress;
  schedule: TaskScheduleConfig;  // Legacy schedule config
  schedules: TaskSchedule[];  // New multi-schedule support
  last_run: string | null;
  next_run: string | null;
  config: Record<string, unknown>;  // Task-specific configuration
  // Alert configuration
  send_alerts?: boolean;  // Master toggle for alerts
  alert_on_success?: boolean;  // Alert when task succeeds
  alert_on_warning?: boolean;  // Alert on partial failures
  alert_on_error?: boolean;  // Alert on complete failures
  alert_on_info?: boolean;  // Alert on info messages
  // Notification channels
  send_to_email?: boolean;  // Send alerts via email
  send_to_discord?: boolean;  // Send alerts via Discord
  send_to_telegram?: boolean;  // Send alerts via Telegram
  show_notifications?: boolean;  // Show in NotificationCenter (bell icon)
}

export interface TaskExecution {
  id: number;
  task_id: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  success: boolean | null;
  message: string | null;
  error: string | null;
  total_items: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  details: Record<string, unknown> | null;
  triggered_by: 'scheduled' | 'manual' | 'api';
}

export interface TaskConfigUpdate {
  enabled?: boolean;
  schedule_type?: 'interval' | 'cron' | 'manual';
  interval_seconds?: number;
  cron_expression?: string;
  schedule_time?: string;
  timezone?: string;
  config?: Record<string, unknown>;  // Task-specific configuration
  // Alert configuration
  send_alerts?: boolean;  // Master toggle for alerts
  alert_on_success?: boolean;  // Alert when task succeeds
  alert_on_warning?: boolean;  // Alert on partial failures
  alert_on_error?: boolean;  // Alert on complete failures
  alert_on_info?: boolean;  // Alert on info messages
  // Notification channels
  send_to_email?: boolean;  // Send alerts via email
  send_to_discord?: boolean;  // Send alerts via Discord
  send_to_telegram?: boolean;  // Send alerts via Telegram
  show_notifications?: boolean;  // Show in NotificationCenter (bell icon)
}

export async function getTasks(): Promise<{ tasks: TaskStatus[] }> {
  return fetchJson(`${API_BASE}/tasks`, {
    method: 'GET',
  });
}

export async function getTask(taskId: string): Promise<TaskStatus> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, {
    method: 'GET',
  });
}

export async function updateTask(taskId: string, config: TaskConfigUpdate): Promise<TaskStatus> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
}

export async function runTask(taskId: string, scheduleId?: number, parameters?: Record<string, unknown>): Promise<{
  success: boolean;
  message: string;
  error?: string;  // "CANCELLED" when task was cancelled
  started_at: string;
  completed_at: string;
  total_items: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
}> {
  const body: Record<string, unknown> = {};
  if (scheduleId) body.schedule_id = scheduleId;
  if (parameters) body.parameters = parameters;
  const hasBody = Object.keys(body).length > 0;
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/run`, {
    method: 'POST',
    headers: hasBody ? { 'Content-Type': 'application/json' } : undefined,
    body: hasBody ? JSON.stringify(body) : undefined,
  });
}

export async function cancelTask(taskId: string): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
  });
}

export async function getTaskHistory(taskId: string, limit = 50, offset = 0): Promise<{ history: TaskExecution[] }> {
  const query = buildQuery({ limit, offset });
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/history${query}`, {
    method: 'GET',
  });
}

// -------------------------------------------------------------------------
// Task Schedule API (Multiple Schedules per Task)
// -------------------------------------------------------------------------

export async function getTaskSchedules(taskId: string): Promise<{ schedules: TaskSchedule[] }> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules`, {
    method: 'GET',
  });
}

export async function createTaskSchedule(taskId: string, data: TaskScheduleCreate): Promise<TaskSchedule> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

export async function updateTaskSchedule(
  taskId: string,
  scheduleId: number,
  data: TaskScheduleUpdate
): Promise<TaskSchedule> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules/${scheduleId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

export async function deleteTaskSchedule(taskId: string, scheduleId: number): Promise<{ status: string; id: number }> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules/${scheduleId}`, {
    method: 'DELETE',
  });
}

export async function getTaskParameterSchema(taskId: string): Promise<TaskParameterSchemaResponse> {
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/parameter-schema`, {
    method: 'GET',
  });
}

// -------------------------------------------------------------------------
// Notifications API
// -------------------------------------------------------------------------

export interface Notification {
  id: number;
  type: 'info' | 'success' | 'warning' | 'error';
  title: string | null;
  message: string;
  read: boolean;
  source: string | null;
  source_id: string | null;
  action_label: string | null;
  action_url: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  read_at: string | null;
  expires_at: string | null;
}

export interface NotificationsResponse {
  notifications: Notification[];
  total: number;
  unread_count: number;
  page: number;
  page_size: number;
}

export async function getNotifications(params?: {
  page?: number;
  page_size?: number;
  unread_only?: boolean;
  notification_type?: string;
}): Promise<NotificationsResponse> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.page_size,
    unread_only: params?.unread_only,
    notification_type: params?.notification_type,
  });
  return fetchJson(`${API_BASE}/notifications${query}`);
}

export async function markNotificationRead(notificationId: number, read: boolean = true): Promise<Notification> {
  const query = buildQuery({ read });
  return fetchJson(`${API_BASE}/notifications/${notificationId}${query}`, {
    method: 'PATCH',
  });
}

export async function markAllNotificationsRead(): Promise<{ marked_read: number }> {
  return fetchJson(`${API_BASE}/notifications/mark-all-read`, {
    method: 'PATCH',
  });
}

export async function deleteNotification(notificationId: number): Promise<{ deleted: boolean }> {
  return fetchJson(`${API_BASE}/notifications/${notificationId}`, {
    method: 'DELETE',
  });
}

export async function clearNotifications(readOnly: boolean = true): Promise<{ deleted: number; read_only: boolean }> {
  const query = buildQuery({ read_only: readOnly });
  return fetchJson(`${API_BASE}/notifications${query}`, {
    method: 'DELETE',
  });
}

// =============================================================================
// Normalization Rules API
// =============================================================================

/**
 * Create a new normalization rule group
 */
export async function createNormalizationGroup(data: CreateRuleGroupRequest): Promise<NormalizationRuleGroup> {
  return fetchJson(`${API_BASE}/normalization/groups`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update a normalization rule group
 */
export async function updateNormalizationGroup(groupId: number, data: UpdateRuleGroupRequest): Promise<NormalizationRuleGroup> {
  return fetchJson(`${API_BASE}/normalization/groups/${groupId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a normalization rule group
 */
export async function deleteNormalizationGroup(groupId: number): Promise<{ status: string; id: number }> {
  return fetchJson(`${API_BASE}/normalization/groups/${groupId}`, {
    method: 'DELETE',
  });
}

/**
 * Reorder normalization rule groups
 */
export async function reorderNormalizationGroups(groupIds: number[]): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/normalization/groups/reorder`, {
    method: 'POST',
    body: JSON.stringify({ group_ids: groupIds }),
  });
}

/**
 * Get all normalization rules (optionally filtered by group)
 */
export async function getNormalizationRules(groupId?: number): Promise<{ groups: NormalizationRuleGroup[] }> {
  const query = groupId ? `?group_id=${groupId}` : '';
  return fetchJson(`${API_BASE}/normalization/rules${query}`);
}

/**
 * Create a new normalization rule
 */
export async function createNormalizationRule(data: CreateRuleRequest): Promise<NormalizationRule> {
  return fetchJson(`${API_BASE}/normalization/rules`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update a normalization rule
 */
export async function updateNormalizationRule(ruleId: number, data: UpdateRuleRequest): Promise<NormalizationRule> {
  return fetchJson(`${API_BASE}/normalization/rules/${ruleId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a normalization rule
 */
export async function deleteNormalizationRule(ruleId: number): Promise<{ status: string; id: number }> {
  return fetchJson(`${API_BASE}/normalization/rules/${ruleId}`, {
    method: 'DELETE',
  });
}

/**
 * Reorder rules within a group
 */
export async function reorderNormalizationRules(groupId: number, ruleIds: number[]): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/normalization/groups/${groupId}/rules/reorder`, {
    method: 'POST',
    body: JSON.stringify({ rule_ids: ruleIds }),
  });
}

/**
 * Test a single rule configuration without saving
 */
export async function testNormalizationRule(data: TestRuleRequest): Promise<TestRuleResult> {
  return fetchJson(`${API_BASE}/normalization/test`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Test multiple texts through all enabled rules (with transformation details)
 */
export async function testNormalizationBatch(texts: string[]): Promise<NormalizationBatchResponse> {
  return fetchJson(`${API_BASE}/normalization/test-batch`, {
    method: 'POST',
    body: JSON.stringify({ texts }),
  });
}

export interface NormalizationRuleStat {
  rule_id: number;
  rule_name: string;
  group_id: number;
  group_name: string;
  enabled: boolean;
  match_count: number;
  match_percentage: number;
}

export interface NormalizationRuleStatsResponse {
  rule_stats: NormalizationRuleStat[];
  total_streams_tested: number;
  total_rules: number;
}

/**
 * Get per-rule match counts against a sample of current Dispatcharr stream
 * names (enhancedchannelmanager-hq3de.e). Expensive (tests every enabled
 * rule against up to `limit` streams) — call on demand, not on every render.
 */
export async function getNormalizationRuleStats(limit = 500): Promise<NormalizationRuleStatsResponse> {
  const query = buildQuery({ limit });
  return fetchJson(`${API_BASE}/normalization/rule-stats${query}`);
}

/**
 * Normalize texts through all enabled rules (simple result)
 */
export async function normalizeTexts(texts: string[]): Promise<NormalizationBatchResponse> {
  return fetchJson(`${API_BASE}/normalization/normalize`, {
    method: 'POST',
    body: JSON.stringify({ texts }),
  });
}

/**
 * Export normalization rules as YAML
 */
export async function exportNormalizationRulesYaml(): Promise<string> {
  const response = await fetch(`${API_BASE}/normalization/export`);
  if (!response.ok) throw new Error('Failed to export normalization rules');
  return response.text();
}

/**
 * Import normalization rules from YAML
 */
export async function importNormalizationRulesYaml(yamlContent: string, overwrite: boolean = false): Promise<{ status: string; created_groups: number; created_rules: number; skipped_groups: number }> {
  return fetchJson(`${API_BASE}/normalization/import`, {
    method: 'POST',
    body: JSON.stringify({ yaml_content: yamlContent, overwrite }),
  });
}

/**
 * Preview applying enabled normalization rules to every existing channel.
 * Returns a per-channel diff without mutating anything (GH-104).
 */
export async function previewApplyNormalizationToChannels(): Promise<import('../types').ApplyToChannelsDryRunResponse> {
  return fetchJson(`${API_BASE}/normalization/apply-to-channels?dry_run=true`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

/**
 * Execute the apply-to-channels flow with per-row actions.
 * Each action entry selects rename / merge / skip for one channel.
 */
export async function executeApplyNormalizationToChannels(
  actions: import('../types').ApplyToChannelsActionOverride[],
): Promise<import('../types').ApplyToChannelsExecuteResponse> {
  return fetchJson(`${API_BASE}/normalization/apply-to-channels?dry_run=false`, {
    method: 'POST',
    body: JSON.stringify({ actions }),
  });
}

// =============================================================================
// Tag Engine API
// =============================================================================

/**
 * Get all tag groups with tag counts
 */
export async function getTagGroups(): Promise<{ groups: TagGroup[] }> {
  return fetchJson(`${API_BASE}/tags/groups`);
}

/**
 * Get a single tag group with all its tags
 */
export async function getTagGroup(groupId: number): Promise<TagGroup> {
  return fetchJson(`${API_BASE}/tags/groups/${groupId}`);
}

/**
 * Create a new tag group
 */
export async function createTagGroup(data: CreateTagGroupRequest): Promise<TagGroup> {
  return fetchJson(`${API_BASE}/tags/groups`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update a tag group
 */
export async function updateTagGroup(groupId: number, data: UpdateTagGroupRequest): Promise<TagGroup> {
  return fetchJson(`${API_BASE}/tags/groups/${groupId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a tag group (cannot delete built-in groups)
 */
export async function deleteTagGroup(groupId: number): Promise<{ status: string; id: number }> {
  return fetchJson(`${API_BASE}/tags/groups/${groupId}`, {
    method: 'DELETE',
  });
}

/**
 * Add tags to a group (supports bulk add)
 */
export async function addTagsToGroup(groupId: number, data: AddTagsRequest): Promise<AddTagsResponse> {
  return fetchJson(`${API_BASE}/tags/groups/${groupId}/tags`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update a tag (enabled, case_sensitive)
 */
export async function updateTag(groupId: number, tagId: number, data: UpdateTagRequest): Promise<Tag> {
  return fetchJson(`${API_BASE}/tags/groups/${groupId}/tags/${tagId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a tag from a group (cannot delete built-in tags)
 */
export async function deleteTag(groupId: number, tagId: number): Promise<{ status: string; id: number }> {
  return fetchJson(`${API_BASE}/tags/groups/${groupId}/tags/${tagId}`, {
    method: 'DELETE',
  });
}

/**
 * Export tags as YAML
 */
export async function exportTagsYaml(): Promise<string> {
  const response = await fetch(`${API_BASE}/tags/export`);
  if (!response.ok) throw new Error('Failed to export tags');
  return response.text();
}

/**
 * Import tags from YAML
 */
export async function importTagsYaml(yamlContent: string, overwrite: boolean = false): Promise<{ status: string; created_groups: number; created_tags: number; merged_groups: number }> {
  return fetchJson(`${API_BASE}/tags/import`, {
    method: 'POST',
    body: JSON.stringify({ yaml_content: yamlContent, overwrite }),
  });
}

export interface TestTagsResult {
  text: string;
  group_id: number;
  group_name: string;
  matches: Array<{ tag_id: number; value: string; case_sensitive: boolean }>;
  match_count: number;
}

/**
 * Test text against a tag group's enabled tags (enhancedchannelmanager-hq3de.f).
 * Mirrors the normalization engine's test UX (testNormalizationRule).
 */
export async function testTags(groupId: number, text: string): Promise<TestTagsResult> {
  return fetchJson(`${API_BASE}/tags/test`, {
    method: 'POST',
    body: JSON.stringify({ group_id: groupId, text }),
  });
}

// =============================================================================
// M3U Change Tracking API
// =============================================================================

/**
 * Get paginated list of M3U change logs
 */
export async function getM3UChanges(params?: {
  page?: number;
  pageSize?: number;
  m3uAccountId?: number;
  changeType?: M3UChangeType;
  enabled?: boolean;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  dateFrom?: string;  // ISO timestamp
  dateTo?: string;    // ISO timestamp
}): Promise<M3UChangesResponse> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.pageSize,
    m3u_account_id: params?.m3uAccountId,
    change_type: params?.changeType,
    enabled: params?.enabled,
    sort_by: params?.sortBy,
    sort_order: params?.sortOrder,
    date_from: params?.dateFrom,
    date_to: params?.dateTo,
  });
  return fetchJson(`${API_BASE}/m3u/changes${query}`);
}

/**
 * Get aggregated summary of M3U changes
 */
export async function getM3UChangesSummary(params?: {
  hours?: number;  // Look back this many hours (default: 24)
  m3uAccountId?: number;
}): Promise<M3UChangeSummary> {
  const query = buildQuery({
    hours: params?.hours,
    m3u_account_id: params?.m3uAccountId,
  });
  return fetchJson(`${API_BASE}/m3u/changes/summary${query}`);
}

/**
 * Get M3U digest email settings
 */
export async function getM3UDigestSettings(): Promise<M3UDigestSettings> {
  return fetchJson(`${API_BASE}/m3u/digest/settings`);
}

/**
 * Update M3U digest email settings
 */
export async function updateM3UDigestSettings(data: M3UDigestSettingsUpdate): Promise<M3UDigestSettings> {
  return fetchJson(`${API_BASE}/m3u/digest/settings`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/**
 * Send a test digest email
 */
export async function sendTestM3UDigest(): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/m3u/digest/test`, {
    method: 'POST',
  });
}

// =============================================================================
// CSV Import/Export API
// =============================================================================

/**
 * Result of a CSV import operation.
 */
export interface CSVImportResult {
  success: boolean;
  channels_created: number;
  groups_created: number;
  streams_linked: number;
  errors: Array<{ row: number; error: string }>;
  warnings: Array<string>;
}

/**
 * Result of CSV preview parsing.
 */
export interface CSVPreviewResult {
  rows: Array<Record<string, string>>;
  errors: Array<{ row: number; error: string }>;
}

/**
 * Export all channels to CSV file.
 * Returns a Blob containing the CSV content.
 */
export async function exportChannelsToCSV(): Promise<Blob> {
  const response = await fetch(`${API_BASE}/channels/export-csv`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: 'Export failed' }));
    throw new Error(errorData.detail || 'Export failed');
  }
  return response.blob();
}

/**
 * Download the CSV template for channel imports.
 * Returns a Blob containing the template CSV content.
 */
export async function downloadCSVTemplate(): Promise<Blob> {
  const response = await fetch(`${API_BASE}/channels/csv-template`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: 'Download failed' }));
    throw new Error(errorData.detail || 'Download failed');
  }
  return response.blob();
}

/**
 * Import channels from a CSV file.
 * @param file - The CSV file to import
 * @returns Import result with counts and any errors
 */
export async function importChannelsFromCSV(file: File): Promise<CSVImportResult> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/channels/import-csv`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: 'Import failed' }));
    throw new Error(errorData.detail || 'Import failed');
  }

  return response.json();
}

/**
 * Parse CSV content and return preview of rows for validation.
 * @param content - Raw CSV content as string
 * @returns Parsed rows and any validation errors
 */
export async function parseCSVPreview(content: string): Promise<CSVPreviewResult> {
  return fetchJson(`${API_BASE}/channels/preview-csv`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

// =============================================================================
// Authentication API
// =============================================================================

/**
 * Get authentication status and configuration.
 * This is always public - used to check if auth is required.
 */
export async function getAuthStatus(): Promise<AuthStatus> {
  return fetchJson(`${API_BASE}/auth/status`);
}

/**
 * Login with username and password (local authentication).
 * Sets httpOnly cookies with JWT tokens.
 */
export async function login(username: string, password: string): Promise<LoginResponse> {
  return fetchJson(`${API_BASE}/auth/login`, {
    method: 'POST',
    body: JSON.stringify({ username, password }),
    credentials: 'include', // Important: include cookies in request
  });
}

/**
 * Login with Dispatcharr credentials.
 * Authenticates against Dispatcharr and creates/updates local user.
 * Sets httpOnly cookies with JWT tokens.
 */
export async function dispatcharrLogin(username: string, password: string): Promise<LoginResponse> {
  return fetchJson(`${API_BASE}/auth/dispatcharr/login`, {
    method: 'POST',
    body: JSON.stringify({ username, password }),
    credentials: 'include',
  });
}

/**
 * Get current authenticated user information.
 * Requires valid access token (sent via cookie).
 */
export async function getCurrentUser(): Promise<MeResponse> {
  return fetchJson(`${API_BASE}/auth/me`, {
    credentials: 'include',
  });
}

/**
 * Refresh access token using refresh token.
 * Called automatically when access token expires.
 */
export async function refreshToken(): Promise<RefreshResponse> {
  return fetchJson(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  });
}

/**
 * Logout current user.
 * Clears cookies and revokes refresh token.
 */
export async function logout(): Promise<LogoutResponse> {
  return fetchJson(`${API_BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  });
}

/**
 * Check if first-time setup is required.
 * Returns true if no users exist in the system.
 * This endpoint is always public.
 */
export async function checkSetupRequired(): Promise<SetupRequiredResponse> {
  return fetchJson(`${API_BASE}/auth/setup-required`);
}

/**
 * Complete first-time setup by creating the initial admin user.
 * Only works when no users exist in the system.
 */
export async function completeSetup(request: SetupRequest): Promise<SetupResponse> {
  return fetchJson(`${API_BASE}/auth/setup`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/**
 * Request a password reset email.
 * Always returns success (to prevent email enumeration).
 */
export async function forgotPassword(email: string): Promise<{ message: string }> {
  return fetchJson(`${API_BASE}/auth/forgot-password`, {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

/**
 * Reset password using a reset token.
 * Token is sent via email from forgotPassword.
 */
export async function resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
  return fetchJson(`${API_BASE}/auth/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

// =============================================================================
// Admin Auth Settings API
// =============================================================================

/**
 * Get auth settings (admin only).
 * Returns settings with sensitive data excluded.
 */
export async function getAuthSettings(): Promise<AuthSettingsPublic> {
  return fetchJson(`${API_BASE}/auth/admin/settings`, {
    credentials: 'include',
  });
}

/**
 * Update auth settings (admin only).
 * Only provided fields are updated.
 */
export async function updateAuthSettings(settings: AuthSettingsUpdate): Promise<{ message: string }> {
  return fetchJson(`${API_BASE}/auth/admin/settings`, {
    method: 'PUT',
    body: JSON.stringify(settings),
    credentials: 'include',
  });
}

// =============================================================================
// Admin User Management API
// =============================================================================

/**
 * List all users (admin only).
 */
export async function listUsers(): Promise<UserListResponse> {
  return fetchJson(`${API_BASE}/auth/admin/users`, {
    credentials: 'include',
  });
}

/**
 * Update user (admin only).
 */
export async function updateUser(userId: number, data: UserUpdateRequest): Promise<UserUpdateResponse> {
  return fetchJson(`${API_BASE}/auth/admin/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Delete user (admin only).
 */
export async function deleteUser(userId: number): Promise<{ message: string }> {
  return fetchJson(`${API_BASE}/auth/admin/users/${userId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

// =============================================================================
// User Profile API
// =============================================================================

/**
 * Update current user's profile.
 */
export async function updateProfile(data: UpdateProfileRequest): Promise<UpdateProfileResponse> {
  return fetchJson(`${API_BASE}/auth/me`, {
    method: 'PUT',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Change current user's password.
 */
export async function changePassword(data: ChangePasswordRequest): Promise<ChangePasswordResponse> {
  return fetchJson(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

// =============================================================================
// Linked Identities API (Account Linking)
// =============================================================================

/**
 * Get all identities linked to the current user's account.
 */
export async function getLinkedIdentities(): Promise<LinkedIdentitiesResponse> {
  return fetchJson(`${API_BASE}/auth/identities`, {
    credentials: 'include',
  });
}

/**
 * Link a new identity to the current user's account.
 * Requires valid credentials for the target provider.
 */
export async function linkIdentity(data: LinkIdentityRequest): Promise<LinkIdentityResponse> {
  return fetchJson(`${API_BASE}/auth/identities/link`, {
    method: 'POST',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Unlink an identity from the current user's account.
 * Cannot unlink the last remaining identity.
 */
export async function unlinkIdentity(identityId: number): Promise<UnlinkIdentityResponse> {
  return fetchJson(`${API_BASE}/auth/identities/${identityId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

// =============================================================================
// TLS Certificate Management API
// =============================================================================

/**
 * Get TLS configuration status.
 */
export async function getTLSStatus(): Promise<TLSStatus> {
  return fetchJson(`${API_BASE}/tls/status`, {
    credentials: 'include',
  });
}

/**
 * Get TLS settings (for form).
 */
export async function getTLSSettings(): Promise<TLSSettings> {
  return fetchJson(`${API_BASE}/tls/settings`, {
    credentials: 'include',
  });
}

/**
 * Configure TLS settings.
 */
export async function configureTLS(settings: TLSConfigureRequest): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/tls/configure`, {
    method: 'POST',
    body: JSON.stringify(settings),
    credentials: 'include',
  });
}

/**
 * Request a Let's Encrypt certificate.
 */
export async function requestCertificate(): Promise<CertificateRequestResponse> {
  return fetchJson(`${API_BASE}/tls/request-cert`, {
    method: 'POST',
    credentials: 'include',
  });
}

/**
 * Complete a pending DNS-01 challenge.
 */
export async function completeDNSChallenge(): Promise<CertificateRequestResponse> {
  return fetchJson(`${API_BASE}/tls/complete-challenge`, {
    method: 'POST',
    credentials: 'include',
  });
}

/**
 * Upload a certificate and key manually.
 */
export async function uploadCertificate(
  certFile: File,
  keyFile: File,
  chainFile?: File
): Promise<{ success: boolean; message: string; expires_at?: string }> {
  const formData = new FormData();
  formData.append('cert_file', certFile);
  formData.append('key_file', keyFile);
  if (chainFile) {
    formData.append('chain_file', chainFile);
  }

  const response = await fetch(`${API_BASE}/tls/upload-cert`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to upload certificate');
  }

  return response.json();
}

/**
 * Trigger certificate renewal.
 */
export async function renewCertificate(): Promise<{ success: boolean; message: string; expires_at?: string }> {
  return fetchJson(`${API_BASE}/tls/renew`, {
    method: 'POST',
    credentials: 'include',
  });
}

/**
 * Delete certificate and disable TLS.
 */
export async function deleteCertificate(): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/tls/certificate`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

/**
 * Test DNS provider credentials.
 */
export async function testDNSProvider(data: DNSProviderTestRequest): Promise<DNSProviderTestResponse> {
  return fetchJson(`${API_BASE}/tls/test-dns-provider`, {
    method: 'POST',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

// =============================================================================
// Dummy EPG (v0.14.0)
// =============================================================================

/**
 * List all Dummy EPG profiles.
 */
export async function getDummyEPGProfiles(): Promise<DummyEPGProfile[]> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles`, { credentials: 'include' });
}

/**
 * Get a single Dummy EPG profile with channel assignments.
 */
export async function getDummyEPGProfile(profileId: number): Promise<DummyEPGProfile> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}`, { credentials: 'include' });
}

/**
 * Create a Dummy EPG profile.
 */
export async function createDummyEPGProfile(data: DummyEPGProfileCreateRequest): Promise<DummyEPGProfile> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles`, {
    method: 'POST',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Update a Dummy EPG profile (partial).
 */
export async function updateDummyEPGProfile(profileId: number, data: DummyEPGProfileUpdateRequest): Promise<DummyEPGProfile> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Delete a Dummy EPG profile (cascades assignments).
 */
export async function deleteDummyEPGProfile(profileId: number): Promise<void> {
  await fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

/**
 * Preview EPG pipeline (no DB).
 */
export async function previewDummyEPG(data: DummyEPGPreviewRequest): Promise<DummyEPGPreviewResult> {
  return fetchJson(`${API_BASE}/dummy-epg/preview`, {
    method: 'POST',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Batch preview EPG pipeline (no DB).
 */
export async function previewDummyEPGBatch(data: DummyEPGBatchPreviewRequest): Promise<DummyEPGPreviewResult[]> {
  return fetchJson(`${API_BASE}/dummy-epg/preview/batch`, {
    method: 'POST',
    body: JSON.stringify(data),
    credentials: 'include',
  });
}

/**
 * Get combined XMLTV URL for all enabled profiles.
 */
export function getDummyEPGXmltvUrl(): string {
  return `${window.location.origin}${API_BASE}/dummy-epg/xmltv`;
}

/**
 * Get XMLTV URL for a single profile.
 */
export function getDummyEPGProfileXmltvUrl(profileId: number): string {
  return `${window.location.origin}${API_BASE}/dummy-epg/xmltv/${profileId}`;
}

/**
 * Export all Dummy EPG profiles as YAML.
 */
export async function exportDummyEPGProfilesYAML(): Promise<string> {
  return fetchText(`${API_BASE}/dummy-epg/profiles/export/yaml`);
}

/**
 * Import Dummy EPG profiles from YAML.
 */
export async function importDummyEPGProfilesYAML(
  yamlContent: string,
  overwrite?: boolean
): Promise<{ success: boolean; imported: { name: string; action: string }[]; errors: { profile_index: number; profile_name: string; errors: string[] }[] }> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles/import/yaml`, {
    method: 'POST',
    body: JSON.stringify({
      yaml_content: yamlContent,
      overwrite: overwrite ?? false,
    }),
  });
}

/**
 * Force regeneration of XMLTV cache.
 */
export async function regenerateDummyEPG(): Promise<{ status: string; profiles: number; channels: number }> {
  return fetchJson(`${API_BASE}/dummy-epg/generate`, {
    method: 'POST',
    credentials: 'include',
  });
}

// ============================================================================
// Dummy EPG Channel Assignments
// ============================================================================

export async function getDummyEPGChannels(profileId: number): Promise<DummyEPGChannelAssignment[]> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}/channels`, { credentials: 'include' });
}

export async function assignDummyEPGChannels(profileId: number, channelIds: number[]): Promise<{ created: number }> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel_ids: channelIds }),
    credentials: 'include',
  });
}

export async function removeDummyEPGChannel(profileId: number, channelId: number): Promise<void> {
  await fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}/channels/${channelId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

export async function assignDummyEPGChannelsFromGroup(profileId: number, groupId: number): Promise<{ created: number }> {
  return fetchJson(`${API_BASE}/dummy-epg/profiles/${profileId}/channels/from-group/${groupId}`, {
    method: 'POST',
    credentials: 'include',
  });
}

// ============================================================================
// Backup & Restore
// ============================================================================

// ── ZIP Backup (legacy) ──

export function getBackupDownloadUrl(): string {
  return `${API_BASE}/backup/create`;
}

export interface RestoreResult {
  status: string;
  backup_version: string;
  backup_date: string;
  restored_files: string[];
}

export async function restoreBackup(file: File): Promise<RestoreResult> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/backup/restore`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Restore failed' }));
    throw new Error(error.detail || 'Restore failed');
  }

  return response.json();
}

export async function restoreBackupInitial(file: File): Promise<RestoreResult> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/backup/restore-initial`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Restore failed' }));
    throw new Error(error.detail || 'Restore failed');
  }

  return response.json();
}

// ── YAML Export / Validate / Selective Restore ──

export interface BackupSectionInfo {
  key: string;
  label: string;
  item_count: number;
  available: boolean;
}

export interface BackupValidation {
  valid: boolean;
  version: string | null;
  exported_at: string | null;
  sections: BackupSectionInfo[];
}

export interface BackupRestoreResult {
  success: boolean;
  sections_restored: string[];
  sections_failed: string[];
  warnings: string[];
  errors: string[];
}

// ---------------------------------------------------------------------------
// DBAS Phase-2 restore report (mirrors backend/dbas/restore_contracts.py)
//
// ONE shape carries the dry-run plan (bead 0i2vt.16) AND the realized
// restore/rollback result (bead 0i2vt.18); the restore-complete summary UX
// (bead 0i2vt.20) renders both, distinguished by `is_dry_run`. Keep the enum
// string values byte-for-byte aligned with the backend `str, Enum` values —
// the wire payload carries the raw string and the UI maps it to a label.
// ---------------------------------------------------------------------------

/** A restorable Dispatcharr/ECM entity type (one per distinct ID namespace). */
export type RestoreEntityType =
  | 'm3u_account'
  | 'epg_source'
  | 'channel_group'
  | 'channel_profile'
  | 'stream_profile'
  | 'channel'
  | 'stream'
  | 'user_agent'
  | 'dvr_rule'
  | 'user'
  | 'logo';

/** Why an entity was (or would be) skipped. */
export type RestoreSkipReason =
  | 'already_exists_identical'
  | 'excluded_by_operator'
  | 'current_admin_preserved'
  | 'unsupported_in_this_version'
  | 'dependency_unresolved';

/** Why an entity failed to apply. */
export type RestoreFailureReason =
  | 'validation_error'
  | 'dependency_unresolved'
  | 'upstream_api_error'
  | 'upstream_timeout'
  | 'conflict'
  | 'password_hash_unsupported'
  | 'internal_error';

/**
 * Overall tri-state result of a realized restore. `null` on a dry-run (a plan
 * has no realized outcome). NEVER `success` on mixed state — the two rolled-back
 * states are explicit failures, surfaced as such by the summary UX.
 */
export type RestoreOutcome =
  | 'success'
  | 'partial_failed_rolled_back'
  | 'failed_rollback_incomplete';

/** One skipped entity, with the reason and an operator-facing label (never a secret). */
export interface RestoreSkipDetail {
  reason: RestoreSkipReason;
  label: string;
  source_export_id?: number | null;
}

/** One failed entity, with the reason and a sanitized operator-facing message. */
export interface RestoreFailureDetail {
  reason: RestoreFailureReason;
  label: string;
  message: string;
  source_export_id?: number | null;
}

/**
 * Per-entity-category counts for ONE category. Apply populates
 * created/updated/skipped/failed; dry-run populates would_create/would_update/
 * would_skip. The detail lists are the source of truth for reasons.
 */
export interface EntityCategoryReport {
  entity_type: RestoreEntityType;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  would_create: number;
  would_update: number;
  would_skip: number;
  skip_details: RestoreSkipDetail[];
  failure_details: RestoreFailureDetail[];
}

/**
 * One logo that could not be matched/applied on restore (bead qhui4) — the
 * per-logo drill-down behind the aggregate `logo_misses` count. `label` is the
 * operator-facing logo name; never a path or secret.
 */
export interface LogoMissDetail {
  source_export_id?: number | null;
  label: string;
}

/** The one restore response schema — dry-run, apply, and summary. */
export interface RestoreReport {
  contract_version: number;
  is_dry_run: boolean;
  outcome: RestoreOutcome | null;
  categories: EntityCategoryReport[];
  /** Aggregate count of unresolved logo references — bead .19 surfaces a red banner when > 0. */
  logo_misses: number;
  /**
   * Per-logo detail (id + name) for each unresolved logo (bead qhui4). Additive
   * to the aggregate count: the banner enumerates these as a drill-down list.
   * May be absent on reports produced before this field existed.
   */
  logo_miss_details?: LogoMissDetail[];
  started_at?: string | null;
  completed_at?: string | null;
  notes: string[];
}

export async function getExportSections(): Promise<{key: string; label: string}[]> {
  return fetchJson(`${API_BASE}/backup/export-sections`);
}

export async function exportBackup(sections?: string[]): Promise<Blob> {
  let url = `${API_BASE}/backup/export`;
  if (sections && sections.length > 0) {
    url += `?sections=${sections.join(',')}`;
  }
  const response = await fetch(url, {
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Export failed' }));
    throw new Error(error.detail || 'Export failed');
  }

  return response.blob();
}

// Saved backups (on-disk files from scheduled task)

export interface SavedBackup {
  filename: string;
  size_bytes: number;
  created_at: string;
  /** "zip" (full on-demand or DBAS backup) or "yaml" (scheduled section export). */
  type: 'zip' | 'yaml';
}

export async function listSavedBackups(): Promise<SavedBackup[]> {
  return fetchJson(`${API_BASE}/backup/saved`);
}

export function getSavedBackupDownloadUrl(filename: string): string {
  return `${API_BASE}/backup/saved/${encodeURIComponent(filename)}`;
}

export async function deleteSavedBackup(filename: string): Promise<void> {
  const response = await fetch(`${API_BASE}/backup/saved/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
    credentials: 'include',
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(error.detail || 'Delete failed');
  }
}

export async function validateBackup(file: File): Promise<BackupValidation> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/backup/validate`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Validation failed' }));
    throw new Error(error.detail || 'Validation failed');
  }

  return response.json();
}

export async function restoreBackupYaml(file: File, sections: string[]): Promise<BackupRestoreResult> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('sections', JSON.stringify(sections));

  const response = await fetch(`${API_BASE}/backup/restore-yaml`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Restore failed' }));
    throw new Error(error.detail || 'Restore failed');
  }

  return response.json();
}

/** Response of the async DBAS restore-trigger endpoint (bead o8tbv). */
export interface DbasRestoreStartResult {
  status: string;
  /** Poll `/api/tasks/{task_id}` for per-stage progress + the terminal report. */
  task_id: string;
  /** True when the run is a counts-only dry-run (default; apply requires confirm). */
  is_dry_run: boolean;
}

/**
 * Trigger an async DBAS artifact restore (bead o8tbv).
 *
 * Streams the new-format artifact (.zip) to the backend, which kicks the
 * `dbas_restore` task in the background and returns its `task_id`. The caller
 * polls `/api/tasks/{task_id}` (see `useRestoreProgress`) for the 13-stage
 * progress and the terminal `RestoreReport`.
 *
 * Dry-run is default-ON: pass `confirmApply=true` for the destructive apply.
 *
 * For an encrypted artifact (ADR-012 D12 / u81kh) pass the operator
 * `passphrase`; it travels as a form field (never the query string, so it does
 * not land in access logs). Omit it for a plain artifact.
 */
export async function startDbasRestore(
  file: File,
  confirmApply = false,
  passphrase?: string
): Promise<DbasRestoreStartResult> {
  const formData = new FormData();
  formData.append('file', file);
  if (passphrase) formData.append('passphrase', passphrase);

  const query = buildQuery({ confirm_apply: confirmApply });
  const response = await fetch(`${API_BASE}/backup/restore-dbas${query}`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Restore failed' }));
    throw new Error(error.detail || 'Restore failed');
  }

  return response.json();
}

/**
 * Restore ECM configuration from an on-disk SAVED backup ZIP (legacy full-
 * archive format written by POST /backup/save or the scheduled YAML backup
 * task). Synchronous — the same shape as `restoreBackup` (upload path), just
 * addressed by filename instead of a File. Human-admin only server-side
 * (enhancedchannelmanager-rzhid).
 *
 * NOTE: `GET /backup/saved` cannot distinguish this legacy ZIP format from a
 * DBAS-format artifact — both share the `ecm-backup-<ts>.zip` naming
 * convention. Callers should let the operator pick the matching restore
 * action (this one, or `restoreDbasBackupSaved`) rather than guessing.
 */
export async function restoreSavedBackup(filename: string): Promise<RestoreResult> {
  return fetchJson(`${API_BASE}/backup/restore-saved`, {
    method: 'POST',
    body: JSON.stringify({ filename }),
  });
}

/**
 * Trigger an async DBAS restore from an on-disk SAVED artifact (bead
 * enhancedchannelmanager-rzhid). SAVED-file analogue of `startDbasRestore`
 * (upload path) — same dry-run-by-default contract (`confirmApply=false`
 * makes zero mutation), addressed by filename instead of a File.
 */
export async function restoreDbasBackupSaved(
  filename: string,
  confirmApply = false,
  passphrase?: string,
): Promise<DbasRestoreStartResult> {
  return fetchJson(`${API_BASE}/backup/restore-dbas-saved`, {
    method: 'POST',
    body: JSON.stringify({
      filename,
      confirm_apply: confirmApply,
      ...(passphrase ? { passphrase } : {}),
    }),
  });
}

// ── Alert Methods API ───────────────────────────────────────────────

export interface AlertMethod {
  id: number;
  name: string;
  method_type: string;
  enabled: boolean;
  config: Record<string, unknown>;
  notify_info: boolean;
  notify_success: boolean;
  notify_warning: boolean;
  notify_error: boolean;
}

export interface AlertMethodCreateRequest {
  name: string;
  method_type: string;
  config: Record<string, unknown>;
  enabled?: boolean;
  notify_info?: boolean;
  notify_success?: boolean;
  notify_warning?: boolean;
  notify_error?: boolean;
  alert_sources?: Record<string, unknown> | null;
}

export interface AlertMethodUpdateRequest {
  name?: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
  notify_info?: boolean;
  notify_success?: boolean;
  notify_warning?: boolean;
  notify_error?: boolean;
  alert_sources?: Record<string, unknown> | null;
}

export async function listAlertMethods(): Promise<AlertMethod[]> {
  return fetchJson(`${API_BASE}/alert-methods`);
}

export async function createAlertMethod(data: AlertMethodCreateRequest): Promise<{ id: number; name: string; method_type: string; enabled: boolean }> {
  return fetchJson(`${API_BASE}/alert-methods`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateAlertMethod(methodId: number, data: AlertMethodUpdateRequest): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/alert-methods/${methodId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteAlertMethod(methodId: number): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/alert-methods/${methodId}`, {
    method: 'DELETE',
  });
}

export async function testAlertMethod(methodId: number): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/alert-methods/${methodId}/test`, {
    method: 'POST',
  });
}

// ---------------------------------------------------------------------------
// Lookup Tables (dummy EPG template engine |lookup:<name> pipe)
// ---------------------------------------------------------------------------

export interface LookupTableSummary {
  id: number;
  name: string;
  description: string | null;
  entry_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface LookupTable extends LookupTableSummary {
  entries: Record<string, string>;
}

export interface LookupTableCreateRequest {
  name: string;
  description?: string;
  entries?: Record<string, string>;
}

export interface LookupTableUpdateRequest {
  name?: string;
  description?: string;
  entries?: Record<string, string>;
}

export async function listLookupTables(): Promise<LookupTableSummary[]> {
  return fetchJson(`${API_BASE}/lookup-tables`);
}

export async function getLookupTable(id: number): Promise<LookupTable> {
  return fetchJson(`${API_BASE}/lookup-tables/${id}`);
}

export async function createLookupTable(data: LookupTableCreateRequest): Promise<LookupTable> {
  return fetchJson(`${API_BASE}/lookup-tables`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateLookupTable(id: number, data: LookupTableUpdateRequest): Promise<LookupTable> {
  return fetchJson(`${API_BASE}/lookup-tables/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteLookupTable(id: number): Promise<void> {
  return fetchJson(`${API_BASE}/lookup-tables/${id}`, { method: 'DELETE' });
}

// =============================================================================
// Channel Merges (Pending Merges queue) — ADR-008 §D1
// =============================================================================
//
// The pending_merges queue is populated by:
//   • the bulk M3U import dedup hook (BD-F / bd-a5lb2)
//   • the drag-drop and Add Stream surfaces (BD-H, BD-I)
//
// and consumed by the Pending Merges page (BD-J / bd-gfxrz) which renders one
// row per queued candidate with operator-facing Merge / Create New actions.
//
// The list endpoint is GET-only and `RequireAuthIfEnabled`; the accept and
// dismiss endpoints are `RequireAdminIfEnabled` (they mutate either ECM state
// or Dispatcharr channel structure). All three follow the ECM flat-outcome
// response envelope established by `POST /api/channels/merge` (no top-level
// `data` wrapper).

/**
 * One pending_merges row, as projected by `GET /api/channel-merges`.
 *
 * Field set matches the backend `PendingMergeRecord` Pydantic model in
 * `backend/routers/channel_merges.py`. `confidence` is a 0.0–1.0 float
 * captured at queue-time; the UI renders it as an integer-percent badge.
 * `created_at` and `resolved_at` are epoch-ms integers per ADR-007/§D8.
 */
export interface PendingMergeRecord {
  id: number;
  stream_name: string;
  group_id: number | null;
  candidate_channel_id: string;
  confidence: number;
  status: 'pending' | 'merged' | 'dismissed';
  created_at: number;
  resolved_at: number | null;
  resolution_source: string | null;
  trigger_context: string;
}

/** Paginated envelope for `GET /api/channel-merges`. */
export interface PendingMergesListResponse {
  merges: PendingMergeRecord[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/**
 * Flat-outcome envelope for `POST /api/channel-merges/{id}/accept` per
 * ADR-008 §D1. Mirrors the `AcceptOutcome` Pydantic model.
 */
export interface AcceptMergeOutcome {
  merged_into_channel_id: string;
  journal_entry_id: number;
  source_stream_id: string;
  confidence: number;
  status: 'merged';
}

/**
 * Flat-outcome envelope for `POST /api/channel-merges/{id}/dismiss` per
 * ADR-008 §D1. Mirrors the `DismissOutcome` Pydantic model.
 */
export interface DismissMergeOutcome {
  journal_entry_id: number;
  status: 'dismissed';
}

/**
 * List pending merge queue rows. Defaults match BD-J's Pending Merges page:
 * status='pending', page=1, page_size=50. Pass `group_id` to scope to a
 * single channel group; omit to list across all groups.
 */
export async function getPendingMerges(params?: {
  status?: 'pending' | 'merged' | 'dismissed';
  groupId?: number;
  page?: number;
  pageSize?: number;
}): Promise<PendingMergesListResponse> {
  const query = buildQuery({
    status: params?.status ?? 'pending',
    group_id: params?.groupId,
    page: params?.page ?? 1,
    page_size: params?.pageSize ?? 50,
  });
  return fetchJson(`${API_BASE}/channel-merges${query}`);
}

/**
 * Accept a pending merge (operator confirms the candidate match).
 * Idempotent on terminal `merged` (returns the prior outcome envelope);
 * 409 on terminal `dismissed`; 404 if the candidate channel has been
 * deleted in Dispatcharr since the row was queued (ADR-008 §D4 lazy
 * resolution — the operator's recovery is `/dismiss` + re-trigger).
 */
export async function acceptPendingMerge(mergeId: number): Promise<AcceptMergeOutcome> {
  return fetchJson(`${API_BASE}/channel-merges/${mergeId}/accept`, {
    method: 'POST',
  });
}

/**
 * Dismiss a pending merge (operator rejects the candidate). Idempotent on
 * terminal `dismissed`; 409 on terminal `merged`. The downstream creation
 * path (drag-drop, Add Stream, M3U refresh) is the operator's next step
 * — `dismiss` is purely an ECM-side state flip plus audit-journal row.
 */
export async function dismissPendingMerge(mergeId: number): Promise<DismissMergeOutcome> {
  return fetchJson(`${API_BASE}/channel-merges/${mergeId}/dismiss`, {
    method: 'POST',
  });
}

// -------------------------------------------------------------------------
// Sync Targets — cross-instance live-sync destinations (epic i39wu).
//
// A SyncTarget is a remote Dispatcharr-B instance ECM pushes config to via the
// one-way sync engine. The CRUD mirrors backend/routers/sync_targets.py:
// credentials are WRITE-ONLY (Fernet-encrypted at rest, never echoed back
// decrypted — responses mask them to last-4). The actual sync is driven by
// `runTask('dbas_sync', undefined, { sync_target_id, confirm_apply })`.
// -------------------------------------------------------------------------

/**
 * Read shape of a sync target. `credentials` is always masked (last-4 only) —
 * never plaintext. Status fields (`last_full_sync_at`, `last_outcome`) drive
 * the per-target badge in the UI.
 */
export interface SyncTarget {
  id: number;
  name: string;
  base_url: string;
  credentials: Record<string, unknown>;
  enabled: boolean;
  insecure: boolean;
  fuzzy_stream_matching: boolean;
  credential_version: number;
  token_revoked_at?: string | null;
  last_full_sync_at?: string | null;
  last_outcome?: string | null;
  last_source_fingerprint?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Create payload — `credentials` is write-only (username/password or api_key). */
export interface SyncTargetCreateRequest {
  name: string;
  base_url: string;
  credentials?: Record<string, string>;
  enabled?: boolean;
  insecure?: boolean;
  fuzzy_stream_matching?: boolean;
}

/**
 * Update payload — all fields optional (partial update). Omit `credentials`
 * to leave the stored secret untouched; supplying it re-encrypts and bumps
 * `credential_version`. The `enabled` toggle is the KILL SWITCH.
 */
export interface SyncTargetUpdateRequest {
  name?: string;
  base_url?: string;
  credentials?: Record<string, string>;
  enabled?: boolean;
  insecure?: boolean;
  fuzzy_stream_matching?: boolean;
}

export async function listSyncTargets(): Promise<SyncTarget[]> {
  return fetchJson(`${API_BASE}/sync-targets`);
}

export async function createSyncTarget(req: SyncTargetCreateRequest): Promise<SyncTarget> {
  return fetchJson(`${API_BASE}/sync-targets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function updateSyncTarget(
  id: number,
  req: SyncTargetUpdateRequest,
): Promise<SyncTarget> {
  return fetchJson(`${API_BASE}/sync-targets/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function deleteSyncTarget(id: number): Promise<void> {
  await fetchJson(`${API_BASE}/sync-targets/${id}`, { method: 'DELETE' });
}
