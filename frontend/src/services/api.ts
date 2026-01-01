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
}): Promise<PaginatedResponse<Stream>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);
  if (params?.channelGroup) searchParams.set('channel_group_name', params.channelGroup);
  if (params?.m3uAccount) searchParams.set('m3u_account', String(params.m3uAccount));

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/streams${query ? `?${query}` : ''}`);
}

export async function getStreamGroups(): Promise<string[]> {
  return fetchJson(`${API_BASE}/stream-groups`);
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
export interface SettingsResponse {
  url: string;
  username: string;
  configured: boolean;
  auto_rename_channel_number: boolean;
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

export type TimezonePreference = 'east' | 'west' | 'both';

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
}

// Normalize a stream name for matching purposes
// Strips quality suffixes and normalizes whitespace
// timezonePreference controls how regional variants are handled:
// - 'both': keep East/West as separate channels (don't merge regional variants)
// - 'east': prefer East timezone - merge West into base name, treat non-suffixed as East
// - 'west': prefer West timezone - merge East/non-suffixed into base, keep West
// stripCountryPrefix: if true, removes country prefix (e.g., "US: ESPN" -> "ESPN")
export function normalizeStreamName(name: string, timezonePreferenceOrOptions: TimezonePreference | NormalizeOptions = 'both'): string {
  // Handle both old signature (just TimezonePreference) and new signature (NormalizeOptions)
  let timezonePreference: TimezonePreference = 'both';
  let stripCountry = false;

  if (typeof timezonePreferenceOrOptions === 'object') {
    timezonePreference = timezonePreferenceOrOptions.timezonePreference ?? 'both';
    stripCountry = timezonePreferenceOrOptions.stripCountryPrefix ?? false;
  } else {
    timezonePreference = timezonePreferenceOrOptions;
  }

  let normalized = name.trim();

  // Strip country prefix if requested (do this first, before other normalization)
  if (stripCountry) {
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

// Separator types for channel number prefix
export type NumberSeparator = '-' | ':' | '|';

// Options for bulk channel creation
export interface BulkCreateOptions {
  timezonePreference?: TimezonePreference;
  stripCountryPrefix?: boolean;
  addChannelNumber?: boolean;
  numberSeparator?: NumberSeparator;
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
  let addChannelNumber = false;
  let numberSeparator: NumberSeparator = '|';

  if (typeof timezonePreferenceOrOptions === 'object') {
    timezonePreference = timezonePreferenceOrOptions.timezonePreference ?? 'both';
    stripCountry = timezonePreferenceOrOptions.stripCountryPrefix ?? false;
    addChannelNumber = timezonePreferenceOrOptions.addChannelNumber ?? false;
    numberSeparator = timezonePreferenceOrOptions.numberSeparator ?? '|';
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
    // Optionally prepend channel number with separator
    const channelName = addChannelNumber
      ? `${channelNumber} ${numberSeparator} ${normalizedName}`
      : normalizedName;

    try {
      // Create the channel
      const channel = await createChannel({
        name: channelName,
        channel_number: channelNumber,
        channel_group_id: channelGroupId ?? undefined,
      });

      // Add all streams with this normalized name to the channel (provides multi-provider/quality redundancy)
      const addedStreamIds: number[] = [];
      for (const stream of groupedStreams) {
        try {
          await addStreamToChannel(channel.id, stream.id);
          addedStreamIds.push(stream.id);
        } catch (streamError) {
          errors.push(`Channel "${channelName}" created but stream assignment failed for stream ${stream.id}: ${streamError}`);
        }
      }

      // Use the first stream's logo if available
      const logoUrl = groupedStreams.find((s: { logo_url?: string | null }) => s.logo_url)?.logo_url;
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
