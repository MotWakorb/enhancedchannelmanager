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
  StaleStreamIdsResponse,
} from '../types';
import type {
  AcceptEventSyncReviewOutcome,
  EventSyncExclusionCreateRequest,
  EventSyncExclusionRecord,
  EventSyncExclusionsListResponse,
  EventSyncReviewsListResponse,
  RejectEventSyncReviewOutcome,
} from '../types/eventSync';
import { logger } from '../utils/logger';
import { voidBackupBannerSnoozeIfScheduled } from '../utils/backupBannerSnooze';
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
  resolveCreateChannelNames,
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
  resolveCreateChannelNames,
};
// `ResolvedCreateChannelNames` is a class, not an interface: it is exported as
// a VALUE so nothing can satisfy it with an object literal carrying a plain
// `Map`. Reintroducing the map is reintroducing the missing-entry case, which
// is the defect (bead enhancedchannelmanager-e9e5o).
export { ResolvedCreateChannelNames, NormalizationIncompleteError } from './streamNormalization';

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

export async function findDuplicateChannels(
  channelIds?: number[],
  foldMatchKey?: boolean,
): Promise<FindDuplicatesResponse> {
  // fold_match_key (GH #645): opt-in whitespace/case-insensitive grouping —
  // the same canonicalization the auto-creation fold_match_key rule flag
  // uses. Only sent when true so the default request stays byte-identical
  // to the pre-flag behavior.
  const body: { channel_ids?: number[]; fold_match_key?: boolean } = {};
  if (channelIds !== undefined) body.channel_ids = channelIds;
  if (foldMatchKey) body.fold_match_key = true;
  return fetchJson(`${API_BASE}/channels/find-duplicates`, {
    method: 'POST',
    ...(Object.keys(body).length > 0 ? { body: JSON.stringify(body) } : {}),
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
  /**
   * Channels a failed numbering plan left on the wrong number, which the
   * compensating write could not put back either (bead
   * `enhancedchannelmanager-ic884.3`). Each entry names the channel, where it
   * is, where it should be, and the one action that closes the gap.
   *
   * Non-empty is the ONLY case where neither the previous numbering nor the
   * proposed one is what the operator is left with, so it must reach them
   * rather than sitting in the envelope: an unexplained middle is the outcome
   * the whole compensation pass exists to prevent.
   */
  numberingRecovery?: {
    channelId: number;
    channelName: string;
    currentNumber: number | null;
    targetNumber: number | null;
    step: string;
    error: string;
  }[];
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
export async function bulkCommit(
  request: BulkCommitRequest,
  batchId?: string,
): Promise<BulkCommitResponse> {
  // One Apply All fans out into a create request plus N batches of 200. Sending
  // the same correlation id on all of them puts every journal row of that
  // session under one batch, which is how a channel's history reads as one
  // event instead of several unrelated ones (bead enhancedchannelmanager-r9py9).
  // The backend middleware reads this header; omitting it mints a per-request
  // id exactly as before.
  const headers = batchId ? { 'X-ECM-Batch-Id': batchId } : undefined;

  if (request.validateOnly) {
    return fetchJson(`${API_BASE}/channels/bulk-commit`, {
      method: 'POST',
      body: JSON.stringify(request),
      headers,
    });
  }

  const accepted = await fetchJson<BulkCommitJobAccepted>(`${API_BASE}/channels/bulk-commit`, {
    method: 'POST',
    body: JSON.stringify(request),
    headers,
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

/** Per-provider catch-up availability for the M3U manager badge (bead 4dpiz).
 *  `has_catchup` is authoritative (true when the provider has ≥1 catch-up
 *  stream); `catchup_days` is the provider's sampled catch-up depth (null when
 *  no catch-up). Keyed by M3U account id as a string. */
export interface ProviderCatchupStatus {
  has_catchup: boolean;
  catchup_days: number | null;
}

export async function getProviderCatchupStatus(): Promise<Record<string, ProviderCatchupStatus>> {
  return fetchJson(`${API_BASE}/providers/catchup-status`);
}

/** One (provider, channel-group) junction row — NON-collapsed. bead 38dzi:
 *  powers the provider-scoped Event Sync group picker (the same channel-group
 *  id can appear under multiple providers). Join on channel_group_id against
 *  the channel groups the caller already loads for the group name. */
export interface ProviderGroupScopeRow {
  m3u_account_id: number;
  m3u_account_name: string;
  channel_group_id: number;
  auto_channel_sync: boolean;
  enabled: boolean;
  stream_count: number | null;
}

export async function getProviderGroupSettingsByProvider(): Promise<ProviderGroupScopeRow[]> {
  return fetchJson(`${API_BASE}/providers/group-settings/by-provider`);
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

export interface DeleteM3UAccountResult {
  status: string;
  deleted_groups: number[];
  skipped_groups: number[];
  failed_groups: Array<{ id: number; error: string }>;
  account_deleted?: boolean;
  linked_settings_cleanup?: 'failed';
  message?: string;
}

export async function deleteM3UAccount(id: number): Promise<DeleteM3UAccountResult> {
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
/**
 * Per-group outcome of the downstream channel-profile reconcile the
 * group-settings PATCH performs (GH #720 Part B / #9). Best-effort — a
 * reconcile problem never fails the PATCH, but it IS reported here so the UI
 * can warn on an incomplete apply.
 */
export interface ProfileApplyOutcome {
  // Backend statuses: no_selection | no_channels | stale_selection |
  // partial_failure | reconciled | error.
  status: string;
  group_id?: number | null;
  failed_profile_ids?: number[];
  conflict?: boolean;
  channels_scoped?: number;
  error?: string | null;
}

export async function updateM3UGroupSettings(
  accountId: number,
  data: { group_settings: Partial<ChannelGroupM3UAccount>[] }
): Promise<{ message?: string; ecm_profile_apply?: ProfileApplyOutcome[] }> {
  // Dispatcharr expects 'group_settings' key, not 'channel_groups'
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/group-settings`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * True when a group-settings save's profile-apply summary reports an
 * INCOMPLETE or dead apply — any per-group partial_failure/degraded/error, a
 * fully stale (all-deleted) selection, a cross-account conflict, or a non-empty
 * failed_profile_ids. Drives the "saved but apply incomplete" warning (#9).
 * An empty summary is a clean no-op (nothing to apply) — NOT incomplete; the
 * backend emits an explicit {status:'error'} entry when setup itself failed so
 * that case is distinguished from "nothing to do".
 */
export function profileApplyIncomplete(
  summary: ProfileApplyOutcome[] | undefined
): boolean {
  return (summary ?? []).some(
    (o) =>
      o.status === 'partial_failure' ||
      o.status === 'degraded' ||
      o.status === 'error' ||
      o.status === 'stale_selection' ||
      o.conflict === true ||
      (o.failed_profile_ids?.length ?? 0) > 0
  );
}

/**
 * Status-specific recovery guidance for an incomplete profile apply. Returns
 * null when the apply is clean. The generic "it will retry automatically"
 * message is WRONG for stale_selection and conflict (auto-retry cannot fix a
 * deleted profile or contradictory operator choices), so those get an
 * actionable next step instead of a false promise.
 */
export function profileApplyWarningMessage(
  summary: ProfileApplyOutcome[] | undefined
): string | null {
  const items = summary ?? [];
  if (!profileApplyIncomplete(items)) return null;
  if (items.some((o) => o.status === 'stale_selection')) {
    return 'Saved, but the selected channel profile(s) no longer exist — open Auto-Sync settings and choose current profiles.';
  }
  if (items.some((o) => o.conflict === true)) {
    return 'Saved, but this group had conflicting profile selections across accounts — reopen Auto-Sync settings and re-save to normalize them.';
  }
  if (items.some((o) => o.status === 'degraded')) {
    return 'Saved, but channel profiles could not be fully enforced (the profile list was unreachable). It will retry automatically on the next sync.';
  }
  // Cheap honesty: the backend already NAMES the affected account(s) + the
  // recovery action in outcome.error — surface it instead of a generic
  // "check the logs" when present.
  const errWithDetail = items.find((o) => o.status === 'error' && o.error);
  if (errWithDetail) {
    return `Saved, but ${errWithDetail.error}`;
  }
  if (items.some((o) => o.status === 'error')) {
    return 'Saved, but applying channel profiles hit an error — check the logs.';
  }
  return 'Saved, but applying some channel profiles failed — check the logs; it will retry automatically.';
}

export interface GroupAutoSyncToggleResult {
  changed: boolean;
  channel_group_id: number;
  group_name: string;
  account_id: number;
  account_name: string;
  auto_channel_sync: boolean;
  was?: boolean;
}

/**
 * Guided-setup auto_channel_sync toggle (bead ti939.3.4). Admin-gated and
 * confirm-gated on the backend: `confirm: true` is REQUIRED and must only
 * be sent from an explicit confirmation dialog — never as a side effect of
 * saving a rule or running the pipeline. Every toggle is journaled
 * (snapshot restore does NOT revert Dispatcharr group settings; the
 * journal entry is the recovery breadcrumb).
 */
export async function toggleGroupAutoSync(
  accountId: number,
  data: { channel_group_id: number; auto_channel_sync: boolean; confirm: true }
): Promise<GroupAutoSyncToggleResult> {
  return fetchJson(`${API_BASE}/m3u/accounts/${accountId}/group-auto-sync-toggle`, {
    method: 'POST',
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

// The update check used to live here (UpdateInfo / isNewerVersion /
// checkForUpdates) and fed the header's "Update available" pill. Bead
// enhancedchannelmanager-nhkd4 moved it to backend/services/version_check.py,
// which reconciles a single notification-centre entry instead: one writer per
// container rather than one per open tab, and the notice retires itself once
// the operator has updated. Nothing in the frontend calls GitHub any more.

// Settings
export type Theme = 'dark' | 'light' | 'high-contrast';

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'WARNING' | 'ERROR' | 'CRITICAL';

// Sort criteria for stream sorting
export type SortCriterion = 'resolution' | 'bitrate' | 'framerate' | 'video_codec' | 'm3u_priority' | 'audio_channels' | 'custom_streams' | 'catchup';
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
  backend_log_file_max_bytes?: number;  // Restart-scoped persistent log file size; absent on older backends
  backend_log_file_backup_count?: number;  // Restart-scoped rotated backup count; absent on older backends
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
  // Canonical public origin (scheme://host[:port]) for links ECM emails out.
  // '' means unset, in which case those links are built from caller-supplied
  // request headers (bead qsqfv). Admin-only on write.
  public_base_url: string;
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
  // first-run wizard + Settings > Backup & Restore (relocated from the
  // removed Security page by bead 09x38.12). The always-on denylist is
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
 * Settings > Backup & Restore card (OutboundPolicyCard, relocated from the
 * removed Security page by bead 09x38.12) and the first-run wizard can save
 * the operator's choice without a full settings round-trip. Returns the
 * saved mode.
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
  // Non-blocking advisory returned alongside a SUCCESSFUL Dispatcharr
  // connection test — today, "this Dispatcharr version is outside the set ECM's
  // API contract is recorded against" (ADR-014, bead ax0kf). Never a failure:
  // ``success`` stays true and the connection still saves.
  warning?: string;
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
  backend_log_file_max_bytes?: number;  // Optional - admin-only, applied after restart
  backend_log_file_backup_count?: number;  // Optional - admin-only, applied after restart
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
  public_base_url?: string;  // Optional - public origin for emailed links; omit to keep the stored value, '' clears it
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
}): Promise<{
  status: string;
  configured: boolean;
  server_changed: boolean;
  restart_required?: boolean;
}> {
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
  // Self-diagnosing /health diagnostic (bd-ix1g6). Distinguishes the ways a
  // key can be missing so the Settings UI Server Status panel can explain WHY
  // api_key_configured is false without operator shell access.
  // "ok" — key present and non-empty.
  // "file_not_found" — the MCP credential projection is missing (volume issue).
  // "invalid_key" — projection unreadable or ambiguous (…-04c0u.8).
  // "field_empty" — projection present but empty (no key generated / revoked).
  // "invalid_json" / "field_missing" — emitted only by pre-…-04c0u.8 sidecars
  //   that still parsed settings.json; kept so a mixed-version deployment
  //   still type-checks against the older image's payload.
  api_key_status?: 'ok' | 'file_not_found' | 'invalid_key' | 'field_empty' | 'invalid_json' | 'field_missing';
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
  /** Sort column. Passing either this or `unusedOnly` (or a non-empty
   * `search`) routes the request through the backend's full-dataset
   * aggregate path (bead enhancedchannelmanager-09x38.13) — see
   * backend/routers/channels.py get_logos() for why Dispatcharr itself
   * cannot sort or honor `search`. */
  sortBy?: 'name' | 'channel_count';
  sortOrder?: 'asc' | 'desc';
  /** Only return logos with channel_count === 0. */
  unusedOnly?: boolean;
}): Promise<PaginatedResponse<Logo>> {
  const query = buildQuery({
    page: params?.page,
    page_size: params?.pageSize,
    search: params?.search,
    sort_by: params?.sortBy,
    sort_order: params?.sortOrder,
    unused_only: params?.unusedOnly ? true : undefined,
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

export type GuideMigrationStatus =
  | 'ready'
  | 'already_target'
  | 'unassigned'
  | 'missing_lcn'
  | 'missing_target'
  | 'ambiguous_target'
  | 'unsupported_origin';

export interface GuideMigrationRow {
  channel_id: number;
  channel_name: string;
  current_epg_data_id: number | null;
  current_source_id: number | null;
  current_source_name: string | null;
  lcn: string | null;
  target_epg_data_id: number | null;
  target_name: string | null;
  current_tvg_id: string | null;
  target_tvg_id: string | null;
  status: GuideMigrationStatus;
}

export interface GuideMigrationPreview {
  target_source_id: number;
  target_source_name: string;
  rows: GuideMigrationRow[];
  counts: Record<GuideMigrationStatus, number>;
  preview_token: string;
}

export type GuideMigrationApplyStatus =
  | 'updated'
  | 'updated_audit_failed'
  | 'ambiguous_target'
  | 'unsupported_origin'
  | 'semantic_drift'
  | 'changed_since_preview'
  | 'failed';

export interface GuideMigrationApplyResult {
  mutated: number;
  updated: number;
  audit_failed: number;
  skipped: number;
  failed: number;
  results: Array<{ channel_id: number; status: GuideMigrationApplyStatus }>;
  batch_id: string;
}

export interface GuideMigrationJobStatus {
  batch_id: string;
  status: 'running' | 'completed' | 'failed';
  processed: number;
  total: number;
  result: GuideMigrationApplyResult;
  error?: string;
}

export async function previewGuideMigration(
  targetEpgSourceId: number
): Promise<GuideMigrationPreview> {
  return fetchJson(`${API_BASE}/epg/migration/preview`, {
    method: 'POST',
    body: JSON.stringify({ target_epg_source_id: targetEpgSourceId }),
  });
}

const GUIDE_MIGRATION_POLL_INTERVAL_MS = 750;

function abortableDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Guide migration polling aborted', 'AbortError'));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('Guide migration polling aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export class GuideMigrationPollingError extends Error {
  constructor(
    message: string,
    public readonly batchId: string
  ) {
    super(message);
    this.name = 'GuideMigrationPollingError';
  }
}

export async function applyGuideMigration(
  preview: GuideMigrationPreview,
  onProgress?: (status: GuideMigrationJobStatus) => void,
  signal?: AbortSignal
): Promise<GuideMigrationApplyResult> {
  const items = preview.rows
    .filter((row) => row.status === 'ready')
    .map((row) => ({
      channel_id: row.channel_id,
      current_epg_data_id: row.current_epg_data_id,
      current_source_id: row.current_source_id,
      current_tvg_id: row.current_tvg_id,
      lcn: row.lcn,
      target_epg_data_id: row.target_epg_data_id,
      target_tvg_id: row.target_tvg_id,
    }));
  const accepted = await fetchJson<{
    batch_id: string;
    status: 'running';
  }>(`${API_BASE}/epg/migration/apply`, {
    method: 'POST',
    signal,
    body: JSON.stringify({
      target_epg_source_id: preview.target_source_id,
      preview_token: preview.preview_token,
      items,
    }),
  });
  const emptyResult: GuideMigrationApplyResult = {
    mutated: 0,
    updated: 0,
    audit_failed: 0,
    skipped: 0,
    failed: 0,
    results: [],
    batch_id: accepted.batch_id,
  };
  onProgress?.({
    batch_id: accepted.batch_id,
    status: 'running',
    processed: 0,
    total: items.length,
    result: emptyResult,
  });
  while (!signal?.aborted) {
    try {
      const status = await fetchJson<GuideMigrationJobStatus>(
        `${API_BASE}/epg/migration/apply/${encodeURIComponent(accepted.batch_id)}`,
        { signal }
      );
      onProgress?.(status);
      if (status.status === 'completed') return status.result;
      if (status.status === 'failed') {
        throw new GuideMigrationPollingError(
          `Guide migration ${accepted.batch_id} failed. Build a fresh preview and verify affected channels in Dispatcharr before retrying.`,
          accepted.batch_id
        );
      }
    } catch (error) {
      if (signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        throw error;
      }
      if (error instanceof GuideMigrationPollingError) throw error;
      if (error instanceof HttpError && error.status === 404) {
        throw new GuideMigrationPollingError(
          `Guide migration ${accepted.batch_id} is no longer available, possibly because ECM restarted. Preserve this batch ID, build a fresh preview, and verify affected channels in Dispatcharr before retrying.`,
          accepted.batch_id
        );
      }
      if (error instanceof HttpError && error.status < 500) {
        throw new GuideMigrationPollingError(
          `Guide migration ${accepted.batch_id} polling stopped: ${error.message}. Preserve this batch ID and verify affected channels in Dispatcharr before retrying.`,
          accepted.batch_id
        );
      }
      // Network and transient server errors do not discard a known accepted
      // batch or its last partial result. Keep polling until terminal/unmount.
    }
    await abortableDelay(GUIDE_MIGRATION_POLL_INTERVAL_MS, signal);
  }
  throw new DOMException('Guide migration polling aborted', 'AbortError');
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
// Provider Stream Usage (GH-482, bd-n5cwp)
// =============================================================================
//
// NOT admin-gated (Dispatcharr-derived catalog/assignment data — see
// ProviderStreamUsageResponse doc comment in types/index.ts for the
// assigned_streams vs total_assignments distinction).

export async function getProviderStreamUsage(): Promise<import('../types').ProviderStreamUsageResponse> {
  return fetchJson(`${API_BASE}/stats/providers/stream-usage`);
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

// Provider-stale stream ids (bead enhancedchannelmanager-po78p / GH #696).
//
// Distinct from getStaleStreams above: this is the raw Dispatcharr `is_stale`
// set (cached, cheap paged scan), used as the single source of truth for
// stale-stream decoration in the Channels/Streams panes — not the richer
// probe-staleness report that endpoint returns.
export async function getStaleStreamIds(bypassCache = false): Promise<StaleStreamIdsResponse> {
  return fetchJson(`${API_BASE}/streams/stale-ids${bypassCache ? '?bypass_cache=true' : ''}`);
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
  required?: boolean;
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
  /**
   * True firing gate (vkktd.3/vkktd.4): parent `enabled` AND >=1 enabled child
   * schedule. A task can read `enabled: true` yet never fire when all its
   * child schedules are disabled — bind UI state to this, never bare `enabled`.
   * Optional because older backends (< build 0091) don't send it.
   */
  effective_enabled?: boolean;
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
  /**
   * How the run ended. `completed_with_warnings` (bead fexq1) is the run that
   * reached the end and left real, kept state without being clean — some items
   * failed, or the task declared itself degraded. It is NOT a failure and must
   * never be rendered as one: a DBAS restore that applied everything and left a
   * channel with no playable stream lands here.
   *
   * Rows written before that build carry `completed` or `failed` only, so any
   * consumer that switches on this must keep whatever fallback it had.
   */
  status: 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
  /** True when the run produced what it was asked for — warnings included. */
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

/**
 * Every function below that can carry a `dbas_backup` schedule reports what it
 * saw to `voidBackupBannerSnoozeIfScheduled`. That helper owns the invariant
 * "an observed enabled backup schedule voids any stored banner snooze"; these
 * call sites only owe it the observation. See `utils/backupBannerSnooze.ts` —
 * the point of doing it here rather than in the banner component is that the
 * fact has to take effect when the banner is not mounted, which is exactly the
 * lifecycle that was broken (bead enhancedchannelmanager-pui76, round 2).
 */
export async function getTasks(): Promise<{ tasks: TaskStatus[] }> {
  const result = await fetchJson<{ tasks: TaskStatus[] }>(`${API_BASE}/tasks`, {
    method: 'GET',
  });
  for (const task of result?.tasks ?? []) {
    voidBackupBannerSnoozeIfScheduled(task.task_id, task.schedules);
  }
  return result;
}

export async function getTask(taskId: string): Promise<TaskStatus> {
  const task = await fetchJson<TaskStatus>(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, {
    method: 'GET',
  });
  voidBackupBannerSnoozeIfScheduled(taskId, task?.schedules);
  return task;
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
  const result = await fetchJson<{ schedules: TaskSchedule[] }>(
    `${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules`,
    {
      method: 'GET',
    }
  );
  voidBackupBannerSnoozeIfScheduled(taskId, result?.schedules);
  return result;
}

export async function createTaskSchedule(taskId: string, data: TaskScheduleCreate): Promise<TaskSchedule> {
  const schedule = await fetchJson<TaskSchedule>(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  voidBackupBannerSnoozeIfScheduled(taskId, schedule ? [schedule] : []);
  return schedule;
}

export async function updateTaskSchedule(
  taskId: string,
  scheduleId: number,
  data: TaskScheduleUpdate
): Promise<TaskSchedule> {
  const schedule = await fetchJson<TaskSchedule>(
    `${API_BASE}/tasks/${encodeURIComponent(taskId)}/schedules/${scheduleId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }
  );
  voidBackupBannerSnoozeIfScheduled(taskId, schedule ? [schedule] : []);
  return schedule;
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
  /**
   * What the artifact could NOT carry, read off the live post-restore instance
   * (bead enhancedchannelmanager-gi4zn). A standard (non-encrypted) backup
   * carries no ECM account credentials, so restoring one onto an instance with
   * no accounts leaves first-run setup required. `restored_files` reports what
   * landed and structurally cannot report that. Optional: a backend predating
   * the field omits it, and every caller must tolerate that.
   */
  notices?: string[];
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
  // The Dispatcharr ServerGroup an M3U account's `server_group` FK points at
  // (bead tyrg1). Its category is ordered before `m3u_account` in the restore
  // and sync registries, so it appears in every sync report.
  | 'server_group'
  | 'dvr_rule'
  // The DVR recording INSTANCES that had not started yet when the backup was
  // taken (bead ciabe). Distinct from `dvr_rule`, which is the recurring RULE
  // that generates them. Completed recordings are never in a backup at all —
  // they reference a media file on the source instance's disk — so there is no
  // second entity type for them.
  | 'upcoming_recording'
  // Report-only category for core_settings + comskip apply results (updated/
  // skipped, never created) — mirrors backend EntityType.SETTINGS (bead lc6zu).
  | 'settings'
  // ECM's OWN settings.json apply results (bead dfkbn) — a DIFFERENT namespace
  // from `settings` above, which is Dispatcharr's core-settings.
  | 'ecm_settings'
  | 'user'
  | 'logo';

/** Why an entity was (or would be) skipped. */
export type RestoreSkipReason =
  | 'already_exists_identical'
  /**
   * A destination row was ADOPTED because its NAME matched, with nothing beyond
   * the name compared (bead 3t74w). Distinct from `already_exists_identical`,
   * which asserts the destination row matches the archive's. Channel groups
   * report this: a group's contents live on the CHANNELS, restored after it, so
   * at match time there is nothing to compare and "identical" was never earned.
   */
  | 'already_exists_name_match'
  | 'excluded_by_operator'
  | 'current_admin_preserved'
  | 'unsupported_in_this_version'
  /**
   * A dependency the run WAS asked to deliver is not on the destination, so the
   * entity was never created. A LOSS: its aggregate,
   * `entities_blocked_by_dependency`, downgrades the outcome (bead 4mkoe).
   */
  | 'dependency_unresolved'
  /**
   * The same missing dependency, when the operator EXCLUDED the category it
   * lives in and this row is a LINK INTO that category (bead 4mkoe) — an
   * archived channel's membership of a channel profile the restore was told to
   * leave out. Never a loss: the absence is what the operator asked for, and
   * naming it would fire on every unattended cycle forever. Render it as an
   * ordinary no-op beside `excluded_by_operator`, never as a problem.
   */
  | 'dependency_deselected'
  /**
   * The archived entity is pinned to an absolute moment that has since passed
   * (bead ciabe) — an upcoming recording in a backup older than the recording
   * itself. NEVER a loss: the destination cannot hold it either (Dispatcharr
   * refuses a recording scheduled in the past), so a replica missing a
   * programme that already aired has lost nothing. Render it as an ordinary
   * no-op, never as a problem.
   */
  | 'schedule_already_past';

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
 * Overall result of a realized restore. `null` on a dry-run (a plan has no
 * realized outcome). NEVER `success` on mixed state — the two rolled-back states
 * are explicit failures, and `completed_with_failures` is a restore that ran to
 * completion with some non-fatal rows failing and NOTHING rolled back. The
 * summary UX surfaces each of them as such.
 */
export type RestoreOutcome =
  | 'success'
  | 'completed_with_failures'
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
  /**
   * `false` when a DRY RUN could not predict this category at all (bead tddmw).
   * Render the counts as "not predicted" rather than as a confident zero — the
   * per-category form of the `null` the stream-health counters already use.
   * Absent on reports produced before this field existed; treat as `true`.
   */
  predicted?: boolean;
  /**
   * Operator-facing note qualifying how far this category's counts can be
   * trusted (bead tddmw). Only a preview carries one; an apply reports facts.
   */
  caveat?: string | null;
}

/**
 * One channel affected by a logo miss (bead cm9bi). `channel_id` is the
 * DESTINATION Dispatcharr channel id when known — null when the channel could
 * not be resolved (its create failed) or on dry-run (whose remap holds
 * provisional ids that must never render as real Dispatcharr links). `name` is
 * the operator-facing channel name; never a secret.
 */
export interface LogoMissChannel {
  channel_id?: number | null;
  name: string;
}

/**
 * One logo that could not be matched/applied on restore (bead qhui4) — the
 * per-logo drill-down behind the aggregate `logo_misses` count. `label` is the
 * operator-facing logo name; never a path or secret. `channels` (bead cm9bi)
 * lists the AFFECTED CHANNELS — one miss stays one detail row (the aggregate
 * counts logos, not channels); a logo shared by several channels lists them
 * all. May be absent on reports produced before the field existed.
 */
export interface LogoMissDetail {
  source_export_id?: number | null;
  label: string;
  channels?: LogoMissChannel[];
}

/**
 * One restored entity whose credential the archive could not carry (bead 6pilh).
 *
 * A STANDARD (redact-by-default) artifact replaces every credential with the
 * `***REDACTED***` placeholder. The restore importers refuse to write that
 * placeholder into the destination, so the field is left UNSET — the entity
 * exists but will not authenticate until the operator re-enters it. `fields`
 * carries credential field NAMES only (`["password"]`), never values.
 * `destination_id` is null on a dry-run (nothing was created).
 */
export interface CredentialReentryDetail {
  entity_type: RestoreEntityType;
  label: string;
  fields: string[];
  source_export_id?: number | null;
  destination_id?: number | null;
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
  /**
   * Count of entities restored WITHOUT their credential because the artifact was
   * redacted (bead 6pilh). A post-restore action item, not a failure — the
   * outcome can still be `success` while these accounts authenticate nowhere.
   * May be absent on reports produced before this field existed.
   */
  credentials_needing_reentry?: number;
  /** Which entities need which credential fields re-entered (bead 6pilh). */
  credential_reentry_details?: CredentialReentryDetail[];
  /**
   * How many provider records this cycle carried a credential onto (PO ruling
   * 2026-08-22, ADR-013 amendment (b) S13').
   *
   * COUNT ONLY — never a value. Under per-cycle transmission the audit trail is
   * the only record of how often a secret moved, so every run states it,
   * including the runs that carried nothing. Replaced
   * `destination_credentials_observed` / `destination_credentials_checked`,
   * which fed the `insecure` refusal that this ruling removed.
   * May be absent on reports produced before this field existed.
   */
  provider_credentials_transmitted?: number;
  /**
   * One line per provider record carrying a credential: operator-facing label
   * plus the FIELD NAMES carried, never a value or a fragment of one.
   */
  provider_credential_transmission_details?: string[];
  /**
   * Replicated provider accounts whose own status and stream count indicate
   * their provisioned credential has stopped working (bead wd20y, ADR-013
   * INV-8, retained as information after amendment (b) voided the invariant's
   * wording). An ACTION ITEM, not a failure and not a delivery shortfall: it
   * does not move the run's outcome. There is no provisioning action to re-run
   * any more — the cycle re-sends the current credential every time, so a
   * persistently stale account is a problem to fix on the SOURCE.
   * May be absent on older reports.
   */
  provisioned_credentials_stale?: number;
  /**
   * One sanitized operator-facing line per stale replicated provider account.
   * Names and counts only — never an upstream error body, which can quote a
   * request URL. May be absent on older reports.
   */
  provisioned_credential_stale_details?: string[];
  /**
   * Channels still holding at least one URL-less PLACEHOLDER stream slot after
   * the restore's post-refresh rebind pass (bead 2o0cz). NOT an unplayability
   * signal: a channel that kept its real streams and holds one leftover
   * placeholder is counted here and plays fine (bead daziw). May be absent on
   * reports produced before this field existed.
   *
   * `null` means NOT PREDICTED and only ever appears on a DRY RUN (bead dgnms):
   * the rebind pass that answers this re-matches against provider streams the
   * deferred M3U refresh materializes, and a preview refreshes nothing. Render
   * it as "not predicted" or omit the row — never as `0`, which is the confident
   * claim this null exists to stop making.
   */
  channels_needing_stream_reattach?: number | null;
  /**
   * The SUBSET of the above left with NO URL-bearing stream at all (bead daziw)
   * — those channels CANNOT PLAY, and an apply that reports any of them
   * resolves to `completed_with_failures`, never `success`.
   *
   * One of FIVE counters that carry that weight (beads posm1 / 4mkoe): an apply
   * is also `completed_with_failures` when `stream_urls_redacted`,
   * `epg_links_unrestored`, `logo_misses` or `entities_blocked_by_dependency`
   * is above zero — each of them means the replica is missing something the
   * source had. Do not render any one of them as the reason for the outcome;
   * read `outcome` itself.
   *
   * `null` on a dry run, for the same reason as the field above (bead dgnms).
   */
  channels_with_no_playable_stream?: number | null;
  /** Which channels still need a playable stream attached (bead 2o0cz). */
  stream_reattach_details?: StreamReattachDetail[];
  /** Placeholder bindings the rebind pass swapped for a real provider stream. */
  streams_rebound?: number;
  /**
   * Destination streams holding a URL the provider credentials were cut out of
   * (bead msqf7) — a real Xtream Codes address embeds the credential in its
   * path, so it cannot be carried without it. Those streams name where they
   * pointed and cannot play until the destination's own provider account
   * supplies one. One of the five counters that make an apply
   * `completed_with_failures`; read `outcome`, not this.
   *
   * A FACT ABOUT THE DESTINATION, NOT ABOUT THIS RUN (bead ukjx5). Recomputed
   * from the destination's own stream rows on every cycle, so a shortfall the
   * replica still exhibits is reported every time rather than only on the cycle
   * that wrote it — the earlier count was cycle-scoped and read 53, then 0,
   * while 53 of B's streams still could not play.
   *
   * `null` means NOT PREDICTED and only ever appears on a DRY RUN: the pass that
   * reads the destination's streams cannot run before the apply, so a preview
   * `0` would be a claim derived from having looked at nothing. Coerce with
   * `?? 0` only where a number is unavoidable — never render the null as `0`.
   */
  stream_urls_redacted?: number | null;
  /** Which streams lost the credential half of their URL (bead msqf7). */
  stream_url_redaction_details?: StreamUrlRedactionDetail[];
  /** Channels whose archived EPG link could not be reattached (bead dfkbn). */
  epg_links_unrestored?: number;
  /**
   * Archived entities never created because something they reference is not on
   * the destination (bead 4mkoe) — a degraded backup that lost the dependency's
   * whole category, a collision that stopped it being created, or a selection
   * that kept the dependants and dropped the dependency.
   *
   * Counts only the GENUINE half. A skip whose missing dependency lives in a
   * category the operator DESELECTED, and which is itself a link into that
   * category, carries `SkipReason.DEPENDENCY_DESELECTED` and is never counted
   * here — that absence is exactly what the operator asked for. The named
   * drill-down is each category's `skip_details`.
   */
  entities_blocked_by_dependency?: number;
  /** Which channels restored with no EPG link, and the tvg_id that missed. */
  epg_link_miss_details?: EpgLinkMissDetail[];
  /**
   * Channel/profile memberships corrected back to the ARCHIVED selection (bead
   * dfkbn). Dispatcharr enables every new channel in every profile, so a profile
   * built to EXCLUDE channels silently widens to all of them; this counts the
   * channels the restore had to flip back.
   */
  profile_membership_drift?: number;
  /** Which profiles drifted, and which channels were flipped back (bead dfkbn). */
  profile_membership_drift_details?: ProfileMembershipDriftDetail[];
  /**
   * Channels sitting in a channel GROUP the archive does not assign them (bead
   * r1ei7). The channel-group sibling of `profile_membership_drift`, and the
   * counter a populated-target restore had no equivalent of: it kept the
   * destination's whole grouping in BOTH relink modes while reporting success.
   * Reported under `preserve` (which changes nothing) as well as `overwrite`.
   */
  channel_group_drift?: number;
  /** Which channels drifted, the group they are in, and the archive's (r1ei7). */
  channel_group_drift_details?: ChannelGroupDriftDetail[];
  /**
   * Per-account provider GROUP-ENABLE selections the destination's M3U account
   * did not receive (bead avrix). Distinct from `channel_group_drift`, which is
   * about which group a CHANNEL sits in: this is about which of a provider
   * ACCOUNT's groups are switched on, i.e. what that account ingests on its next
   * refresh. `channel_group_drift` read `0` throughout the run that found this,
   * while the replica held zero selections — not wrong, measuring another thing.
   *
   * Deliberately NOT one of the five counters that move the outcome: an operator
   * who deselects the channel-groups category leaves the destination without the
   * groups these selections point at, so the count would stay non-zero forever
   * with no action that clears it. It renders as a named action item instead —
   * the `profile_membership_drift` precedent — visible without moving `outcome`.
   */
  provider_group_selection_unapplied?: number;
  /**
   * Which replicated M3U accounts did not receive their group selection, how
   * many, and why (bead avrix).
   */
  provider_group_selection_details?: ProviderGroupSelectionDetail[];
  /**
   * Fields on an ALREADY-EXISTING replica M3U account that differ from the
   * source's (bead zszjd). Counts FIELDS, not accounts — "3 settings on this
   * account are not what you set on the primary".
   *
   * Deliberately NOT one of the counters that move the outcome, for the same
   * reason `profile_membership_drift` is not: it counts every difference the
   * cycle FOUND, including the ones it then converged, so downgrading on it
   * would put a red mark on exactly the cycle that fixed the problem. The half
   * that does move the outcome is `account_convergence_unapplied`.
   */
  account_field_drift?: number;
  /**
   * Which accounts drifted, WHICH FIELD NAMES (never values — a converging
   * account carries `username`, `password` and a credential-bearing
   * `server_url`), and whether this cycle wrote them (bead zszjd).
   */
  account_field_drift_details?: AccountFieldDriftDetail[];
  /**
   * Drifted fields an APPLY tried to write onto the replica and could not
   * (bead zszjd). One of the counters that forbid a SUCCESS outcome: the
   * replica is missing something the source has and the run tried to deliver
   * it. Clears by itself once a later cycle's write lands.
   */
  account_convergence_unapplied?: number;
  /**
   * Set when the channel-group check did NOT run, because the operator
   * deselected the channel groups category (bead r1ei7). Its absence is what
   * makes `channel_group_drift: 0` trustworthy; when it is present that zero
   * means "not examined", not "no drift" — the same distinction the per-category
   * `predicted` flag draws (bead tddmw). Render it wherever the count renders.
   */
  channel_group_drift_note?: string | null;
  /**
   * How the EPG-link reattach split across channels this restore created and
   * channels that already existed (bead dfkbn). Reported on the dry run AND the
   * apply, so the preview predicts the split rather than describing it after.
   */
  epg_link_reattach?: ReattachPopulation;
  /** The same split for the channel-logo reattach (bead dfkbn). */
  logo_reattach?: ReattachPopulation;
  /**
   * The "I never read the destination" marker (bead jqfxm). Every count in this
   * report is a claim ABOUT the destination — "would create 24" means "B does
   * not have these 24" — but each importer degrades a failed destination read to
   * "B is empty", so a run that could not authenticate produced the same shape
   * as a run against an empty B: a full would-create plan, zero failures. A live
   * cross-instance sync measured that against a WRONG PASSWORD and offered
   * Apply, with seven 401/429s in B's log that no surface mentioned.
   *
   * Set whenever the run could not read the destination it claims to describe —
   * credentials rejected, rate-limited, unreachable, TLS/DNS/SSRF refused, a
   * 5xx, or aborted before any read. A report carrying this is NEVER a success
   * and its counts describe the source, not the destination. Sanitized prose
   * (status code + error class); never a URL, body or credential.
   */
  destination_unreadable?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  notes: string[];
}

/**
 * What a restore does to channels it did NOT create (bead dfkbn).
 *
 * `preserve` leaves a channel that already existed on the destination with the
 * EPG link, logo and channel group it already has; `overwrite` replaces all
 * three with the archive's. On an empty destination every channel is created, so
 * the two are indistinguishable: the choice only matters when merging into a
 * live install.
 *
 * Grouping joined the other two in bead r1ei7. It differs in one way: `preserve`
 * still REPORTS the divergence (`channel_group_drift`). Leaving state alone is a
 * choice; leaving it unmentioned was the defect.
 */
export type ChannelReattachMode = 'preserve' | 'overwrite';

/**
 * How one post-create reattach pass split across the two channel populations
 * (bead dfkbn). `existing_channels` is the destructive half: those are channels
 * the operator already had, whose live reference was replaced by the archive's.
 */
export interface ReattachPopulation {
  mode: ChannelReattachMode;
  created_channels: number;
  existing_channels: number;
  preserved_channels: number;
  existing_channels_named: string[];
  preserved_channels_named: string[];
}

/**
 * One restored channel that still cannot play (bead 2o0cz). `placeholder_streams`
 * carries the names of the URL-less placeholder streams still attached — names
 * only, never a stream URL (which can embed a provider token).
 */
export interface StreamReattachDetail {
  channel_id?: number | null;
  name: string;
  placeholder_streams?: string[];
  /**
   * True (the default when absent) when the channel still holds a real,
   * URL-bearing stream and PLAYS despite the leftover placeholder; false when
   * every slot is a placeholder and the channel is dead (bead daziw).
   */
  has_playable_stream?: boolean;
}

/**
 * One destination stream whose URL had the provider credentials cut out of it
 * (bead msqf7) — the per-stream drill-down behind `stream_urls_redacted`.
 * `label` is the operator-facing stream name; never the URL, which can embed a
 * provider token. `stream_id` is the destination stream id when upstream
 * returned one.
 */
export interface StreamUrlRedactionDetail {
  stream_id?: number | null;
  label: string;
}

/**
 * One restored channel whose EPG link could not be reattached (bead dfkbn).
 * The link is re-derived from the archived `tvg_id` — an EPG row's numeric id is
 * instance-local and cannot round-trip.
 */
export interface EpgLinkMissDetail {
  channel_id?: number | null;
  name: string;
  tvg_id?: string;
}

/**
 * One channel profile whose membership the restore had to correct (bead dfkbn).
 * `channels_disabled` are the channels the archive EXCLUDED that the destination
 * had enabled by default — the silent widening this counter exists to surface.
 */
export interface ProfileMembershipDriftDetail {
  profile_id?: number | null;
  name: string;
  channels_disabled?: string[];
  channels_enabled?: string[];
}

/**
 * One restored channel sitting in a group the archive does not put it in
 * (bead r1ei7). A channel that already exists on the destination is never
 * overwritten, so its group is never written: on a populated target the lineup
 * silently keeps whatever grouping the destination had while the counts
 * reconcile exactly.
 *
 * Both group fields are NAMES — the ids are instance-local and mean nothing to
 * the reader. `moved` is true only under the `overwrite` relink mode, where the
 * restore also puts the channel back in the archive's group (on a preview, the
 * would-be move).
 */
export interface ChannelGroupDriftDetail {
  channel_id?: number | null;
  name: string;
  current_group: string;
  archive_group: string;
  moved?: boolean;
}

/**
 * One replicated M3U account whose per-group ENABLE selection did not fully land
 * (bead avrix) — the drill-down behind `provider_group_selection_unapplied`.
 *
 * That selection is what decides how much of a provider's catalogue the account
 * ingests, so losing it sends the replica one of two ways depending on the
 * source account's auto-enable flag: a refresh that filters zero streams from
 * zero enabled categories, or one that enables every category the provider has.
 *
 * IDS, NOT NAMES: the selections are keyed by destination account id and source
 * channel-group pk, and neither name is in scope where they are recorded.
 * `reason` is a sanitized phrase — never a secret.
 */
export interface ProviderGroupSelectionDetail {
  destination_account_id: number;
  selections_total: number;
  selections_applied?: number;
  selections_unapplied?: number;
  enabled_applied?: number;
  reason: string;
}

/**
 * One replicated M3U account whose fields differ from the source's (bead zszjd).
 *
 * An account that already exists on the replica used to be matched
 * ALREADY_EXISTS_IDENTICAL and never written to again — for every field, not
 * one — so a setting changed on the primary afterwards diverged silently and
 * permanently. The cycle now converges the account and reports what it moved.
 *
 * FIELD NAMES ONLY, NEVER VALUES: a converging account carries `username`,
 * `password` and a credential-bearing `server_url`. `reason` is a sanitized
 * phrase saying why a write did not happen, and is null when it did.
 */
export interface AccountFieldDriftDetail {
  destination_account_id?: number | null;
  name: string;
  fields?: string[];
  applied?: boolean;
  reason?: string | null;
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
  passphrase?: string,
  channelReattachMode: ChannelReattachMode = 'preserve'
): Promise<DbasRestoreStartResult> {
  const formData = new FormData();
  formData.append('file', file);
  if (passphrase) formData.append('passphrase', passphrase);

  const query = buildQuery({
    confirm_apply: confirmApply,
    channel_reattach_mode: channelReattachMode,
  });
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
  channelReattachMode: ChannelReattachMode = 'preserve',
): Promise<DbasRestoreStartResult> {
  return fetchJson(`${API_BASE}/backup/restore-dbas-saved`, {
    method: 'POST',
    body: JSON.stringify({
      filename,
      confirm_apply: confirmApply,
      channel_reattach_mode: channelReattachMode,
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
 *
 * `candidate_channel_name` / `candidate_channel_number` /
 * `candidate_channel_group_name` are additive fields (bead
 * enhancedchannelmanager-09x38.14) resolved server-side from Dispatcharr
 * at list time so the operator can see what they'd be merging into
 * without leaving the page. All three are `null` when the candidate
 * channel could not be resolved — most commonly because it was deleted
 * in Dispatcharr since the row was queued — in which case the UI should
 * render an explicit "channel no longer exists" fallback using
 * `candidate_channel_id`.
 */
export interface PendingMergeRecord {
  id: number;
  stream_name: string;
  group_id: number | null;
  candidate_channel_id: string;
  candidate_channel_name: string | null;
  candidate_channel_number: number | null;
  candidate_channel_group_name: string | null;
  confidence: number;
  status: 'pending' | 'merged' | 'dismissed';
  created_at: number;
  resolved_at: number | null;
  resolution_source: string | null;
  trigger_context: string;
  /**
   * Why the LAST accept on this row could not be applied to Dispatcharr, in
   * operator-actionable prose; `null` when no accept has failed to apply.
   *
   * A `'pending'` row with this set is a merge the operator ALREADY accepted
   * that ECM could not carry out. It deliberately stays in the queue, flagged,
   * so the reason is on the row rather than only in the Journal, and accepting
   * it again is an ordinary retry that clears the flag when it lands (bead
   * enhancedchannelmanager-i5ic0, PO decision 2026-08-16).
   */
  unapplied_reason: string | null;
}

/** Paginated envelope for `GET /api/channel-merges`. */
export interface PendingMergesListResponse {
  merges: PendingMergeRecord[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PendingMergesSnapshotResponse {
  merges: PendingMergeRecord[];
  total: number;
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
  /**
   * The QUEUE ROW's state, which is no longer always terminal. `'merged'` when
   * the merge applied and the row left the queue; `'pending'` when ECM could
   * not apply it, in which case the row STAYS queued carrying its
   * `unapplied_reason` and remains retryable (PO decision 2026-08-16). Never a
   * claim about Dispatcharr on its own — see `dispatcharr_updated`.
   */
  status: 'merged' | 'pending';
  /**
   * Whether the candidate channel ends the request holding the stream.
   *
   * THREE values, and each needs its own handling — `!== false` is not a test
   * for success (bead enhancedchannelmanager-i5ic0):
   *
   * - `true`  — the channel holds the stream, whether this call PATCHed it or
   *   it was already there.
   * - `false` — no upstream write happened. The stream-name lookup matched
   *   zero or several streams, matched one with no usable id, failed, or was
   *   truncated at its page ceiling — a truncated search cannot establish
   *   uniqueness even when exactly one match is visible on the page it saw.
   * - `null`  — an idempotent replay. It made no Dispatcharr call and has no
   *   evidence either way; treating it as success reports a certainty nothing
   *   established.
   */
  dispatcharr_updated: boolean | null;
  /** Operator-actionable prose whenever `dispatcharr_updated` is not `true`. */
  unapplied_reason: string | null;
  /** Journal rows this request could not write. Always present. */
  journal_rows_unwritten: number;
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

/** Admin-gated coherent snapshot used before queue-wide bulk operations. */
export async function getPendingMergesSnapshot(params?: {
  groupId?: number;
}): Promise<PendingMergesSnapshotResponse> {
  const query = buildQuery({ group_id: params?.groupId });
  return fetchJson(`${API_BASE}/channel-merges/snapshot${query}`);
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
// Event Sync review queue (bead ti939.3.2) — /api/event-sync-reviews.
//
// Ambiguous-band event_sync matches enqueue here instead of being silently
// skipped. Rows key on content fingerprints (rule, provider, normalized
// stream name hash, normalized event identity) — never channel/stream IDs —
// so operator decisions survive Dispatcharr refreshes and re-apply on every
// future run. List is RequireAuthIfEnabled; accept/reject are admin-gated.
// -------------------------------------------------------------------------

/**
 * List event sync review rows. Defaults to the open queue
 * (status='pending', page=1, page_size=50). Pass `ruleId` to scope to one
 * event_sync rule.
 */
export async function getEventSyncReviews(params?: {
  status?: 'pending' | 'accepted' | 'rejected' | 'superseded';
  ruleId?: number;
  page?: number;
  pageSize?: number;
}): Promise<EventSyncReviewsListResponse> {
  const query = buildQuery({
    status: params?.status ?? 'pending',
    rule_id: params?.ruleId,
    page: params?.page ?? 1,
    page_size: params?.pageSize ?? 50,
  });
  return fetchJson(`${API_BASE}/event-sync-reviews${query}`);
}

/**
 * Accept a review pairing: the backend records the fingerprint-keyed
 * decision (durable — future runs auto-attach it) and best-effort attaches
 * the stream immediately after re-verifying the snapshot ids against live
 * Dispatcharr. `attach_deferred_reason` explains a deferred attach; the
 * next pipeline run applies it. Idempotent on `accepted`; 409 on
 * `rejected`/`superseded`.
 */
export async function acceptEventSyncReview(
  reviewId: number,
): Promise<AcceptEventSyncReviewOutcome> {
  return fetchJson(`${API_BASE}/event-sync-reviews/${reviewId}/accept`, {
    method: 'POST',
  });
}

/**
 * Reject a review pairing: durable fingerprint-keyed suppression — future
 * runs skip the pairing without re-asking. Idempotent on `rejected`; 409
 * on `accepted`/`superseded`.
 */
export async function rejectEventSyncReview(
  reviewId: number,
): Promise<RejectEventSyncReviewOutcome> {
  return fetchJson(`${API_BASE}/event-sync-reviews/${reviewId}/reject`, {
    method: 'POST',
  });
}

// -------------------------------------------------------------------------
// Event Sync operator exclusions (bead ti939.3.5) —
// /api/event-sync-exclusions.
//
// A durable "never attach this provider stream to that event" standing
// order. Fingerprint-keyed like review decisions (never channel/stream
// IDs), consulted by the shared resolver BEFORE the attach band — an
// exclusion outranks a prior accept. List is RequireAuthIfEnabled;
// create/delete are admin-gated.
// -------------------------------------------------------------------------

/** List never-attach exclusions, newest first (optionally one rule's). */
export async function getEventSyncExclusions(params?: {
  ruleId?: number;
  page?: number;
  pageSize?: number;
}): Promise<EventSyncExclusionsListResponse> {
  const query = buildQuery({
    rule_id: params?.ruleId,
    page: params?.page ?? 1,
    page_size: params?.pageSize ?? 50,
  });
  return fetchJson(`${API_BASE}/event-sync-exclusions${query}`);
}

/**
 * Create a never-attach exclusion. Idempotent on the fingerprint: an
 * already-excluded pairing returns the existing row
 * (`already_existed: true`), never a duplicate.
 */
export async function createEventSyncExclusion(
  body: EventSyncExclusionCreateRequest,
): Promise<EventSyncExclusionRecord> {
  return fetchJson(`${API_BASE}/event-sync-exclusions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Remove a never-attach exclusion — the pairing becomes matchable again on
 * the next run/preview (nothing is re-attached immediately; the idempotent
 * run is the applier).
 */
export async function deleteEventSyncExclusion(
  exclusionId: number,
): Promise<void> {
  await fetchJson(`${API_BASE}/event-sync-exclusions/${exclusionId}`, {
    method: 'DELETE',
  });
}

// -------------------------------------------------------------------------
// Sync Targets — cross-instance live-sync destinations (epic i39wu).
//
// A SyncTarget is a remote Dispatcharr-B instance ECM pushes config to via the
// one-way sync engine. The CRUD mirrors backend/routers/sync_targets.py:
// credentials are WRITE-ONLY (Fernet-encrypted at rest, never echoed back
// decrypted — responses mask them to last-4). The actual sync is driven by
// `runTask(`dbas_sync_${id}`, undefined, { sync_target_id, confirm_apply })` —
// one registered task per target (7ipq2.3 / ADR-013 S6), so distinct targets
// sync concurrently and a second run against the SAME target is refused.
//
// PROVIDER CREDENTIALS CROSS ON EVERY CYCLE (PO ruling 2026-08-22). The
// replica's M3U accounts and EPG sources receive this instance's real provider
// username, password and credential-bearing addresses as part of the ordinary
// sync, so it authenticates and serves without the operator typing anything and
// a rotation on the source reaches it on the next scheduled run. There is no
// separate "provision credentials" action and no client function for one — that
// action existed for two days and was removed. The only credential an operator
// still supplies is the Schedules Direct password (write-only upstream, so
// nothing on the source can be read), stored once on the target.
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
  /**
   * Per-target logo replication. DEFAULT ON since bead 2yq19.
   *
   * It shipped OFF (ADR-013 S9) because the logos importer carries a
   * streaming-upload cost S9 judged wrong to run every interval — a COST
   * decision, not a correctness one. Under the faithful-copy principle an OFF
   * default is a silent omission: the operator who never finds the toggle gets
   * a replica with no artwork. The cost is answered by
   * `logo_sync_interval_hours` instead, so the slice still does not run every
   * cycle. The card offers a toggle; nothing flips it implicitly.
   */
  sync_logos: boolean;
  /**
   * How often the logo slice may run, in hours (default 24). `0` means every
   * cycle. This is the throttle that lets `sync_logos` default ON.
   */
  logo_sync_interval_hours: number;
  /** When the logo slice last actually ran; null == never (so the next cycle carries it). */
  last_logo_sync_at?: string | null;
  /**
   * Core-settings blobs this target declines (bead 10wnq). Empty means
   * "replicate every blob the engine allows", which is the default.
   *
   * Only two blobs have a real reason to be declined: `proxy_settings`
   * (buffering tuning copied onto slower hardware) and `backup_settings` (both
   * instances backing themselves up on one schedule, with the replica's
   * retention bounded by the replica's storage). `network_access` is never
   * replicated at all and cannot be named here — naming it is rejected rather
   * than silently accepted, because accepting it would tell the operator their
   * choice mattered.
   */
  core_settings_excluded: string[];
  /**
   * PRESENCE of a stored Schedules Direct password — never the value. True once
   * the operator has supplied one; the backend re-sends it to the replica's
   * Schedules Direct EPG sources on every cycle so it is never typed twice.
   */
  has_schedules_direct_password: boolean;
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
  /** Omit to take the backend default (ON) — see `SyncTarget.sync_logos`. */
  sync_logos?: boolean;
  /** Omit to take the backend default (24). `0` means every cycle. */
  logo_sync_interval_hours?: number;
  /** Core-settings blobs to decline. Omit to replicate all of them. */
  core_settings_excluded?: string[];
  /**
   * The ONE credential an operator types, and they type it once. Ask for it
   * only when `getSyncSourceCredentialNeeds()` reports
   * `needs_schedules_direct_password` — this instance having no Schedules
   * Direct EPG source means there is nothing for the value to be written onto.
   */
  schedules_direct_password?: string;
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
  sync_logos?: boolean;
  logo_sync_interval_hours?: number;
  /** Omit to leave the stored list untouched; `[]` clears it. */
  core_settings_excluded?: string[];
  /** Omit to leave the stored value untouched; `''` clears it. */
  schedules_direct_password?: string;
}

/**
 * What this instance CANNOT harvest and therefore has to be typed once.
 *
 * Normally nothing: every provider credential is read off the source's own
 * records on every cycle. The Schedules Direct password is the sole exception —
 * Dispatcharr marks it write-only and never returns it — so the create form
 * shows a field for it if, and only if, this reports one.
 */
export interface SyncSourceCredentialNeeds {
  needs_schedules_direct_password: boolean;
  /** Operator-facing names of the Schedules Direct EPG sources on this instance. */
  schedules_direct_sources: string[];
}

export async function getSyncSourceCredentialNeeds(): Promise<SyncSourceCredentialNeeds> {
  return fetchJson(`${API_BASE}/sync-targets/source-credential-needs`);
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
