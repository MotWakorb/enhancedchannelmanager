/**
 * EPG Matching Utilities
 *
 * Provides intelligent matching between channels and EPG data entries,
 * with country-aware filtering based on stream names and groups.
 */

import type { Channel, Stream, EPGData, EPGSource } from '../types';
import { getCountryPrefix, stripCountryPrefix } from '../services/api';

// Quality suffixes to strip when normalizing names
const QUALITY_SUFFIXES = [
  'FHD', 'UHD', '4K', 'HD', 'SD',
  '1080P', '1080I', '720P', '480P', '2160P',
  'HEVC', 'H264', 'H265',
];

// Timezone/regional suffixes to strip
const TIMEZONE_SUFFIXES = ['EAST', 'WEST', 'ET', 'PT', 'CT', 'MT'];

/**
 * Result of EPG matching for a single channel
 */
export interface EPGMatchResult {
  channel: Channel;
  detectedCountry: string | null;
  normalizedName: string;
  matches: EPGData[];
  status: 'exact' | 'multiple' | 'none';
}

/**
 * Assignment to be made after user confirms
 */
export interface EPGAssignment {
  channelId: number;
  channelName: string;
  tvg_id: string | null;
  epg_data_id: number | null;
}

/**
 * Detect country code from a channel's associated streams.
 * Tries stream name first, then falls back to channel_group_name.
 *
 * @param streams - Array of streams to check
 * @returns Lowercase country code (e.g., "us") or null
 */
export function detectCountryFromStreams(streams: Stream[]): string | null {
  if (streams.length === 0) return null;

  // Try first stream's name (e.g., "US: ESPN" -> "US")
  for (const stream of streams) {
    const nameCountry = getCountryPrefix(stream.name);
    if (nameCountry) {
      return nameCountry.toLowerCase();
    }
  }

  // Fallback to channel_group_name (e.g., "US: Sports" -> "US")
  for (const stream of streams) {
    if (stream.channel_group_name) {
      const groupCountry = getCountryPrefix(stream.channel_group_name);
      if (groupCountry) {
        return groupCountry.toLowerCase();
      }
    }
  }

  return null;
}

/**
 * Normalize a channel/EPG name for matching purposes.
 * Strips channel number prefix, country prefix, quality suffixes, timezone suffixes,
 * and normalizes to lowercase alphanumeric only.
 *
 * @param name - Channel or EPG name to normalize
 * @returns Normalized name (lowercase, alphanumeric only)
 */
export function normalizeForEPGMatch(name: string): string {
  let normalized = name.trim();

  // Strip channel number prefix - multiple approaches to handle various formats
  // Pattern 1: "107 | Channel", "107 - Channel", "107: Channel", "107. Channel"
  normalized = normalized.replace(/^\d+(?:\.\d+)?\s*[|\-:.]\s*/, '');
  // Pattern 2: "107 Channel" (number followed by space and letter)
  normalized = normalized.replace(/^\d+(?:\.\d+)?\s+(?=[A-Za-z])/, '');

  // After stripping non-alphanumeric, we may have "5033CW" - strip leading digits
  // We do this after the initial attempts to preserve any meaningful numeric prefixes
  // that might be part of the actual name (rare but possible)

  // Strip country prefix
  normalized = stripCountryPrefix(normalized);

  // Strip quality suffixes
  for (const suffix of QUALITY_SUFFIXES) {
    const pattern = new RegExp(`[\\s\\-_|:]*${suffix}\\s*$`, 'i');
    normalized = normalized.replace(pattern, '');
  }

  // Strip timezone suffixes
  for (const suffix of TIMEZONE_SUFFIXES) {
    const pattern = new RegExp(`[\\s\\-_|:]*${suffix}\\s*$`, 'i');
    normalized = normalized.replace(pattern, '');
  }

  // Normalize to lowercase alphanumeric only
  normalized = normalized.toLowerCase().replace(/[^a-z0-9]/g, '');

  // Final pass: strip leading digits that may remain after normalization
  // This catches cases like "5033 | CW" where separator char wasn't matched
  normalized = normalized.replace(/^\d+/, '');

  return normalized;
}

/**
 * Parse a TVG-ID into its name and country components.
 * TVG-IDs typically follow the format: "ChannelName.country" or "ChannelName(variant).country"
 *
 * @param tvgId - The TVG-ID to parse (e.g., "ESPN.us", "BBCNews(America).us")
 * @returns Tuple of [normalizedName, countryCode] where countryCode may be null
 */
