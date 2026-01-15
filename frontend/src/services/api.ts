import type {
  Channel,
  ChannelGroup,
  ChannelProfile,
  Stream,
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
} from '../types';
import { logger } from '../utils/logger';
import {
  type TimezonePreference,
  type NormalizeOptions,
  type NumberSeparator,
  type PrefixOrder,
  getStreamQualityPriority,
  sortStreamsByQuality,
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
  normalizeStreamName,
  filterStreamsByTimezone,
} from './streamNormalization';
// Re-export stream normalization utilities for backward compatibility
export type {
  TimezonePreference,
  NormalizeOptions,
  NumberSeparator,
  PrefixOrder,
};
export {
  getStreamQualityPriority,
  sortStreamsByQuality,
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
  normalizeStreamName,
  filterStreamsByTimezone,
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
      logger.error(`API error: ${method} ${url} - ${response.status} ${response.statusText}`);
      throw new Error(`API error: ${response.status} ${response.statusText}`);
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
  await fetch(`${API_BASE}/channel-groups/${id}`, { method: 'DELETE' });
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
  return fetchJson(`${API_BASE}/channel-groups/orphaned`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
    body: groupIds ? JSON.stringify({ group_ids: groupIds }) : undefined,
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

export async function getStreamGroups(bypassCache?: boolean): Promise<string[]> {
  const params = bypassCache ? '?bypass_cache=true' : '';
  return fetchJson(`${API_BASE}/stream-groups${params}`);
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
export async function getHealth(): Promise<{ status: string; service: string }> {
  return fetchJson(`${API_BASE}/health`);
}

// Settings
export type Theme = 'dark' | 'light' | 'high-contrast';

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'WARNING' | 'ERROR' | 'CRITICAL';

// Sort criteria for stream sorting
export type SortCriterion = 'resolution' | 'bitrate' | 'framerate';
export type SortEnabledMap = Record<SortCriterion, boolean>;

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
  // Stream probe settings
  stream_probe_enabled: boolean;  // Enable scheduled background probing
  stream_probe_interval_hours: number;  // Hours between auto-probe cycles
  stream_probe_batch_size: number;  // Streams to probe per scheduled cycle
  stream_probe_timeout: number;  // Timeout in seconds for each probe
  stream_probe_schedule_time: string;  // Time of day to run probes (HH:MM, 24h format)
  probe_channel_groups: string[];  // Channel group names to probe (empty = all groups)
  bitrate_sample_duration: number;  // Duration in seconds to sample stream for bitrate (10, 20, or 30)
  parallel_probing_enabled: boolean;  // Probe streams from different M3Us simultaneously
  skip_recently_probed_hours: number;  // Skip streams probed within last N hours (0 = always probe)
  stream_sort_priority: SortCriterion[];  // Priority order for Smart Sort (e.g., ['resolution', 'bitrate', 'framerate'])
  stream_sort_enabled: SortEnabledMap;  // Which sort criteria are enabled (e.g., { resolution: true, bitrate: true, framerate: false })
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
  // Stream probe settings
  stream_probe_enabled?: boolean;  // Optional - enable scheduled probing, defaults to true
  stream_probe_interval_hours?: number;  // Optional - hours between auto-probe cycles, defaults to 24
  stream_probe_batch_size?: number;  // Optional - streams per scheduled cycle, defaults to 10
  stream_probe_timeout?: number;  // Optional - timeout in seconds, defaults to 30
  stream_probe_schedule_time?: string;  // Optional - time of day for probes (HH:MM), defaults to "03:00"
  probe_channel_groups?: string[];  // Optional - channel group names to probe, empty = all groups
  bitrate_sample_duration?: number;  // Optional - duration in seconds to sample stream for bitrate (10, 20, or 30), defaults to 10
  parallel_probing_enabled?: boolean;  // Optional - probe streams from different M3Us simultaneously, defaults to true
  skip_recently_probed_hours?: number;  // Optional - skip streams probed within last N hours, defaults to 0 (always probe)
  stream_sort_priority?: SortCriterion[];  // Optional - priority order for Smart Sort, defaults to ['resolution', 'bitrate', 'framerate']
  stream_sort_enabled?: SortEnabledMap;  // Optional - which sort criteria are enabled, defaults to all true
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
  await fetch(`${API_BASE}/epg/sources/${id}`, { method: 'DELETE' });
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
export async function getEPGGrid(): Promise<EPGProgram[]> {
  return fetchJson(`${API_BASE}/epg/grid`);
}

// Get LCN (Logical Channel Number / Gracenote ID) for a TVG-ID from EPG sources
export async function getEPGLcnByTvgId(tvgId: string): Promise<{ tvg_id: string; lcn: string; source: string }> {
  return fetchJson(`${API_BASE}/epg/lcn?tvg_id=${encodeURIComponent(tvgId)}`);
}

// Batch fetch LCN for multiple TVG-IDs at once (more efficient than individual calls)
export async function getEPGLcnBatch(tvgIds: string[]): Promise<{
  results: Record<string, { lcn: string; source: string }>;
}> {
  return fetchJson(`${API_BASE}/epg/lcn/batch`, {
    method: 'POST',
    body: JSON.stringify({ tvg_ids: tvgIds }),
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
 */
export async function probeAllStreams(channelGroups?: string[]): Promise<{ status: string; message: string }> {
  logger.debug('[Probe] probeAllStreams called with groups:', channelGroups);

  try {
    const result = await fetchJson(`${API_BASE}/stream-stats/probe/all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel_groups: channelGroups || [] }),
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
  failed_streams: Array<{ id: number; name: string; url?: string }>;
  skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
  success_count: number;
  failed_count: number;
  skipped_count: number;
}> {
  return fetchJson(`${API_BASE}/stream-stats/probe/results`, {
    method: 'GET',
  }) as Promise<{
    success_streams: Array<{ id: number; name: string; url?: string }>;
    failed_streams: Array<{ id: number; name: string; url?: string }>;
    skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
    success_count: number;
    failed_count: number;
    skipped_count: number;
  }>;
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
  failed_streams: Array<{ id: number; name: string; url?: string }>;
  skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
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
