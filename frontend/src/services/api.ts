import type {
  Channel,
  ChannelGroup,
  Stream,
  M3UAccount,
  M3UGroupSetting,
  Logo,
  PaginatedResponse,
  EPGSource,
  EPGData,
  StreamProfile,
} from '../types';

const API_BASE = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

// Channels
export async function getChannels(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  channelGroup?: number;
}): Promise<PaginatedResponse<Channel>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);
  if (params?.channelGroup) searchParams.set('channel_group', String(params.channelGroup));

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/channels${query ? `?${query}` : ''}`);
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

// Streams
export async function getStreams(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  channelGroup?: string;
  m3uAccount?: number;
  bypassCache?: boolean;
}): Promise<PaginatedResponse<Stream>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);
  if (params?.channelGroup) searchParams.set('channel_group_name', params.channelGroup);
  if (params?.m3uAccount) searchParams.set('m3u_account', String(params.m3uAccount));
  if (params?.bypassCache) searchParams.set('bypass_cache', 'true');

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/streams${query ? `?${query}` : ''}`);
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
  return fetchJson(`${API_BASE}/providers`);
}

export async function getProviderGroupSettings(): Promise<Record<number, M3UGroupSetting>> {
  return fetchJson(`${API_BASE}/providers/group-settings`);
}

// Health check
export async function getHealth(): Promise<{ status: string; service: string }> {
  return fetchJson(`${API_BASE}/health`);
}

// Settings
export type Theme = 'dark' | 'light' | 'high-contrast';

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
  theme: Theme;
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
  theme?: Theme;  // Optional - defaults to 'dark'
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

// Logos
export async function getLogos(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}): Promise<PaginatedResponse<Logo>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/channels/logos${query ? `?${query}` : ''}`);
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

export interface CreateEPGSourceRequest {
  name: string;
  source_type: 'xmltv' | 'schedules_direct' | 'dummy';
  url?: string | null;
  api_key?: string | null;
  is_active?: boolean;
  refresh_interval?: number;
  priority?: number;
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
  const searchParams = new URLSearchParams();
  if (params?.search) searchParams.set('search', params.search);
  if (params?.epgSource) searchParams.set('epg_source', String(params.epgSource));

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/epg/data${query ? `?${query}` : ''}`);
}

export async function getEPGDataById(id: number): Promise<EPGData> {
  return fetchJson(`${API_BASE}/epg/data/${id}`);
}

// Stream Profiles
export async function getStreamProfiles(): Promise<StreamProfile[]> {
  return fetchJson(`${API_BASE}/stream-profiles`);
}

// Helper function to get or create a logo by URL
// Dispatcharr enforces unique URLs, so we try to create first, then search if it already exists
async function getOrCreateLogo(name: string, url: string, logoCache: Map<string, Logo>): Promise<Logo> {
  // Check cache first
  const cached = logoCache.get(url);
  if (cached) {
    return cached;
  }

  try {
    // Try to create the logo
    const logo = await createLogo({ name, url });
    logoCache.set(url, logo);
    return logo;
  } catch (error) {
    // If creation failed, the logo might already exist - search for it
    // Fetch all logos and find by URL (search param may not support exact URL match)
    const allLogos = await getLogos({ pageSize: 10000 });
    const existingLogo = allLogos.results.find((l) => l.url === url);
    if (existingLogo) {
      logoCache.set(url, existingLogo);
      return existingLogo;
    }
    // If we still can't find it, re-throw the original error
    throw error;
  }
}

// Quality suffixes to strip when normalizing stream names for matching
// These are common quality/resolution indicators that don't change the channel identity
const QUALITY_SUFFIXES = [
  'FHD', 'UHD', '4K', 'HD', 'SD',
  '1080P', '1080I', '720P', '480P', '2160P',
  'HEVC', 'H264', 'H265',
];

// Network/channel prefixes that should be stripped when followed by content names
// These are networks that often prefix their content with their branding
// Format: "NETWORK | Content Name" or "NETWORK: Content Name"
const NETWORK_PREFIXES = [
  // Sports networks
  'CHAMP', 'CHAMPIONSHIP', 'PPV', 'PAY PER VIEW',
  'PREMIER', 'PREMIER LEAGUE', 'PL',
  'NFL', 'NBA', 'MLB', 'NHL', 'MLS', 'NCAA',
  'UFC', 'WWE', 'AEW', 'BOXING',
  'GOLF', 'TENNIS', 'CRICKET', 'RUGBY',
  'RACING', 'MOTORSPORT', 'F1', 'NASCAR',
  // General networks that prefix content
  'LIVE', 'SPORTS', 'MATCH', 'GAME',
  '24/7', 'LINEAR',
  // Regional sports networks pattern
  'RSN',
];