export function parseTvgId(tvgId: string): [string, string | null] {
  const lowerTvgId = tvgId.toLowerCase();
  const lastDot = lowerTvgId.lastIndexOf('.');

  if (lastDot === -1) {
    // No dot, so no country suffix
    return [normalizeForEPGMatch(tvgId), null];
  }

  const suffix = lowerTvgId.slice(lastDot + 1);

  // Check if suffix looks like a country code (2-3 lowercase letters)
  if (suffix.length >= 2 && suffix.length <= 3 && /^[a-z]+$/.test(suffix)) {
    const namepart = tvgId.slice(0, lastDot);
    return [normalizeForEPGMatch(namepart), suffix];
  }

  // Suffix doesn't look like a country code
  return [normalizeForEPGMatch(tvgId), null];
}

/**
 * Pre-processed EPG lookup structure for fast matching.
 * Built once per batch operation, then used for O(1) lookups.
 */
interface EPGLookup {
  // Map from normalized name -> array of EPG entries
  byNormalizedTvgId: Map<string, EPGData[]>;
  byNormalizedName: Map<string, EPGData[]>;
  byCallSign: Map<string, EPGData[]>;
  // Pre-parsed country codes for sorting
  countryByEpgId: Map<number, string | null>;
}

/**
 * Build lookup maps from EPG data for fast matching.
 * This is O(n) where n = EPG entries, done once per batch.
 */
function buildEPGLookup(epgData: EPGData[]): EPGLookup {
  const byNormalizedTvgId = new Map<string, EPGData[]>();
  const byNormalizedName = new Map<string, EPGData[]>();
  const byCallSign = new Map<string, EPGData[]>();
  const countryByEpgId = new Map<number, string | null>();

  for (const epg of epgData) {
    // Parse and normalize TVG-ID
    const [epgNormalizedTvgId, country] = parseTvgId(epg.tvg_id);
    countryByEpgId.set(epg.id, country);

    // Add to TVG-ID lookup
    if (epgNormalizedTvgId) {
      const existing = byNormalizedTvgId.get(epgNormalizedTvgId) || [];
      existing.push(epg);
      byNormalizedTvgId.set(epgNormalizedTvgId, existing);
    }

    // Add to name lookup
    const epgNormalizedName = normalizeForEPGMatch(epg.name);
    if (epgNormalizedName) {
      const existing = byNormalizedName.get(epgNormalizedName) || [];
      existing.push(epg);
      byNormalizedName.set(epgNormalizedName, existing);
    }

    // Extract and add call sign if present
    const callSignMatch = epg.tvg_id.match(/\(([^)]+)\)/);
    if (callSignMatch) {
      const callSign = callSignMatch[1].toLowerCase().replace(/[^a-z0-9]/g, '');
      if (callSign) {
        const existing = byCallSign.get(callSign) || [];
        existing.push(epg);
        byCallSign.set(callSign, existing);
      }
    }
  }

  return { byNormalizedTvgId, byNormalizedName, byCallSign, countryByEpgId };
}

/**
 * Find EPG matches for a channel using pre-built lookup maps.
 * This is O(1) per channel instead of O(n) where n = EPG entries.
 */
function findEPGMatchesWithLookup(
  channel: Channel,
  channelStreams: Stream[],
  lookup: EPGLookup
): EPGMatchResult {
  // Detect country from streams
  const detectedCountry = detectCountryFromStreams(channelStreams);

  // Normalize the channel name
  const normalizedName = normalizeForEPGMatch(channel.name);

  if (!normalizedName) {
    return {
      channel,
      detectedCountry,
      normalizedName,
      matches: [],
      status: 'none',
    };
  }

  // Collect matches from all lookup maps (using Set to dedupe by EPG id)
  const matchSet = new Map<number, EPGData>();

  // Check TVG-ID matches
  const tvgIdMatches = lookup.byNormalizedTvgId.get(normalizedName) || [];
  for (const epg of tvgIdMatches) {
    matchSet.set(epg.id, epg);
  }

  // Check name matches
  const nameMatches = lookup.byNormalizedName.get(normalizedName) || [];
  for (const epg of nameMatches) {
    matchSet.set(epg.id, epg);
  }

  // Check call sign matches
  const callSignMatches = lookup.byCallSign.get(normalizedName) || [];
  for (const epg of callSignMatches) {
    matchSet.set(epg.id, epg);
  }

  // Convert to array and sort
  const matchArray = Array.from(matchSet.values());

  // Sort matches to put the matching country first (if we detected one)
  const matches = matchArray.sort((a, b) => {
    const aCountry = lookup.countryByEpgId.get(a.id);
    const bCountry = lookup.countryByEpgId.get(b.id);

    // Matching country goes first
    if (detectedCountry) {
      if (aCountry === detectedCountry && bCountry !== detectedCountry) return -1;
      if (bCountry === detectedCountry && aCountry !== detectedCountry) return 1;
    }

    // Then sort by name
    return a.name.localeCompare(b.name);
  });

  // Determine status
  let status: 'exact' | 'multiple' | 'none';
  if (matches.length === 0) {
    status = 'none';
  } else if (matches.length === 1) {
    status = 'exact';
  } else {
    status = 'multiple';
  }

  return {
    channel,
    detectedCountry,
    normalizedName,
    matches,
    status,
  };
}

