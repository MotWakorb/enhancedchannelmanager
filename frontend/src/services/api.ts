import type {
  Channel,
  ChannelGroup,
  ChannelProfile,
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
  StreamProfile,
  DummyEPGCustomProperties,
  JournalQueryParams,
  JournalResponse,
  JournalStats,
  ChannelStatsResponse,
  ChannelStats,
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
  NormalizationMigrationStatus,
  NormalizationMigrationResult,
  TagGroup,
  Tag,
  CreateTagGroupRequest,
  UpdateTagGroupRequest,
  AddTagsRequest,
  AddTagsResponse,
  UpdateTagRequest,
  TestTagsResponse,
} from '../types';
import { logger } from '../utils/logger';
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

/**
 * Build a query string from an object of parameters.
 * Filters out undefined/null values and converts to string.
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

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const method = options?.method || 'GET';
  logger.debug(`API request: ${method} ${url}`);

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      // Try to extract error detail from response body
      let errorDetail = response.statusText;
      try {
        const errorBody = await response.json();
        if (errorBody.detail) {
          errorDetail = errorBody.detail;
        }
      } catch {
        // Response body isn't JSON or couldn't be parsed
      }
      logger.error(`API error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new Error(errorDetail);
    }

    const data = await response.json();
    logger.info(`API success: ${method} ${url} - ${response.status}`);
    return data;
  } catch (error) {
    logger.exception(`API request failed: ${method} ${url}`, error as Error);
    throw error;
  }
}

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

export async function getChannel(id: number): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${id}`);
}

export async function getChannelStreams(channelId: number): Promise<Stream[]> {
  return fetchJson(`${API_BASE}/channels/${channelId}/streams`);
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
}): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels`, {
    method: 'POST',
    body: JSON.stringify(data),
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
}

/**
 * Commit multiple channel operations in a single request.
 * This is much more efficient than making individual API calls for 1000+ operations.
 */