// Quality priority for stream ordering (lower number = higher priority/quality)
// Streams without quality indicators default to 720p position (priority 30)
const QUALITY_PRIORITY: Record<string, number> = {
  // Ultra HD / 4K (highest quality)
  'UHD': 10,
  '4K': 10,
  '2160P': 10,
  // Full HD
  'FHD': 20,
  '1080P': 20,
  '1080I': 21, // Slightly lower than progressive
  // HD (default level for unknown quality)
  'HD': 30,
  '720P': 30,
  // Standard Definition (lowest)
  'SD': 40,
  '480P': 40,
};

// Default priority for streams without quality indicators (treated as HD/720p)
const DEFAULT_QUALITY_PRIORITY = 30;

/**
 * Get the quality priority score for a stream name.
 * Lower score = higher quality (should appear first in the list).
 * Streams without quality indicators get DEFAULT_QUALITY_PRIORITY (HD level).
 */
export function getStreamQualityPriority(streamName: string): number {
  const upperName = streamName.toUpperCase();

  // Check for each quality indicator in the name
  for (const [quality, priority] of Object.entries(QUALITY_PRIORITY)) {
    // Match quality at word boundary or with common separators
    const pattern = new RegExp(`(?:^|[\\s\\-_|:])${quality}(?:$|[\\s\\-_|:])`, 'i');
    if (pattern.test(upperName)) {
      return priority;
    }
  }

  return DEFAULT_QUALITY_PRIORITY;
}

/**
 * Sort streams by quality priority (highest quality first).
 * Maintains stable sort for streams with same quality.
 */
export function sortStreamsByQuality<T extends { name: string }>(streams: T[]): T[] {
  return [...streams].sort((a, b) => {
    const priorityA = getStreamQualityPriority(a.name);
    const priorityB = getStreamQualityPriority(b.name);
    return priorityA - priorityB;
  });
}

export type TimezonePreference = 'east' | 'west' | 'both';

/**
 * Strip network prefix from a stream name if present.
 * Network prefixes are things like "CHAMP |", "PPV |", "NFL |" that precede content names.
 * Only strips if the prefix is followed by a separator AND substantial content.
 *
 * Examples:
 * - "CHAMP | Queens Park Rangers" → "Queens Park Rangers"
 * - "PPV | UFC 300" → "UFC 300"
 * - "ESPN" → "ESPN" (no change - it's the channel name itself)
 * - "ESPN2" → "ESPN2" (no change - suffix is part of channel identity)
 */
export function stripNetworkPrefix(name: string): string {
  const trimmedName = name.trim();

  // Sort prefixes by length (longest first) to match more specific ones first
  const sortedPrefixes = [...NETWORK_PREFIXES].sort((a, b) => b.length - a.length);

  for (const prefix of sortedPrefixes) {
    // Pattern: prefix at start, followed by separator (|, :, -, /)
    // The content after must be at least 3 characters (to avoid stripping too much)
    const pattern = new RegExp(`^${prefix}\\s*[|:\\-/]\\s*(.{3,})$`, 'i');
    const match = trimmedName.match(pattern);
    if (match) {
      return match[1].trim();
    }
  }

  return trimmedName;
}

/**
 * Detect if a stream name has a network prefix that can be stripped.
 */
export function hasNetworkPrefix(name: string): boolean {
  return stripNetworkPrefix(name) !== name.trim();
}

/**
 * Detect if a list of streams has network prefixes.
 */
export function detectNetworkPrefixes(streams: { name: string }[]): boolean {
  for (const stream of streams) {
    if (hasNetworkPrefix(stream.name)) {
      return true;
    }
  }
  return false;
}

