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

// Broadcast call sign patterns for US/Canada local TV stations
// US call signs: K or W followed by 2-4 letters, optionally with -DT, -TV, -HD suffix
// Examples: KATU, KOIN, KGW, WKOW, WHA-DT, KPTV-DT, WMTV
const BROADCAST_CALL_SIGN_PATTERN = /\b([KW][A-Z]{2,4})(?:[-]?(?:DT|TV|HD|LP|CD|CA|LD))?\b/i;

/**
 * EPG match with confidence score
 */
export interface EPGMatchWithScore {
  epg: EPGData;
  confidence: number; // 0-100 confidence score
}

/**
 * Result of EPG matching for a single channel
 */
export interface EPGMatchResult {
  channel: Channel;
  detectedCountry: string | null;
  normalizedName: string;
  matches: EPGData[];
  matchesWithScores: EPGMatchWithScore[]; // Matches with confidence scores
  bestScore: number; // Highest confidence score among matches
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
 * Extract broadcast call sign from a channel name.
 * US/Canada broadcast stations use call signs starting with K (west of Mississippi)
 * or W (east of Mississippi), followed by 2-4 letters, optionally with -DT/-TV/-HD suffix.
 *
 * Examples:
 *   "21.1 | PBS: WHA-DT Madison" -> "wha"
 *   "2.2 | ABC: KATU Portland" -> "katu"
 *   "6.1 | CBS: KOIN Portland" -> "koin"
 *   "ESPN" -> null (not a broadcast call sign)
 *
 * @param name - Channel name to extract call sign from
 * @returns Lowercase call sign (without suffix) or null if none found
 */
export function extractBroadcastCallSign(name: string): string | null {
  const match = name.match(BROADCAST_CALL_SIGN_PATTERN);
  if (match) {
    // Return just the base call sign (e.g., "WHA" from "WHA-DT"), normalized to lowercase
    return match[1].toLowerCase();
  }
  return null;
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

    // Extract and add call sign if present in parentheses (e.g., "AdultSwim(ADSM)")
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

    // Also extract broadcast call signs from EPG tvg_id and name
    // This handles local TV stations like "KATU Portland" or TVG-IDs like "KATU.us"
    const epgBroadcastCallSign = extractBroadcastCallSign(epg.tvg_id) || extractBroadcastCallSign(epg.name);
    if (epgBroadcastCallSign) {
      const existing = byCallSign.get(epgBroadcastCallSign) || [];
      // Only add if not already in the array (avoid duplicates)
      if (!existing.some(e => e.id === epg.id)) {
        existing.push(epg);
        byCallSign.set(epgBroadcastCallSign, existing);
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

  // Default to US if no country detected - this ensures US EPG entries
  // are preferred over international ones when no explicit country is set
  if (!detectedCountry) {
    detectedCountry = 'us';
  }

  // Normalize the channel name
  const normalizedName = normalizeForEPGMatch(channel.name);

  if (!normalizedName) {
    return {
      channel,
      detectedCountry,
      normalizedName,
      matches: [],
      matchesWithScores: [],
      bestScore: 0,
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

  // Check call sign matches using normalized name
  const callSignMatches = lookup.byCallSign.get(normalizedName) || [];
  for (const epg of callSignMatches) {
    matchSet.set(epg.id, epg);
    matchQuality.set(epg.id, 'exact');
  }

  // Extract broadcast call sign from channel name (e.g., "KATU" from "2.2 | ABC: KATU Portland")
  // and look up EPG entries that have matching call signs
  const broadcastCallSign = extractBroadcastCallSign(channel.name);
  if (broadcastCallSign) {
    const broadcastCallSignMatches = lookup.byCallSign.get(broadcastCallSign) || [];
    for (const epg of broadcastCallSignMatches) {
      matchSet.set(epg.id, epg);
      matchQuality.set(epg.id, 'exact');
    }
  }

  // Always try prefix matching to find additional matches
  // This ensures we find entries like "DiscoveryChannel.us" even if there's
  // an exact match for "Discovery.be" - country sorting will then prefer the right one
  // Only skip prefix matching for very short names (1-2 chars) that are too generic
  if (normalizedName.length >= MIN_PREFIX_LENGTH) {
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
  // 1. Matching country over non-matching country (highest priority)
  // 2. Exact matches over prefix matches (but not for very short names)
  // 3. Matching special punctuation (e.g., "!" in channel name matches "!" in EPG)
  // 4. Name similarity (prefer EPG names closer in length to channel name)
  // 5. Regional preference: match channel's region hint, or default to East
  // 6. HD + call sign combined: prefer HD unless non-HD has much better call sign
  //    (e.g., TVLandHD beats TVLand, but Nosey(NOSEY) beats Nosey(VIZNOSE)-HD)
  // 7. Alphabetically by name
  const matches = matchArray.sort((a, b) => {
    const aQuality = matchQuality.get(a.id) || 'prefix';
    const bQuality = matchQuality.get(b.id) || 'prefix';
    const aCountry = lookup.countryByEpgId.get(a.id);
    const bCountry = lookup.countryByEpgId.get(b.id);

    // FIRST: Country matching - this is the highest priority
    // A US channel should prefer US EPG entries over international ones
    if (detectedCountry) {
      if (aCountry === detectedCountry && bCountry !== detectedCountry) return -1;
      if (bCountry === detectedCountry && aCountry !== detectedCountry) return 1;
    }

    // For short names (1-2 chars), don't prioritize exact matches over prefix
    // because single letters are too generic
    if (normalizedName.length > 2) {
      // Exact matches first (within same country)
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

    // Combined HD + call sign scoring
    // We want HD variants, but also want call signs that match the channel name.
    // Strategy: Prefer HD variants unless the non-HD has a MUCH better call sign match
    // (e.g., VIZNOSE vs NOSEY for "Nosey" - NOSEY is clearly better and worth preferring non-HD)
    const aCallSignMatch = a.tvg_id.match(/\(([^)]+)\)/);
    const bCallSignMatch = b.tvg_id.match(/\(([^)]+)\)/);
    const aHasHD = /hd\)?\./i.test(a.tvg_id) || /HD$/i.test(a.name);
    const bHasHD = /hd\)?\./i.test(b.tvg_id) || /HD$/i.test(b.name);

    // Score call sign match quality:
    // 3 = exact match (call sign equals normalized name)
    // 2 = call sign starts with channel name (e.g., REELZHD starts with REELZ for "Reelz")
    // 1 = partial match (significant common prefix)
    // 0 = no meaningful match
    const scoreCallSign = (callSignRaw: string | null): number => {
      if (!callSignRaw) return 0;
      const callSign = callSignRaw.toLowerCase().replace(/[^a-z0-9]/g, '');
      const callSignBase = callSign.replace(/(hd|sd|fhd|uhd)$/, '');

      // Exact match (after stripping HD suffix)
      if (callSign === normalizedName || callSignBase === normalizedName) return 3;
      // Call sign starts with channel name (e.g., REELZHD starts with REELZ for "Reelz")
      if (callSign.startsWith(normalizedName) || callSignBase.startsWith(normalizedName)) return 2;
      // Check for significant common prefix
      if (normalizedName.length >= 4 && callSignBase.length >= 4) {
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

    // If one is HD and one isn't, we need to balance HD preference with call sign quality
    if (aHasHD !== bHasHD) {
      const hdScore = aHasHD ? aCallSignScore : bCallSignScore;
      const nonHdScore = aHasHD ? bCallSignScore : aCallSignScore;

      // Only prefer non-HD if it has a MUCH better call sign (score difference >= 2)
      // AND the HD variant has a poor call sign (score <= 1)
      // This means: NOSEY (score 3) beats VIZNOSE-HD (score 0) -- diff is 3, HD score is 0
      // But: TVLAND (score 3) loses to TVLNDHD (score 2) -- diff is only 1
      // And: FYI (score 3) loses to FYIHD (score 3) -- same score, HD wins
      if (nonHdScore >= hdScore + 2 && hdScore <= 1) {
        // Non-HD has much better call sign and HD has poor call sign
        return aHasHD ? 1 : -1; // Prefer non-HD
      }
      // Otherwise prefer HD
      return aHasHD ? -1 : 1;
    }

    // Both are HD or both are non-HD: prefer better call sign match
    if (aCallSignScore !== bCallSignScore) {
      return bCallSignScore - aCallSignScore; // Higher score = better match
    }

    // Then alphabetically by name
    return a.name.localeCompare(b.name);
  });

  // Calculate confidence scores for each match
  // Scoring factors (total 100 points):
  // - Country match: 40 points (most important)
  // - Exact vs prefix match: 25 points
  // - Name length similarity: 20 points (closer length = higher score)
  // - Call sign match: 10 points
  // - HD variant: 5 points
  const matchesWithScores: EPGMatchWithScore[] = matches.map(epg => {
    let confidence = 0;
    const epgCountry = lookup.countryByEpgId.get(epg.id);
    const epgNormalized = lookup.normalizedTvgIdByEpgId.get(epg.id) || '';
    const quality = matchQuality.get(epg.id) || 'prefix';

    // Country match: 40 points
    if (detectedCountry && epgCountry === detectedCountry) {
      confidence += 40;
    } else if (!epgCountry && detectedCountry === 'us') {
      // No country in EPG, but we're looking for US - give partial credit
      confidence += 20;
    }

    // Exact vs prefix match: 25 points
    if (quality === 'exact') {
      confidence += 25;
    } else {
      // Prefix match - score based on how close the match is
      const isChannelPrefix = epgNormalized.startsWith(normalizedName);
      const isEpgPrefix = normalizedName.startsWith(epgNormalized);
      if (isChannelPrefix && isEpgPrefix) {
        confidence += 25; // Both are prefixes of each other = exact
      } else if (isChannelPrefix) {
        confidence += 20; // Channel name is prefix of EPG name
      } else if (isEpgPrefix) {
        confidence += 15; // EPG name is prefix of channel name
      }
    }

    // Name length similarity: 20 points
    const lengthDiff = Math.abs(epgNormalized.length - normalizedName.length);
    const maxLength = Math.max(epgNormalized.length, normalizedName.length, 1);
    const lengthSimilarity = 1 - (lengthDiff / maxLength);
    confidence += Math.round(lengthSimilarity * 20);

    // Call sign match: 10 points
    const callSignMatch = epg.tvg_id.match(/\(([^)]+)\)/);
    if (callSignMatch) {
      const callSign = callSignMatch[1].toLowerCase().replace(/[^a-z0-9]/g, '');
      const callSignBase = callSign.replace(/(hd|sd|fhd|uhd)$/, '');
      if (callSign === normalizedName || callSignBase === normalizedName) {
        confidence += 10; // Exact call sign match
      } else if (callSign.startsWith(normalizedName) || callSignBase.startsWith(normalizedName)) {
        confidence += 7; // Call sign starts with channel name
      } else if (normalizedName.startsWith(callSignBase)) {
        confidence += 5; // Channel name starts with call sign
      }
    }

    // HD variant: 5 points
    const hasHD = /hd\)?\./i.test(epg.tvg_id) || /HD$/i.test(epg.name);
    if (hasHD) {
      confidence += 5;
    }

    // Cap at 100
    confidence = Math.min(100, confidence);

    return { epg, confidence };
  });

  // Sort by confidence score (highest first), keeping the original sort order as tiebreaker
  matchesWithScores.sort((a, b) => b.confidence - a.confidence);

  // Get the best score
  const bestScore = matchesWithScores.length > 0 ? matchesWithScores[0].confidence : 0;

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
    matchesWithScores,
    bestScore,
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
 *
 * @param channels - Channels to match
 * @param allStreams - All available streams
 * @param epgData - All available EPG data (should already be filtered by selected sources)
 * @param onProgress - Optional callback for progress updates
 * @param sourceOrder - Optional array of source IDs in priority order (first = highest priority)
 *                      When provided, matches from higher-priority sources are preferred
 * @returns Array of match results
 */
export async function batchFindEPGMatchesAsync(
  channels: Channel[],
  allStreams: Stream[],
  epgData: EPGData[],
  onProgress?: (progress: BatchMatchProgress) => void,
  sourceOrder?: number[]
): Promise<EPGMatchResult[]> {
  // Build lookup maps ONCE for all EPG data
  const epgLookup = buildEPGLookup(epgData);

  // Create a lookup map for streams by ID
  const streamMap = new Map(allStreams.map(s => [s.id, s]));

  // Create source priority map for sorting (lower index = higher priority)
  const sourcePriorityMap = new Map<number, number>();
  if (sourceOrder) {
    sourceOrder.forEach((sourceId, index) => {
      sourcePriorityMap.set(sourceId, index);
    });
  }

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

    const result = findEPGMatchesWithLookup(channel, channelStreams, epgLookup);

    // If we have source priority order, re-sort matches by source priority
    if (sourceOrder && sourceOrder.length > 0 && result.matches.length > 1) {
      result.matches.sort((a, b) => {
        const aPriority = sourcePriorityMap.get(a.epg_source) ?? 999;
        const bPriority = sourcePriorityMap.get(b.epg_source) ?? 999;
        // Lower priority number = higher priority (comes first)
        return aPriority - bPriority;
      });

      // If the top match is from the highest priority source, and there's only one match
      // from that source, treat it as an exact match
      const topSourceId = result.matches[0].epg_source;
      const topPriority = sourcePriorityMap.get(topSourceId) ?? 999;
      const matchesFromTopSource = result.matches.filter(
        m => (sourcePriorityMap.get(m.epg_source) ?? 999) === topPriority
      );
      if (matchesFromTopSource.length === 1 && result.status === 'multiple') {
        // Single match from highest priority source - treat as exact
        result.matches = matchesFromTopSource;
        result.status = 'exact';
      }
    }

    results.push(result);

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
