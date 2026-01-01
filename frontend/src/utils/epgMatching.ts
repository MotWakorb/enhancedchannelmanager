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
 * Strips country prefix, quality suffixes, timezone suffixes,
 * and normalizes to lowercase alphanumeric only.
 *
 * @param name - Channel or EPG name to normalize
 * @returns Normalized name (lowercase, alphanumeric only)
 */
export function normalizeForEPGMatch(name: string): string {
  let normalized = name.trim();

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
 * Find EPG matches for a channel based on name similarity and country filtering.
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

  // Find matching EPG entries
  const exactCountryMatches: EPGData[] = [];
  const exactNoCountryMatches: EPGData[] = [];
  const partialMatches: EPGData[] = [];

  for (const epg of epgData) {
    const [epgNormalizedName, epgCountry] = parseTvgId(epg.tvg_id);

    // Check if names match
    const namesMatch = normalizedName === epgNormalizedName;
    const nameContains = epgNormalizedName.includes(normalizedName) ||
                         normalizedName.includes(epgNormalizedName);

    if (namesMatch) {
      // Exact name match
      if (detectedCountry && epgCountry === detectedCountry) {
        // Exact match with matching country - highest priority
        exactCountryMatches.push(epg);
      } else if (!detectedCountry || !epgCountry) {
        // Exact match but no country info on one or both sides
        exactNoCountryMatches.push(epg);
      } else {
        // Exact name match but different country - still include as option
        partialMatches.push(epg);
      }
    } else if (nameContains && (!detectedCountry || !epgCountry || epgCountry === detectedCountry)) {
      // Partial name match with compatible country
      partialMatches.push(epg);
    }
  }

  // Combine matches with priority: exact+country > exact+noCountry > partial
  let matches: EPGData[] = [];

  if (exactCountryMatches.length > 0) {
    // We have exact matches with the right country
    matches = exactCountryMatches;
  } else if (exactNoCountryMatches.length > 0) {
    // Exact name matches without country filtering
    matches = exactNoCountryMatches;
  } else if (partialMatches.length > 0) {
    // Fall back to partial matches
    matches = partialMatches;
  }

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
 * Process multiple channels for EPG matching.
 *
 * @param channels - Channels to match
 * @param allStreams - All available streams
 * @param epgData - All available EPG data
 * @returns Array of match results
 */
export function batchFindEPGMatches(
  channels: Channel[],
  allStreams: Stream[],
  epgData: EPGData[]
): EPGMatchResult[] {
  // Create a lookup map for streams by ID
  const streamMap = new Map(allStreams.map(s => [s.id, s]));

  return channels.map(channel => {
    // Get streams associated with this channel
    const channelStreams = channel.streams
      .map(id => streamMap.get(id))
      .filter((s): s is Stream => s !== undefined);

    return findEPGMatches(channel, channelStreams, epgData);
  });
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