// Common country prefixes found in stream names
// These typically appear at the start of the name followed by a separator
const COUNTRY_PREFIXES = [
  'US', 'USA', 'UK', 'CA', 'AU', 'NZ', 'IE', 'IN', 'PH', 'MX', 'BR', 'DE', 'FR', 'ES', 'IT', 'NL', 'BE', 'CH', 'AT', 'PL', 'SE', 'NO', 'DK', 'FI', 'PT', 'GR', 'TR', 'RU', 'JP', 'KR', 'CN', 'TW', 'HK', 'SG', 'MY', 'TH', 'ID', 'VN', 'PK', 'BD', 'LK', 'ZA', 'EG', 'NG', 'KE', 'GH', 'AR', 'CL', 'CO', 'PE', 'VE', 'EC', 'PR', 'DO', 'CU', 'JM', 'TT', 'BB', 'CR', 'PA', 'HN', 'SV', 'GT', 'NI', 'BZ', 'IL', 'AE', 'SA', 'QA', 'KW', 'BH', 'OM', 'JO', 'LB', 'IR', 'IQ', 'AF', 'LATAM', 'LATINO', 'LATIN',
];

// Detect if a stream name has a country prefix
// Returns the country code if found, null otherwise
export function getCountryPrefix(name: string): string | null {
  const trimmedName = name.trim();

  // Check for each country prefix at the start of the name
  // Must be followed by a separator (space, colon, hyphen, pipe, etc.) or end of match
  for (const prefix of COUNTRY_PREFIXES) {
    // Pattern: prefix at start, followed by separator
    const pattern = new RegExp(`^${prefix}(?:[\\s:\\-|/]+)`, 'i');
    if (pattern.test(trimmedName)) {
      return prefix.toUpperCase();
    }
  }

  return null;
}

// Strip country prefix and any trailing punctuation from a name
export function stripCountryPrefix(name: string): string {
  const trimmedName = name.trim();

  // Try to match and remove country prefix with separator
  for (const prefix of COUNTRY_PREFIXES) {
    const pattern = new RegExp(`^${prefix}[\\s:\\-|/]+`, 'i');
    if (pattern.test(trimmedName)) {
      return trimmedName.replace(pattern, '').trim();
    }
  }

  return trimmedName;
}

// Detect if a list of streams has country prefixes
export function detectCountryPrefixes(streams: { name: string }[]): boolean {
  for (const stream of streams) {
    if (getCountryPrefix(stream.name) !== null) {
      return true;
    }
  }
  return false;
}

// Get all unique country prefixes found in a list of streams
export function getUniqueCountryPrefixes(streams: { name: string }[]): string[] {
  const prefixes = new Set<string>();
  for (const stream of streams) {
    const prefix = getCountryPrefix(stream.name);
    if (prefix) {
      prefixes.add(prefix);
    }
  }
  return Array.from(prefixes).sort();
}

// Check if a stream name has a regional suffix (East or West)
export function getRegionalSuffix(name: string): 'east' | 'west' | null {
  // Check for East/West at the end with optional separator
  if (/[\s\-_|:]+EAST\s*$/i.test(name)) return 'east';
  if (/[\s\-_|:]+WEST\s*$/i.test(name)) return 'west';
  return null;
}

// Strip regional suffix from a name
function stripRegionalSuffix(name: string): string {
  return name.replace(/[\s\-_|:]+(?:EAST|WEST)\s*$/i, '').trim();
}

// Detect if a list of streams has regional variants (both East and West versions, or base + West)
export function detectRegionalVariants(streams: { name: string }[]): boolean {
  // Build a set of base names (without regional suffix) and track which variants exist
  const baseNames = new Map<string, Set<'east' | 'west' | 'none'>>();

  for (const stream of streams) {
    // First strip quality suffixes to get consistent base comparison
    let nameWithoutQuality = stream.name.trim();
    for (const suffix of QUALITY_SUFFIXES) {
      const pattern = new RegExp(`[\\s\\-_|:]*${suffix}\\s*$`, 'i');
      nameWithoutQuality = nameWithoutQuality.replace(pattern, '');
    }
    nameWithoutQuality = nameWithoutQuality.replace(/\s+/g, ' ').trim();

    const regional = getRegionalSuffix(nameWithoutQuality);
    const baseName = stripRegionalSuffix(nameWithoutQuality).toLowerCase();

    if (!baseNames.has(baseName)) {
      baseNames.set(baseName, new Set());
    }
    baseNames.get(baseName)!.add(regional ?? 'none');
  }

  // Check if any base name has regional variants
  // A variant exists if we have: (East or none) AND West
  // "none" is treated as East (default timezone)
  for (const [, variants] of baseNames) {
    const hasEastOrNone = variants.has('east') || variants.has('none');
    const hasWest = variants.has('west');
    if (hasEastOrNone && hasWest) {
      return true;
    }
  }

  return false;
}