export async function bulkCommit(request: BulkCommitRequest): Promise<BulkCommitResponse> {
  return fetchJson(`${API_BASE}/channels/bulk-commit`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
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

export async function getChannelGroupsWithStreams(): Promise<{
  groups: Array<{ id: number; name: string }>;
  total_groups: number;
}> {
  return fetchJson(`${API_BASE}/channel-groups/with-streams`);
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

export async function invalidateCache(prefix?: string): Promise<{ message: string }> {
  const params = prefix ? `?prefix=${encodeURIComponent(prefix)}` : '';
  return fetchJson(`${API_BASE}/cache/invalidate${params}`, { method: 'POST' });
}

export async function getCacheStats(): Promise<{ entry_count: number; entries: Array<{ key: string; age_seconds: number }> }> {
  return fetchJson(`${API_BASE}/cache/stats`);
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

export async function refreshAllM3UAccounts(): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/m3u/refresh`, {
    method: 'POST',
  });
}

export async function refreshM3UVod(id: number): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/m3u/accounts/${id}/refresh-vod`, {
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

export async function createServerGroup(data: { name: string }): Promise<ServerGroup> {
  return fetchJson(`${API_BASE}/m3u/server-groups`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateServerGroup(id: number, data: Partial<ServerGroup>): Promise<ServerGroup> {
  return fetchJson(`${API_BASE}/m3u/server-groups/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteServerGroup(id: number): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/m3u/server-groups/${id}`, {
    method: 'DELETE',
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

      // Compare versions
      const updateAvailable = latestVersion !== currentVersion &&
        currentVersion !== 'unknown' &&
        latestVersion !== 'unknown';

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

      // Compare versions
      const updateAvailable = latestVersion !== currentVersion &&
        currentVersion !== 'unknown' &&
        latestVersion !== 'unknown';

      return {
        updateAvailable,
        latestVersion,
        releaseUrl: data.html_url,
        releaseNotes: data.body,
      };
    }
  } catch (error) {
    console.warn('Failed to check for updates:', error);
    return { updateAvailable: false };
  }
}

// Settings
export type Theme = 'dark' | 'light' | 'high-contrast';

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'WARNING' | 'ERROR' | 'CRITICAL';

// Sort criteria for stream sorting
export type SortCriterion = 'resolution' | 'bitrate' | 'framerate' | 'm3u_priority' | 'audio_channels';
export type SortEnabledMap = Record<SortCriterion, boolean>;

// M3U account priorities for sorting - maps account ID (as string) to priority value
export type M3UAccountPriorities = Record<string, number>;

export type GracenoteConflictMode = 'ask' | 'skip' | 'overwrite';

export interface SettingsResponse {
  url: string;
  username: string;
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
  default_channel_profile_ids: number[];
  linked_m3u_accounts: number[][];  // List of link groups, each is a list of account IDs
  epg_auto_match_threshold: number;  // 0-100, confidence score threshold for auto-matching
  custom_network_prefixes: string[];  // User-defined network prefixes to strip
  custom_network_suffixes: string[];  // User-defined network suffixes to strip
  stats_poll_interval: number;  // Seconds between stats polling (default 10)
  user_timezone: string;  // IANA timezone name (e.g. "America/Los_Angeles")
  backend_log_level: string;  // Backend log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  frontend_log_level: string;  // Frontend log level (DEBUG, INFO, WARN, ERROR)
  vlc_open_behavior: string;  // VLC open behavior: "protocol_only", "m3u_fallback", "m3u_only"
  // Stream probe settings (scheduled probing is controlled by Task Engine)
  stream_probe_batch_size: number;  // Streams to probe per scheduled cycle
  stream_probe_timeout: number;  // Timeout in seconds for each probe
  stream_probe_schedule_time: string;  // Time of day to run probes (HH:MM, 24h format)
  bitrate_sample_duration: number;  // Duration in seconds to sample stream for bitrate (10, 20, or 30)
  parallel_probing_enabled: boolean;  // Probe streams from different M3Us simultaneously
  max_concurrent_probes: number;  // Max simultaneous probes when parallel probing is enabled (1-16)
  skip_recently_probed_hours: number;  // Skip streams probed within last N hours (0 = always probe)
  refresh_m3us_before_probe: boolean;  // Refresh all M3U accounts before starting probe
  auto_reorder_after_probe: boolean;  // Automatically reorder streams in channels after probe completes
  stream_fetch_page_limit: number;  // Max pages when fetching streams (pages * 500 = max streams)
  stream_sort_priority: SortCriterion[];  // Priority order for Smart Sort (e.g., ['resolution', 'bitrate', 'framerate'])
  stream_sort_enabled: SortEnabledMap;  // Which sort criteria are enabled (e.g., { resolution: true, bitrate: true, framerate: false })
  m3u_account_priorities: M3UAccountPriorities;  // M3U account priorities for sorting (account_id -> priority)
  deprioritize_failed_streams: boolean;  // When enabled, failed/timeout/pending streams sort to bottom
}

export interface TestConnectionResult {
  success: boolean;
  message: string;
}

export async function getSettings(): Promise<SettingsResponse> {
  return fetchJson(`${API_BASE}/settings`);
}

export async function saveSettings(settings: {
  url: string;
  username: string;
  password?: string;  // Optional - only required when changing URL or username
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
  default_channel_profile_ids?: number[];  // Optional - empty array means no defaults
  linked_m3u_accounts?: number[][];  // Optional - list of link groups
  epg_auto_match_threshold?: number;  // Optional - 0-100, defaults to 80
  custom_network_prefixes?: string[];  // Optional - user-defined network prefixes
  custom_network_suffixes?: string[];  // Optional - user-defined network suffixes
  stats_poll_interval?: number;  // Optional - seconds between stats polling, defaults to 10
  user_timezone?: string;  // Optional - IANA timezone name (e.g. "America/Los_Angeles")
  backend_log_level?: string;  // Optional - Backend log level, defaults to INFO
  frontend_log_level?: string;  // Optional - Frontend log level, defaults to INFO
  vlc_open_behavior?: string;  // Optional - VLC open behavior: "protocol_only", "m3u_fallback", "m3u_only"
  // Stream probe settings (scheduled probing is controlled by Task Engine)
  stream_probe_batch_size?: number;  // Optional - streams per scheduled cycle, defaults to 10
  stream_probe_timeout?: number;  // Optional - timeout in seconds, defaults to 30
  stream_probe_schedule_time?: string;  // Optional - time of day for probes (HH:MM), defaults to "03:00"
  bitrate_sample_duration?: number;  // Optional - duration in seconds to sample stream for bitrate (10, 20, or 30), defaults to 10
  parallel_probing_enabled?: boolean;  // Optional - probe streams from different M3Us simultaneously, defaults to true
  skip_recently_probed_hours?: number;  // Optional - skip streams probed within last N hours, defaults to 0 (always probe)
  refresh_m3us_before_probe?: boolean;  // Optional - refresh all M3U accounts before starting probe, defaults to true
  auto_reorder_after_probe?: boolean;  // Optional - automatically reorder streams after probe, defaults to false
  stream_fetch_page_limit?: number;  // Optional - max pages when fetching streams, defaults to 200 (100K streams)
  stream_sort_priority?: SortCriterion[];  // Optional - priority order for Smart Sort, defaults to ['resolution', 'bitrate', 'framerate']
  stream_sort_enabled?: SortEnabledMap;  // Optional - which sort criteria are enabled, defaults to all true
  m3u_account_priorities?: M3UAccountPriorities;  // Optional - M3U account priorities for sorting
  deprioritize_failed_streams?: boolean;  // Optional - deprioritize failed/timeout/pending streams in sort, defaults to true
}): Promise<{ status: string; configured: boolean }> {
  return fetchJson(`${API_BASE}/settings`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

export async function testConnection(settings: {
  url: string;
  username: string;
  password: string;
}): Promise<TestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/test`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

export async function restartServices(): Promise<{ success: boolean; message: string }> {
  return fetchJson(`${API_BASE}/settings/restart-services`, {
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
  return fetchJson(`${API_BASE}/channels/logos${query}`);
}

export async function getLogo(id: number): Promise<Logo> {
  return fetchJson(`${API_BASE}/channels/logos/${id}`);
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
  api_key?: string | null;
  is_active?: boolean;
  refresh_interval?: number;
  priority?: number;
  custom_properties?: DummyEPGCustomProperties | Record<string, unknown> | null;
}

export async function createEPGSource(data: CreateEPGSourceRequest): Promise<EPGSource> {
  return fetchJson(`${API_BASE}/epg/sources`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateEPGSource(id: number, data: Partial<EPGSource>): Promise<EPGSource> {
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

export async function getEPGDataById(id: number): Promise<EPGData> {
  return fetchJson(`${API_BASE}/epg/data/${id}`);
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

// Stream Profiles
export async function getStreamProfiles(): Promise<StreamProfile[]> {
  return fetchJson(`${API_BASE}/stream-profiles`);
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

export async function bulkUpdateProfileChannels(
  profileId: number,
  data: { channel_ids: number[]; enabled: boolean }
): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/channel-profiles/${profileId}/channels/bulk-update`, {
    method: 'PATCH',
    body: JSON.stringify(data),
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

// Options for bulk channel creation
export interface BulkCreateOptions {
  timezonePreference?: TimezonePreference;
  stripCountryPrefix?: boolean;
  keepCountryPrefix?: boolean;       // Keep and normalize country prefix (e.g., "US: ESPN" -> "US | ESPN")
  countrySeparator?: NumberSeparator; // Separator for country prefix when keeping
  stripNetworkPrefix?: boolean;      // Strip network prefixes like "CHAMP |", "PPV |" etc.
  customNetworkPrefixes?: string[];  // Additional user-defined prefixes to strip
  stripNetworkSuffix?: boolean;      // Strip network suffixes like "(ENGLISH)", "[LIVE]", "BACKUP" etc.
  customNetworkSuffixes?: string[];  // Additional user-defined suffixes to strip
  addChannelNumber?: boolean;
  numberSeparator?: NumberSeparator;
  prefixOrder?: PrefixOrder;         // Order of prefixes: 'number-first' (100 | US | Name) or 'country-first' (US | 100 | Name)
}

// Bulk Channel Creation
// Groups streams with normalized names into the same channel (merging streams from different M3Us and quality variants)
export async function bulkCreateChannelsFromStreams(
  streams: { id: number; name: string; logo_url?: string | null }[],
  startingNumber: number,
  channelGroupId: number | null,
  timezonePreferenceOrOptions: TimezonePreference | BulkCreateOptions = 'both'
): Promise<{ created: Channel[]; errors: string[]; mergedCount: number }> {
  logger.info(`Starting bulk channel creation from ${streams.length} streams`, {
    startingNumber,
    channelGroupId,
  });

  // Handle both old signature (just TimezonePreference) and new signature (BulkCreateOptions)
  let timezonePreference: TimezonePreference = 'both';
  let stripCountry = false;
  let keepCountry = false;
  let countrySeparator: NumberSeparator = '|';
  let stripNetwork = false;
  let customNetworkPrefixes: string[] | undefined;
  let stripSuffix = false;
  let customNetworkSuffixes: string[] | undefined;
  let addChannelNumber = false;
  let numberSeparator: NumberSeparator = '|';
  let prefixOrder: PrefixOrder = 'number-first';

  if (typeof timezonePreferenceOrOptions === 'object') {
    timezonePreference = timezonePreferenceOrOptions.timezonePreference ?? 'both';
    stripCountry = timezonePreferenceOrOptions.stripCountryPrefix ?? false;
    keepCountry = timezonePreferenceOrOptions.keepCountryPrefix ?? false;
    countrySeparator = timezonePreferenceOrOptions.countrySeparator ?? '|';
    stripNetwork = timezonePreferenceOrOptions.stripNetworkPrefix ?? false;
    customNetworkPrefixes = timezonePreferenceOrOptions.customNetworkPrefixes;
    stripSuffix = timezonePreferenceOrOptions.stripNetworkSuffix ?? false;
    customNetworkSuffixes = timezonePreferenceOrOptions.customNetworkSuffixes;
    addChannelNumber = timezonePreferenceOrOptions.addChannelNumber ?? false;
    numberSeparator = timezonePreferenceOrOptions.numberSeparator ?? '|';
    prefixOrder = timezonePreferenceOrOptions.prefixOrder ?? 'number-first';
  } else {
    timezonePreference = timezonePreferenceOrOptions;
  }

  logger.debug('Bulk create options', {
    timezonePreference,
    stripCountry,
    keepCountry,
    stripNetwork,
    stripSuffix,
    addChannelNumber,
  });

  const created: Channel[] = [];
  const errors: string[] = [];
  // Cache logos to avoid repeated lookups for the same URL
  const logoCache = new Map<string, Logo>();

  // Filter streams based on timezone preference first
  const filteredStreams = filterStreamsByTimezone(streams, timezonePreference);
  logger.debug(`Filtered ${streams.length} streams to ${filteredStreams.length} based on timezone preference`);

  // Group streams by normalized name to merge identical channels from different M3Us and quality variants
  // The normalized name is used as the key, but we track original names for the channel name selection
  const streamsByNormalizedName = new Map<string, { id: number; name: string; logo_url?: string | null }[]>();
  for (const stream of filteredStreams) {
    const normalizedName = normalizeStreamName(stream.name, {
      timezonePreference,
      stripCountryPrefix: stripCountry,
      keepCountryPrefix: keepCountry,
      countrySeparator,
      stripNetworkPrefix: stripNetwork,
      customNetworkPrefixes,
      stripNetworkSuffix: stripSuffix,
      customNetworkSuffixes,
    });
    const existing = streamsByNormalizedName.get(normalizedName);
    if (existing) {
      existing.push(stream);
    } else {
      streamsByNormalizedName.set(normalizedName, [stream]);
    }
  }

  // Count how many streams were merged (filtered streams - unique normalized names)
  const mergedCount = filteredStreams.length - streamsByNormalizedName.size;
  logger.info(`Grouped ${filteredStreams.length} streams into ${streamsByNormalizedName.size} unique channels (${mergedCount} merged)`);

  // Create one channel per unique normalized name
  let channelIndex = 0;
  for (const [normalizedName, groupedStreams] of streamsByNormalizedName) {
    const channelNumber = startingNumber + channelIndex;
    channelIndex++;

    // Use the normalized name as the channel name (cleaner, without quality suffix)
    // Optionally prepend channel number and/or country prefix based on prefixOrder
    // normalizedName already has country prefix if keepCountry is true (e.g., "US | Sports Channel")
    // We need to extract it to reorder if needed
    let channelName = normalizedName;

    if (addChannelNumber && keepCountry) {
      // Both number and country enabled - need to consider order
      // normalizedName is currently: "US | Sports Channel" (country already normalized)
      // Extract country from the normalized name to reorder
      const countryMatch = normalizedName.match(new RegExp(`^([A-Z]{2,6})\\s*[${countrySeparator}]\\s*(.+)$`));
      if (countryMatch) {
        const [, countryCode, baseName] = countryMatch;
        if (prefixOrder === 'country-first') {
          // Country first: "US | 100 | Sports Channel"
          channelName = `${countryCode} ${countrySeparator} ${channelNumber} ${numberSeparator} ${baseName}`;
        } else {
          // Number first (default): "100 | US | Sports Channel"
          channelName = `${channelNumber} ${numberSeparator} ${countryCode} ${countrySeparator} ${baseName}`;
        }
      } else {
        // No country found in name, just add number
        channelName = `${channelNumber} ${numberSeparator} ${normalizedName}`;
      }
    } else if (addChannelNumber) {
      // Only number, no country
      channelName = `${channelNumber} ${numberSeparator} ${normalizedName}`;
    }
    // else: normalizedName is already correct (with or without country prefix)

    try {
      // Create the channel
      logger.debug(`Creating channel ${channelIndex}/${streamsByNormalizedName.size}: ${channelName}`);
      const channel = await createChannel({
        name: channelName,
        channel_number: channelNumber,
        channel_group_id: channelGroupId ?? undefined,
      });

      // Add all streams with this normalized name to the channel (provides multi-provider/quality redundancy)
      // Sort streams by quality so highest quality (UHD/4K) appears first
      const sortedStreams = sortStreamsByQuality(groupedStreams);
      const addedStreamIds: number[] = [];
      for (const stream of sortedStreams) {
        try {
          await addStreamToChannel(channel.id, stream.id);
          addedStreamIds.push(stream.id);
        } catch (streamError) {
          logger.error(`Failed to add stream ${stream.id} to channel ${channelName}`, streamError);
          errors.push(`Channel "${channelName}" created but stream assignment failed for stream ${stream.id}: ${streamError}`);
        }
      }
      logger.info(`Created channel: ${channelName}`, { channelId: channel.id, streamCount: addedStreamIds.length });

      // Use the first stream's logo if available (sorted streams, so highest quality first)
      const logoUrl = sortedStreams.find((s: { logo_url?: string | null }) => s.logo_url)?.logo_url;
      if (logoUrl) {
        try {
          const logo = await getOrCreateLogo(channelName, logoUrl, logoCache);
          await updateChannel(channel.id, { logo_id: logo.id });
          created.push({ ...channel, streams: addedStreamIds, logo_id: logo.id });
        } catch (logoError) {
          // Logo assignment failed, but channel was still created
          logger.warn(`Logo assignment failed for channel: ${channelName}`, logoError);
          errors.push(`Channel "${channelName}" created but logo assignment failed: ${logoError}`);
          created.push({ ...channel, streams: addedStreamIds });
        }
      } else {
        created.push({ ...channel, streams: addedStreamIds });
      }
    } catch (error) {
      logger.error(`Failed to create channel: ${channelName}`, error);
      errors.push(`Failed to create channel "${channelName}": ${error}`);
    }
  }

  logger.info(`Bulk channel creation complete`, {
    channelsCreated: created.length,
    errors: errors.length,
    mergedCount,
  });

  return { created, errors, mergedCount };
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
  });
  return fetchJson(`${API_BASE}/journal${query}`);
}

export async function getJournalStats(): Promise<JournalStats> {
  return fetchJson(`${API_BASE}/journal/stats`);
}

export async function purgeJournalEntries(days: number): Promise<{ deleted_count: number }> {
  return fetchJson(`${API_BASE}/journal/purge?days=${days}`, {
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
 * Get detailed stats for a specific channel.
 * Includes per-client information, buffer status, codec details, etc.
 */
export async function getChannelStatsDetail(channelId: number): Promise<ChannelStats> {
  return fetchJson(`${API_BASE}/stats/channels/${channelId}`);
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
 * Stop a specific client connection.
 */
export async function stopClient(channelId: number | string): Promise<{ success: boolean }> {
  return fetchJson(`${API_BASE}/stats/channels/${channelId}/stop-client`, {
    method: 'POST',
  });
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
// Stream Stats / Probing
// =============================================================================

/**
 * Get all stream probe statistics.
 */
export async function getStreamStats(): Promise<import('../types').StreamStats[]> {
  return fetchJson(`${API_BASE}/stream-stats`);
}

/**
 * Get probe stats for a specific stream.
 */
export async function getStreamStatsById(streamId: number): Promise<import('../types').StreamStats> {
  return fetchJson(`${API_BASE}/stream-stats/${streamId}`);
}

/**
 * Get probe stats for multiple streams by their IDs.
 */
export async function getStreamStatsByIds(streamIds: number[]): Promise<Record<number, import('../types').StreamStats>> {
  return fetchJson(`${API_BASE}/stream-stats/by-ids`, {
    method: 'POST',
    body: JSON.stringify({ stream_ids: streamIds }),
  });
}

/**
 * Get summary of stream probe statistics.
 */
export async function getStreamStatsSummary(): Promise<import('../types').StreamStatsSummary> {
  return fetchJson(`${API_BASE}/stream-stats/summary`);
}

/**
 * Probe a single stream on-demand.
 */
export async function probeStream(streamId: number): Promise<import('../types').StreamStats> {
  logger.debug(`[Probe] probeStream called for stream ID: ${streamId}`);

  try {
    const result = await fetchJson(`${API_BASE}/stream-stats/probe/${streamId}`, {
      method: 'POST',
    }) as import('../types').StreamStats;
    logger.debug(`[Probe] probeStream succeeded for stream ${streamId}:`, result);
    return result;
  } catch (error) {
    console.error(`[Probe] probeStream failed for stream ${streamId}:`, error);
    throw error;
  }
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
    console.error(`[Probe] probeBulkStreams failed:`, error);
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
    console.error('[Probe] probeAllStreams failed:', error);
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
  percentage: number;
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
    percentage: number;
  }>;
}

export async function getProbeResults(): Promise<{
  success_streams: Array<{ id: number; name: string; url?: string }>;
  failed_streams: Array<{ id: number; name: string; url?: string; error?: string }>;
  skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
  success_count: number;
  failed_count: number;
  skipped_count: number;
}> {
  return fetchJson(`${API_BASE}/stream-stats/probe/results`, {
    method: 'GET',
  }) as Promise<{
    success_streams: Array<{ id: number; name: string; url?: string }>;
    failed_streams: Array<{ id: number; name: string; url?: string; error?: string }>;
    skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
    success_count: number;
    failed_count: number;
    skipped_count: number;
  }>;
}

/**
 * Dismiss probe failures for the specified streams.
 * Dismissed streams won't appear in failed lists until re-probed.
 */
export async function dismissStreamStats(streamIds: number[]): Promise<{ dismissed: number; stream_ids: number[] }> {
  return fetchJson(`${API_BASE}/stream-stats/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stream_ids: streamIds }),
  }) as Promise<{ dismissed: number; stream_ids: number[] }>;
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

/**
 * Get list of dismissed stream IDs.
 */
export async function getDismissedStreamIds(): Promise<{ dismissed_stream_ids: number[]; count: number }> {
  return fetchJson(`${API_BASE}/stream-stats/dismissed`, {
    method: 'GET',
  }) as Promise<{ dismissed_stream_ids: number[]; count: number }>;
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
}

export interface CronPreset {
  name: string;
  expression: string;
  description: string;
}

export interface CronValidationResult {
  valid: boolean;
  error?: string;
  description?: string;
  next_runs?: string[];
}

export interface TaskEngineStatus {
  running: boolean;
  check_interval: number;
  max_concurrent: number;
  active_tasks: string[];
  active_task_count: number;
  registered_task_count: number;
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

export async function runTask(taskId: string, scheduleId?: number): Promise<{
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
  const body = scheduleId ? { schedule_id: scheduleId } : undefined;
  return fetchJson(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/run`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
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

export async function getAllTaskHistory(limit = 100, offset = 0): Promise<{ history: TaskExecution[] }> {
  const query = buildQuery({ limit, offset });
  return fetchJson(`${API_BASE}/tasks/history/all${query}`, {
    method: 'GET',
  });
}

export async function getTaskEngineStatus(): Promise<TaskEngineStatus> {
  return fetchJson(`${API_BASE}/tasks/engine/status`, {
    method: 'GET',
  });
}

export async function getCronPresets(): Promise<{ presets: CronPreset[] }> {
  return fetchJson(`${API_BASE}/cron/presets`, {
    method: 'GET',
  });
}

export async function validateCronExpression(expression: string): Promise<CronValidationResult> {
  return fetchJson(`${API_BASE}/cron/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expression }),
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

export async function getAllTaskParameterSchemas(): Promise<{ schemas: Record<string, { description: string; parameters: TaskParameterSchema[] }> }> {
  return fetchJson(`${API_BASE}/tasks/parameter-schemas`, {
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

export interface CreateNotificationData {
  notification_type?: 'info' | 'success' | 'warning' | 'error';
  title?: string;
  message: string;
  source?: string;
  source_id?: string;
  action_label?: string;
  action_url?: string;
  metadata?: Record<string, unknown>;
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

export async function createNotification(data: CreateNotificationData): Promise<Notification> {
  const query = buildQuery({
    message: data.message,
    notification_type: data.notification_type,
    title: data.title,
    source: data.source,
    source_id: data.source_id,
    action_label: data.action_label,
    action_url: data.action_url,
  });
  return fetchJson(`${API_BASE}/notifications${query}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: data.metadata ? JSON.stringify({ metadata: data.metadata }) : undefined,
  });
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
// Alert Methods
// =============================================================================

export interface AlertMethodType {
  type: string;
  display_name: string;
  required_fields: string[];
  optional_fields: Record<string, unknown>;
}

// Granular alert source filtering types
export type AlertFilterMode = 'all' | 'only_selected' | 'all_except';

export interface AlertSourceEpgRefresh {
  enabled: boolean;
  filter_mode: AlertFilterMode;
  source_ids: number[];
}

export interface AlertSourceM3uRefresh {
  enabled: boolean;
  filter_mode: AlertFilterMode;
  account_ids: number[];
}

export interface AlertSourceProbeFailures {
  enabled: boolean;
  min_failures: number;
}

export interface AlertSources {
  version?: number;
  epg_refresh?: AlertSourceEpgRefresh;
  m3u_refresh?: AlertSourceM3uRefresh;
  probe_failures?: AlertSourceProbeFailures;
}

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
  alert_sources: AlertSources | null;
  last_sent_at: string | null;
  created_at: string | null;
}

export interface AlertMethodCreate {
  name: string;
  method_type: string;
  config: Record<string, unknown>;
  enabled?: boolean;
  notify_info?: boolean;
  notify_success?: boolean;
  notify_warning?: boolean;
  notify_error?: boolean;
  alert_sources?: AlertSources | null;
}

export interface AlertMethodUpdate {
  name?: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
  notify_info?: boolean;
  notify_success?: boolean;
  notify_warning?: boolean;
  notify_error?: boolean;
  alert_sources?: AlertSources | null;
}

export async function getAlertMethodTypes(): Promise<AlertMethodType[]> {
  return fetchJson(`${API_BASE}/alert-methods/types`);
}

export async function getAlertMethods(): Promise<AlertMethod[]> {
  return fetchJson(`${API_BASE}/alert-methods`);
}

export async function getAlertMethod(methodId: number): Promise<AlertMethod> {
  return fetchJson(`${API_BASE}/alert-methods/${methodId}`);
}

export async function createAlertMethod(data: AlertMethodCreate): Promise<{ id: number; name: string; method_type: string; enabled: boolean }> {
  return fetchJson(`${API_BASE}/alert-methods`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateAlertMethod(methodId: number, data: AlertMethodUpdate): Promise<{ success: boolean }> {
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
// Normalization Rules API
// =============================================================================

/**
 * Get all normalization rule groups
 */
export async function getNormalizationGroups(): Promise<{ groups: NormalizationRuleGroup[] }> {
  return fetchJson(`${API_BASE}/normalization/groups`);
}

/**
 * Get a single normalization rule group by ID
 */
export async function getNormalizationGroup(groupId: number): Promise<NormalizationRuleGroup> {
  return fetchJson(`${API_BASE}/normalization/groups/${groupId}`);
}

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
 * Get a single normalization rule by ID
 */
export async function getNormalizationRule(ruleId: number): Promise<NormalizationRule> {
  return fetchJson(`${API_BASE}/normalization/rules/${ruleId}`);
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
 * Get normalization migration status
 */
export async function getNormalizationMigrationStatus(): Promise<NormalizationMigrationStatus> {
  return fetchJson(`${API_BASE}/normalization/migration/status`);
}

/**
 * Run normalization migration to create built-in rules
 */
export async function runNormalizationMigration(force?: boolean): Promise<NormalizationMigrationResult> {
  const query = force ? '?force=true' : '';
  return fetchJson(`${API_BASE}/normalization/migration/run${query}`, {
    method: 'POST',
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
 * Test text against a tag group to find matches
 */
export async function testTagGroup(groupId: number, text: string): Promise<TestTagsResponse> {
  return fetchJson(`${API_BASE}/tags/test`, {
    method: 'POST',
    body: JSON.stringify({ group_id: groupId, text }),
  });
}
