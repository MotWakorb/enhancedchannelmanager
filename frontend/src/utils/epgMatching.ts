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

  // Convert special characters with semantic meaning before stripping
  // This ensures "AMC+" matches "AMCPlus" or "AMCPLUS"
  normalized = normalized.replace(/\+/g, 'plus');
  normalized = normalized.replace(/&/g, 'and');

  // Normalize to lowercase alphanumeric only
  normalized = normalized.toLowerCase().replace(/[^a-z0-9]/g, '');

  // Final pass: strip leading digits that may remain after normalization
  // This catches cases like "5033 | CW" where separator char wasn't matched
  normalized = normalized.replace(/^\d+/, '');

  // Strip leading article prefixes (the, a, an) only if remaining text is long enough
  // This allows "Bob Ross Channel" to match "TheBobRossChannel"
  // but prevents "AWE" from being reduced to "WE"
  const articleMatch = normalized.match(/^(the|a|an)([a-z]+)$/);
  if (articleMatch && articleMatch[2].length >= 4) {
    normalized = articleMatch[2];
  }

  return normalized;
}

/**
 * Parse a TVG-ID into its name and country components.
 * TVG-IDs typically follow the format: "ChannelName.country" or "ChannelName(variant).country"
 * Call signs in parentheses are stripped to get the base channel name.
 *
 * @param tvgId - The TVG-ID to parse (e.g., "ESPN.us", "BBCNews(America).us", "AdultSwim(ADSM).ca")
 * @returns Tuple of [normalizedName, countryCode] where countryCode may be null
 */
export function parseTvgId(tvgId: string): [string, string | null] {
  const lowerTvgId = tvgId.toLowerCase();
  const lastDot = lowerTvgId.lastIndexOf('.');

  let nameToNormalize = tvgId;
  let country: string | null = null;

  if (lastDot !== -1) {
    const suffix = lowerTvgId.slice(lastDot + 1);

    // Check if suffix looks like a country code (2-3 lowercase letters)
    if (suffix.length >= 2 && suffix.length <= 3 && /^[a-z]+$/.test(suffix)) {
      nameToNormalize = tvgId.slice(0, lastDot);
      country = suffix;
    }
  }

  // Strip call signs in parentheses before normalizing
  // This converts "AdultSwim(ADSM)" or "AdultSwim(IPFeed)(ASIP)" to "AdultSwim"
  nameToNormalize = nameToNormalize.replace(/\([^)]+\)/g, '');

  return [normalizeForEPGMatch(nameToNormalize), country];
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
  // Normalized TVG-ID name for each EPG entry (for sorting by name similarity)
  normalizedTvgIdByEpgId: Map<number, string>;
  // For prefix matching: list of all normalized TVG-IDs with their EPG entries
  allNormalizedTvgIds: Array<{ normalized: string; epg: EPGData }>;
  // For prefix matching: list of all normalized names with their EPG entries
  allNormalizedNames: Array<{ normalized: string; epg: EPGData }>;
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
  const normalizedTvgIdByEpgId = new Map<number, string>();
  const allNormalizedTvgIds: Array<{ normalized: string; epg: EPGData }> = [];
  const allNormalizedNames: Array<{ normalized: string; epg: EPGData }> = [];

  for (const epg of epgData) {
    // Parse and normalize TVG-ID
    const [epgNormalizedTvgId, country] = parseTvgId(epg.tvg_id);
    countryByEpgId.set(epg.id, country);
    normalizedTvgIdByEpgId.set(epg.id, epgNormalizedTvgId);

    // Add to TVG-ID lookup
    if (epgNormalizedTvgId) {
      const existing = byNormalizedTvgId.get(epgNormalizedTvgId) || [];
      existing.push(epg);
      byNormalizedTvgId.set(epgNormalizedTvgId, existing);
      // Also add to prefix matching list
      allNormalizedTvgIds.push({ normalized: epgNormalizedTvgId, epg });
    }

    // Add to name lookup
    const epgNormalizedName = normalizeForEPGMatch(epg.name);
    if (epgNormalizedName) {
      const existing = byNormalizedName.get(epgNormalizedName) || [];
      existing.push(epg);
      byNormalizedName.set(epgNormalizedName, existing);
      // Also add to prefix matching list
      allNormalizedNames.push({ normalized: epgNormalizedName, epg });
    }

    // Extract and add call sign if present
    const callSignMatch = epg.tvg_id.match(/\(([^)]+)\)/);
    if (callSignMatch) {
      const callSign = callSignMatch[1].toLowerCase().replace(/[^a-z0-9]/g, '');
      if (callSign) {
        const existing = byCallSign.get(callSign) || [];
        existing.push(epg);
        byCallSign.set(callSign, existing);

        // Also index call sign without common suffixes (HD, SD, FHD, UHD)
        // This allows "AWE" to match "AWEHD" call sign
        const suffixPattern = /(hd|sd|fhd|uhd)$/;
        if (suffixPattern.test(callSign)) {
          const baseCallSign = callSign.replace(suffixPattern, '');
          if (baseCallSign && baseCallSign !== callSign) {
            const baseExisting = byCallSign.get(baseCallSign) || [];
            baseExisting.push(epg);
            byCallSign.set(baseCallSign, baseExisting);
          }
        }
      }
    }
  }

  return {
    byNormalizedTvgId,
    byNormalizedName,
    byCallSign,
    countryByEpgId,
    normalizedTvgIdByEpgId,
    allNormalizedTvgIds,
    allNormalizedNames,
  };
}