/**
 * Find EPG matches for a channel based on name similarity and country filtering.
 * NOTE: For batch operations, use batchFindEPGMatches instead for better performance.
 *
 * @param channel - The channel to find matches for
 * @param channelStreams - Streams associated with this channel
 * @param epgData - All available EPG data entries
 * @returns Match result with categorized matches
 */
export function findEPGMatches(
  channel: Channel,
  channelStreams: Stream[],
  epgData: EPGData[]
): EPGMatchResult {
  // For single channel, build lookup and use it
  const lookup = buildEPGLookup(epgData);
  return findEPGMatchesWithLookup(channel, channelStreams, lookup);
}

/**
 * Progress callback for batch matching
 */
export interface BatchMatchProgress {
  current: number;
  total: number;
  channelName: string;
}

/**
 * Process multiple channels for EPG matching.
 * Uses pre-built lookup maps for O(n + m) performance instead of O(n * m)
 * where n = channels and m = EPG entries.
 *
 * @param channels - Channels to match
 * @param allStreams - All available streams
 * @param epgData - All available EPG data
 * @param onProgress - Optional callback for progress updates
 * @returns Array of match results
 */
export function batchFindEPGMatches(
  channels: Channel[],
  allStreams: Stream[],
  epgData: EPGData[],
  onProgress?: (progress: BatchMatchProgress) => void
): EPGMatchResult[] {
  console.log('[EPGMatching] Building lookup maps...');
  const startLookup = performance.now();

  // Build lookup maps ONCE for all EPG data
  const epgLookup = buildEPGLookup(epgData);

  // Create a lookup map for streams by ID
  const streamMap = new Map(allStreams.map(s => [s.id, s]));

  console.log(`[EPGMatching] Lookup maps built in ${(performance.now() - startLookup).toFixed(0)}ms`);
  console.log('[EPGMatching] Matching channels...');
  const startMatch = performance.now();

  const results: EPGMatchResult[] = [];
  const total = channels.length;

  for (let i = 0; i < channels.length; i++) {
    const channel = channels[i];

    // Report progress
    if (onProgress) {
      onProgress({ current: i + 1, total, channelName: channel.name });
    }

    // Get streams associated with this channel
    const channelStreams = channel.streams
      .map(id => streamMap.get(id))
      .filter((s): s is Stream => s !== undefined);

    results.push(findEPGMatchesWithLookup(channel, channelStreams, epgLookup));
  }

  console.log(`[EPGMatching] Matched ${channels.length} channels in ${(performance.now() - startMatch).toFixed(0)}ms`);

  return results;
}

/**
 * Process multiple channels for EPG matching with async progress updates.
 * Yields control periodically to allow UI updates.
 */
export async function batchFindEPGMatchesAsync(
  channels: Channel[],
  allStreams: Stream[],
  epgData: EPGData[],
  onProgress?: (progress: BatchMatchProgress) => void
): Promise<EPGMatchResult[]> {
  console.log('[EPGMatching] Building lookup maps...');
  const startLookup = performance.now();

  // Build lookup maps ONCE for all EPG data
  const epgLookup = buildEPGLookup(epgData);

  // Create a lookup map for streams by ID
  const streamMap = new Map(allStreams.map(s => [s.id, s]));

  console.log(`[EPGMatching] Lookup maps built in ${(performance.now() - startLookup).toFixed(0)}ms`);
  console.log('[EPGMatching] Matching channels...');
  const startMatch = performance.now();

  const results: EPGMatchResult[] = [];
  const total = channels.length;
  const BATCH_SIZE = 10; // Process 10 channels before yielding

  for (let i = 0; i < channels.length; i++) {
    const channel = channels[i];

    // Report progress
    if (onProgress) {
      onProgress({ current: i + 1, total, channelName: channel.name });
    }

    // Get streams associated with this channel
    const channelStreams = channel.streams
      .map(id => streamMap.get(id))
      .filter((s): s is Stream => s !== undefined);

    results.push(findEPGMatchesWithLookup(channel, channelStreams, epgLookup));

    // Yield control every BATCH_SIZE channels to allow UI updates
    if ((i + 1) % BATCH_SIZE === 0) {
      await new Promise(resolve => setTimeout(resolve, 0));
    }
  }

  console.log(`[EPGMatching] Matched ${channels.length} channels in ${(performance.now() - startMatch).toFixed(0)}ms`);

  return results;
}

/**
 * Get the EPG source name for an EPG data entry.
 *
 * @param epgData - The EPG data entry
 * @param epgSources - All EPG sources
 * @returns Source name or "Unknown"
 */
export function getEPGSourceName(
  epgData: EPGData,
  epgSources: EPGSource[]
): string {
  const source = epgSources.find(s => s.id === epgData.epg_source);
  return source?.name || 'Unknown';
}