// Options for normalizing stream names
export interface NormalizeOptions {
  timezonePreference?: TimezonePreference;
  stripCountryPrefix?: boolean;
  keepCountryPrefix?: boolean;       // Keep and normalize country prefix format
  countrySeparator?: NumberSeparator; // Separator to use when keeping country prefix
  stripNetworkPrefix?: boolean;      // Strip network prefixes like "CHAMP |", "PPV |" etc.
}

// Normalize a stream name for matching purposes
// Strips quality suffixes and normalizes whitespace
// timezonePreference controls how regional variants are handled:
// - 'both': keep East/West as separate channels (don't merge regional variants)
// - 'east': prefer East timezone - merge West into base name, treat non-suffixed as East
// - 'west': prefer West timezone - merge East/non-suffixed into base, keep West
// stripCountryPrefix: if true, removes country prefix (e.g., "US: Sports Channel" -> "Sports Channel")
// keepCountryPrefix: if true, keeps country prefix but normalizes format (e.g., "US: Sports Channel" -> "US | Sports Channel")
export function normalizeStreamName(name: string, timezonePreferenceOrOptions: TimezonePreference | NormalizeOptions = 'both'): string {
  // Handle both old signature (just TimezonePreference) and new signature (NormalizeOptions)
  let timezonePreference: TimezonePreference = 'both';
  let stripCountry = false;
  let keepCountry = false;
  let countrySeparator: NumberSeparator = '|';
  let stripNetwork = false;

  if (typeof timezonePreferenceOrOptions === 'object') {
    timezonePreference = timezonePreferenceOrOptions.timezonePreference ?? 'both';
    stripCountry = timezonePreferenceOrOptions.stripCountryPrefix ?? false;
    keepCountry = timezonePreferenceOrOptions.keepCountryPrefix ?? false;
    countrySeparator = timezonePreferenceOrOptions.countrySeparator ?? '|';
    stripNetwork = timezonePreferenceOrOptions.stripNetworkPrefix ?? false;
  } else {
    timezonePreference = timezonePreferenceOrOptions;
  }

  let normalized = name.trim();

  // Strip network prefix first (before country prefix, as network prefix may come before country)
  // e.g., "CHAMP | US: Queens Park Rangers" → "US: Queens Park Rangers" → then handle country
  if (stripNetwork) {
    normalized = stripNetworkPrefix(normalized);
  }

  // Handle country prefix based on options
  // keepCountryPrefix takes precedence over stripCountryPrefix if both are set
  if (keepCountry) {
    // Keep the country prefix but normalize its format
    const countryCode = getCountryPrefix(normalized);
    if (countryCode) {
      // Strip the existing prefix (with whatever separator it had)
      const nameWithoutPrefix = stripCountryPrefix(normalized);
      // Re-add it with the chosen separator
      normalized = `${countryCode} ${countrySeparator} ${nameWithoutPrefix}`;
    }
  } else if (stripCountry) {
    normalized = stripCountryPrefix(normalized);
  }

  // Build a regex that matches quality suffixes at the end of the name
  // Handle various separators: space, dash, underscore, pipe, colon
  for (const suffix of QUALITY_SUFFIXES) {
    // Match suffix at end with optional separator before it
    // Case insensitive
    const pattern = new RegExp(`[\\s\\-_|:]*${suffix}\\s*$`, 'i');
    normalized = normalized.replace(pattern, '');
  }

  // Handle regional suffixes based on timezone preference
  if (timezonePreference !== 'both') {
    const regional = getRegionalSuffix(normalized);

    // For either preference, we merge by stripping the regional suffix
    // The difference is which streams get included (handled by caller filtering)
    if (regional === 'east' || regional === 'west') {
      normalized = stripRegionalSuffix(normalized);
    }
    // Non-suffixed names stay as-is (they represent the base channel)
  }

  // Normalize multiple spaces to single space and trim
  normalized = normalized.replace(/\s+/g, ' ').trim();

  return normalized;
}