// Minimum length for prefix matching to avoid too many false positives
const MIN_PREFIX_LENGTH = 4;

/**
 * Find EPG matches for a channel using pre-built lookup maps.
 * Uses exact matching first, then falls back to prefix/fuzzy matching.
 */
function findEPGMatchesWithLookup(
  channel: Channel,
  channelStreams: Stream[],
  lookup: EPGLookup
): EPGMatchResult {
  // Detect country from streams or channel name
  let detectedCountry = detectCountryFromStreams(channelStreams);

  // Also try to detect country from channel name if not found in streams
  if (!detectedCountry) {
    const channelNameCountry = channel.name.match(/^([A-Z]{2})\s*[|:]/);
    if (channelNameCountry) {
      detectedCountry = channelNameCountry[1].toLowerCase();
    }
  }

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

  // Track match quality for sorting: exact matches are better than prefix matches
  const matchQuality = new Map<number, 'exact' | 'prefix'>();

  // Collect matches from all lookup maps (using Map to dedupe by EPG id)
  const matchSet = new Map<number, EPGData>();

  // Check exact TVG-ID matches
  const tvgIdMatches = lookup.byNormalizedTvgId.get(normalizedName) || [];
  for (const epg of tvgIdMatches) {
    matchSet.set(epg.id, epg);
    matchQuality.set(epg.id, 'exact');
  }

  // Check exact name matches
  const nameMatches = lookup.byNormalizedName.get(normalizedName) || [];
  for (const epg of nameMatches) {
    matchSet.set(epg.id, epg);
    matchQuality.set(epg.id, 'exact');
  }

  // Check call sign matches
  const callSignMatches = lookup.byCallSign.get(normalizedName) || [];
  for (const epg of callSignMatches) {
    matchSet.set(epg.id, epg);
    matchQuality.set(epg.id, 'exact');
  }

  // Try prefix matching if:
  // 1. No exact matches found and name is long enough, OR
  // 2. Name is very short (1-2 chars) - single letters are too generic, need more context
  const shouldTryPrefixMatching =
    (matchSet.size === 0 && normalizedName.length >= MIN_PREFIX_LENGTH) ||
    (normalizedName.length <= 2);

  if (shouldTryPrefixMatching) {
    // Check if channel name is a prefix of any EPG TVG-ID
    for (const { normalized, epg } of lookup.allNormalizedTvgIds) {
      // Channel name starts with EPG name, or EPG name starts with channel name
      if (normalized.startsWith(normalizedName) || normalizedName.startsWith(normalized)) {
        if (!matchSet.has(epg.id)) {
          matchSet.set(epg.id, epg);
          matchQuality.set(epg.id, 'prefix');
        }
      }
    }

    // Also check prefix matches in EPG names
    for (const { normalized, epg } of lookup.allNormalizedNames) {
      if (normalized.startsWith(normalizedName) || normalizedName.startsWith(normalized)) {
        if (!matchSet.has(epg.id)) {
          matchSet.set(epg.id, epg);
          matchQuality.set(epg.id, 'prefix');
        }
      }
    }
  }

  // Convert to array and sort
  const matchArray = Array.from(matchSet.values());

  // Extract special punctuation from original channel name for matching
  // This helps "E!" prefer "E!Entertainment" over just "E"
  const channelSpecialChars = channel.name.match(/[!@#$%^*]/g) || [];

  // Sort matches with priority:
  // 1. Exact matches over prefix matches (but not for very short names)
  // 2. Matching special punctuation (e.g., "!" in channel name matches "!" in EPG)
  // 3. Matching country over non-matching country
  // 4. Name similarity (prefer EPG names closer in length to channel name)
  // 5. Regional preference: match channel's region hint, or default to East
  // 6. Call sign match quality (NOSEY > VIZNOSE for "Nosey", REELZHD > VIZREEL for "Reelz")
  // 7. HD variants over non-HD (prefer higher quality)
  // 8. Alphabetically by name
  const matches = matchArray.sort((a, b) => {
    const aQuality = matchQuality.get(a.id) || 'prefix';
    const bQuality = matchQuality.get(b.id) || 'prefix';
    const aCountry = lookup.countryByEpgId.get(a.id);
    const bCountry = lookup.countryByEpgId.get(b.id);

    // For short names (1-2 chars), don't prioritize exact matches over prefix
    // because single letters are too generic
    if (normalizedName.length > 2) {
      // Exact matches first
      if (aQuality === 'exact' && bQuality !== 'exact') return -1;
      if (bQuality === 'exact' && aQuality !== 'exact') return 1;
    }

    // Prefer EPG entries that share special punctuation with channel name
    // This helps "E!" match "E!Entertainment" over "E"
    if (channelSpecialChars.length > 0) {
      const aHasSpecialChar = channelSpecialChars.some(char => a.tvg_id.includes(char) || a.name.includes(char));
      const bHasSpecialChar = channelSpecialChars.some(char => b.tvg_id.includes(char) || b.name.includes(char));
      if (aHasSpecialChar && !bHasSpecialChar) return -1;
      if (bHasSpecialChar && !aHasSpecialChar) return 1;
    }

    // Then matching country
    if (detectedCountry) {
      if (aCountry === detectedCountry && bCountry !== detectedCountry) return -1;
      if (bCountry === detectedCountry && aCountry !== detectedCountry) return 1;
    }

    // Prefer matches where channel name is a prefix of EPG name (not the reverse)
    // "ebonytv" matching "ebonytvbylionsgate" is better than "e" matching "ebonytv"
    const aNormalized = lookup.normalizedTvgIdByEpgId.get(a.id) || '';
    const bNormalized = lookup.normalizedTvgIdByEpgId.get(b.id) || '';
    const aChannelIsPrefix = aNormalized.startsWith(normalizedName);
    const bChannelIsPrefix = bNormalized.startsWith(normalizedName);
    // Prefer matches where channel name is prefix of EPG name
    if (aChannelIsPrefix && !bChannelIsPrefix) return -1;
    if (bChannelIsPrefix && !aChannelIsPrefix) return 1;
    // Among same type of matches, prefer EPG names closer in length to channel name
    const aLengthDiff = Math.abs(aNormalized.length - normalizedName.length);
    const bLengthDiff = Math.abs(bNormalized.length - normalizedName.length);
    if (aLengthDiff !== bLengthDiff) {
      return aLengthDiff - bLengthDiff; // Smaller difference = better match
    }

    // Handle regional variants (Pacific, East, West, etc.) in TVG-ID
    // If channel name contains a region hint, prefer matching region
    // Otherwise, deprioritize regional variants
    // Pattern matches: (Pacific), (East), (West), (WestCoast), (WestCoastFeed), (EastFeed), etc.
    const regionalPattern = /\((Pacific|East(?:ern)?(?:Feed)?|West(?:ern)?(?:Coast)?(?:Feed)?|Central|Mountain)\)/i;
    const aRegionalMatch = a.tvg_id.match(regionalPattern);
    const bRegionalMatch = b.tvg_id.match(regionalPattern);
    const aIsRegional = !!aRegionalMatch;
    const bIsRegional = !!bRegionalMatch;

    // Check if channel name hints at a region (e.g., "FYI West" or "FYI East")
    const channelWantsWest = /\bwest\b/i.test(channel.name) || /\bpacific\b/i.test(channel.name);
    const channelWantsEast = /\beast\b/i.test(channel.name);
    const channelWantsCentral = /\bcentral\b/i.test(channel.name);
    const channelWantsMountain = /\bmountain\b/i.test(channel.name);

    // Determine which region to prefer
    // If channel name specifies a region, use that; otherwise default to East
    const wantsWest = channelWantsWest;
    const wantsEast = channelWantsEast || (!channelWantsWest && !channelWantsCentral && !channelWantsMountain);
    const wantsCentral = channelWantsCentral;
    const wantsMountain = channelWantsMountain;

    // Check if each EPG entry matches the wanted region
    // West includes: Pacific, West, WestCoast, WestCoastFeed, Western, WesternFeed
    // East includes: East, Eastern, EastFeed, EasternFeed
    const aMatchesWanted =
      (wantsWest && aRegionalMatch && /pacific|west/i.test(aRegionalMatch[1])) ||
      (wantsEast && aRegionalMatch && /^east/i.test(aRegionalMatch[1])) ||
      (wantsCentral && aRegionalMatch && /central/i.test(aRegionalMatch[1])) ||
      (wantsMountain && aRegionalMatch && /mountain/i.test(aRegionalMatch[1]));
    const bMatchesWanted =
      (wantsWest && bRegionalMatch && /pacific|west/i.test(bRegionalMatch[1])) ||
      (wantsEast && bRegionalMatch && /^east/i.test(bRegionalMatch[1])) ||
      (wantsCentral && bRegionalMatch && /central/i.test(bRegionalMatch[1])) ||
      (wantsMountain && bRegionalMatch && /mountain/i.test(bRegionalMatch[1]));

    // Prefer matching regional variant, then non-regional, then other regional
    if (aMatchesWanted && !bMatchesWanted) return -1;
    if (bMatchesWanted && !aMatchesWanted) return 1;
    // If neither matches wanted region, prefer non-regional over wrong regional
    if (!aMatchesWanted && !bMatchesWanted) {
      if (!aIsRegional && bIsRegional) return -1;
      if (aIsRegional && !bIsRegional) return 1;
    }

    // Prefer EPG entries where call sign better matches the channel name
    // This is important because call signs like NOSEY, REELZHD, PETCOLL contain
    // the channel name, while VIZNOSE, VIZREEL, XALPTCL don't
    const aCallSignMatch = a.tvg_id.match(/\(([^)]+)\)/);
    const bCallSignMatch = b.tvg_id.match(/\(([^)]+)\)/);

    // Score call sign match quality:
    // 3 = exact match (call sign equals normalized name)
    // 2 = base match (call sign minus HD/SD suffix equals normalized name)
    // 1 = partial match (call sign starts with normalized name, or normalized name starts with call sign base)
    // 0 = no meaningful match
    const scoreCallSign = (callSignRaw: string | null): number => {
      if (!callSignRaw) return 0;
      const callSign = callSignRaw.toLowerCase().replace(/[^a-z0-9]/g, '');
      const callSignBase = callSign.replace(/(hd|sd|fhd|uhd)$/, '');

      // Exact match
      if (callSign === normalizedName || callSignBase === normalizedName) return 3;
      // Call sign starts with channel name (e.g., REELZHD starts with REELZ for "Reelz")
      if (callSign.startsWith(normalizedName) || callSignBase.startsWith(normalizedName)) return 2;
      // Channel name starts with call sign base (e.g., "tastemadetravel" starts with "tmtravel" - no wait, that's not right)
      // Actually check if they share a common prefix of significant length
      // This catches cases like PURSTHD for "pursuit" (both start with "purs")
      if (normalizedName.length >= 4 && callSignBase.length >= 4) {
        // Find longest common prefix
        let commonLen = 0;
        const minLen = Math.min(normalizedName.length, callSignBase.length);
        for (let i = 0; i < minLen; i++) {
          if (normalizedName[i] === callSignBase[i]) {
            commonLen++;
          } else {
            break;
          }
        }
        // If common prefix is at least 60% of the shorter string, it's a partial match
        if (commonLen >= Math.ceil(Math.min(normalizedName.length, callSignBase.length) * 0.6)) {
          return 1;
        }
      }
      return 0;
    };

    const aCallSignScore = scoreCallSign(aCallSignMatch?.[1] ?? null);
    const bCallSignScore = scoreCallSign(bCallSignMatch?.[1] ?? null);

    if (aCallSignScore !== bCallSignScore) {
      return bCallSignScore - aCallSignScore; // Higher score = better match
    }

    // Prefer HD variants over non-HD (check TVG-ID for HD suffix in call sign)
    const aHasHD = /hd\)?\./i.test(a.tvg_id) || /HD$/i.test(a.name);
    const bHasHD = /hd\)?\./i.test(b.tvg_id) || /HD$/i.test(b.name);
    if (aHasHD && !bHasHD) return -1;
    if (bHasHD && !aHasHD) return 1;

    // Then alphabetically by name
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
  // Build lookup maps ONCE for all EPG data
  const epgLookup = buildEPGLookup(epgData);

  // Create a lookup map for streams by ID
  const streamMap = new Map(allStreams.map(s => [s.id, s]));

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
  // Build lookup maps ONCE for all EPG data
  const epgLookup = buildEPGLookup(epgData);

  // Create a lookup map for streams by ID
  const streamMap = new Map(allStreams.map(s => [s.id, s]));

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