// Filter streams based on timezone preference
// - 'east': include streams without suffix OR with East suffix, exclude West
// - 'west': include streams with West suffix, exclude East and non-suffixed
// - 'both': include all streams
export function filterStreamsByTimezone<T extends { name: string }>(
  streams: T[],
  timezonePreference: TimezonePreference
): T[] {
  if (timezonePreference === 'both') {
    return streams;
  }

  return streams.filter((stream) => {
    // First normalize quality to check regional suffix properly
    let nameWithoutQuality = stream.name.trim();
    for (const suffix of QUALITY_SUFFIXES) {
      const pattern = new RegExp(`[\\s\\-_|:]*${suffix}\\s*$`, 'i');
      nameWithoutQuality = nameWithoutQuality.replace(pattern, '');
    }

    const regional = getRegionalSuffix(nameWithoutQuality);

    if (timezonePreference === 'east') {
      // Include East or no suffix (which is treated as East)
      return regional === 'east' || regional === null;
    } else {
      // West preference: include West suffix only
      return regional === 'west';
    }
  });
}

// Separator types for channel number prefix and country prefix
export type NumberSeparator = '-' | ':' | '|';

// Prefix order when both country and number are enabled
export type PrefixOrder = 'number-first' | 'country-first';

// Options for bulk channel creation
export interface BulkCreateOptions {
  timezonePreference?: TimezonePreference;
  stripCountryPrefix?: boolean;
  keepCountryPrefix?: boolean;       // Keep and normalize country prefix (e.g., "US: ESPN" -> "US | ESPN")
  countrySeparator?: NumberSeparator; // Separator for country prefix when keeping
  stripNetworkPrefix?: boolean;      // Strip network prefixes like "CHAMP |", "PPV |" etc.
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
  // Handle both old signature (just TimezonePreference) and new signature (BulkCreateOptions)
  let timezonePreference: TimezonePreference = 'both';
  let stripCountry = false;
  let keepCountry = false;
  let countrySeparator: NumberSeparator = '|';
  let stripNetwork = false;
  let addChannelNumber = false;
  let numberSeparator: NumberSeparator = '|';
  let prefixOrder: PrefixOrder = 'number-first';

  if (typeof timezonePreferenceOrOptions === 'object') {
    timezonePreference = timezonePreferenceOrOptions.timezonePreference ?? 'both';
    stripCountry = timezonePreferenceOrOptions.stripCountryPrefix ?? false;
    keepCountry = timezonePreferenceOrOptions.keepCountryPrefix ?? false;
    countrySeparator = timezonePreferenceOrOptions.countrySeparator ?? '|';
    stripNetwork = timezonePreferenceOrOptions.stripNetworkPrefix ?? false;
    addChannelNumber = timezonePreferenceOrOptions.addChannelNumber ?? false;
    numberSeparator = timezonePreferenceOrOptions.numberSeparator ?? '|';
    prefixOrder = timezonePreferenceOrOptions.prefixOrder ?? 'number-first';
  } else {
    timezonePreference = timezonePreferenceOrOptions;
  }

  const created: Channel[] = [];
  const errors: string[] = [];
  // Cache logos to avoid repeated lookups for the same URL
  const logoCache = new Map<string, Logo>();

  // Filter streams based on timezone preference first
  const filteredStreams = filterStreamsByTimezone(streams, timezonePreference);

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
          errors.push(`Channel "${channelName}" created but stream assignment failed for stream ${stream.id}: ${streamError}`);
        }
      }

      // Use the first stream's logo if available (sorted streams, so highest quality first)
      const logoUrl = sortedStreams.find((s: { logo_url?: string | null }) => s.logo_url)?.logo_url;
      if (logoUrl) {
        try {
          const logo = await getOrCreateLogo(channelName, logoUrl, logoCache);
          await updateChannel(channel.id, { logo_id: logo.id });
          created.push({ ...channel, streams: addedStreamIds, logo_id: logo.id });
        } catch (logoError) {
          // Logo assignment failed, but channel was still created
          errors.push(`Channel "${channelName}" created but logo assignment failed: ${logoError}`);
          created.push({ ...channel, streams: addedStreamIds });
        }
      } else {
        created.push({ ...channel, streams: addedStreamIds });
      }
    } catch (error) {
      errors.push(`Failed to create channel "${channelName}": ${error}`);
    }
  }

  return { created, errors, mergedCount };
}
