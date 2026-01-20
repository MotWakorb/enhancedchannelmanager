/**
 * Stream Normalization Utilities
 *
 * Functions for normalizing, parsing, and filtering stream names.
 * Extracted from api.ts for better separation of concerns.
 */

import {
  QUALITY_SUFFIXES,
  NETWORK_PREFIXES,
  NETWORK_SUFFIXES,
  QUALITY_PRIORITY,
  DEFAULT_QUALITY_PRIORITY,
  COUNTRY_PREFIXES,
  TIMEZONE_SUFFIXES,
  LEAGUE_PREFIXES,
  TAG_GROUPS,
  TagGroupName,
} from '../constants/streamNormalization';
import { NormalizationSettings, NormalizationTag, NormalizationTagMode } from './api';

// Separator types for channel number prefix and country prefix
export type NumberSeparator = '-' | ':' | '|';

/**
 * Strip quality/resolution suffixes from a name.
 * Handles both named suffixes (FHD, UHD, 4K, HD, SD) and arbitrary resolutions (1080p, 720p, 476p, etc.)
 */
function stripQualitySuffixes(name: string): string {
  let result = name;

  // First strip named quality suffixes from the constant list
  for (const suffix of QUALITY_SUFFIXES) {
    const pattern = new RegExp(`[\\s\\-_|:]*${suffix}\\s*$`, 'i');
    result = result.replace(pattern, '');
  }

  // Then strip any arbitrary resolution pattern (e.g., 476p, 540p, 1440p)
  // Match number followed by 'p' or 'i' at end of string with optional separator before
  result = result.replace(/[\s\-_|:]*\d+[pPiI]\s*$/, '');

  return result.trim();
}

// Prefix order when both country and number are enabled
export type PrefixOrder = 'number-first' | 'country-first';

// Timezone preference type
export type TimezonePreference = 'east' | 'west' | 'both';

// Options for normalizing stream names
export interface NormalizeOptions {
  timezonePreference?: TimezonePreference;
  stripCountryPrefix?: boolean;
  keepCountryPrefix?: boolean;       // Keep and normalize country prefix format
  countrySeparator?: NumberSeparator; // Separator to use when keeping country prefix
  stripNetworkPrefix?: boolean;      // Strip network prefixes like "CHAMP |", "PPV |" etc.
  customNetworkPrefixes?: string[];  // Additional user-defined prefixes to strip
  stripNetworkSuffix?: boolean;      // Strip network suffixes like "(ENGLISH)", "[LIVE]", "BACKUP" etc.
  customNetworkSuffixes?: string[];  // Additional user-defined suffixes to strip
  normalizationSettings?: NormalizationSettings;  // Tag-based normalization settings
}

/**
 * Get the quality priority score for a stream name.
 * Lower score = higher quality (should appear first in the list).
 * Streams without quality indicators get DEFAULT_QUALITY_PRIORITY (HD level).
 *
 * Handles:
 * - Named quality indicators: 4K, UHD, FHD, HD, SD
 * - Any resolution ending in 'p' or 'i': 2160p, 1440p, 1080p, 720p, 576p, 540p, 480p, 476p, etc.
 * - Higher resolution numbers = higher quality = lower priority value
 */
export function getStreamQualityPriority(streamName: string): number {
  const upperName = streamName.toUpperCase();

  // First check for named quality indicators (4K, UHD, FHD, HD, SD)
  // These take precedence over numeric resolution parsing
  for (const [quality, priority] of Object.entries(QUALITY_PRIORITY)) {
    // Skip numeric resolutions in the map - we'll handle those dynamically
    if (/^\d+[PI]$/.test(quality)) continue;

    // Match quality at word boundary or with common separators
    const pattern = new RegExp(`(?:^|[\\s\\-_|:])${quality}(?:$|[\\s\\-_|:])`, 'i');
    if (pattern.test(upperName)) {
      return priority;
    }
  }

  // Look for any resolution pattern ending in 'p' or 'i' (e.g., 1080p, 720p, 476p, 1080i)
  // Match at word boundary or with common separators
  const resolutionMatch = upperName.match(/(?:^|[\s\-_|:])(\d+)[PI](?:$|[\s\-_|:])/);
  if (resolutionMatch) {
    const resolution = parseInt(resolutionMatch[1], 10);
    if (resolution > 0) {
      // Calculate priority: higher resolution = lower priority value (sorts first)
      // Formula: ~10 for 2160p, ~20 for 1080p, ~30 for 720p, ~40 for 480p
      // Using 20000/resolution gives good spread across common resolutions
      const calculatedPriority = Math.round(20000 / resolution);

      // Clamp to reasonable range (5-60) to avoid extreme values
      return Math.max(5, Math.min(60, calculatedPriority));
    }
  }

  return DEFAULT_QUALITY_PRIORITY;
}

/**
 * Sort streams by quality priority (highest quality first).
 * Within each quality tier, alternates between providers for failover redundancy.
 *
 * Example with 4 streams:
 * - "US: ESPN FHD" on Provider 1
 * - "US: ESPN" on Provider 1
 * - "US: ESPN FHD" on Provider 2
 * - "US: ESPN" on Provider 2
 *
 * Result order:
 * 1. Provider 1 "US: ESPN FHD" (FHD tier, provider 1)
 * 2. Provider 2 "US: ESPN FHD" (FHD tier, provider 2)
 * 3. Provider 1 "US: ESPN" (HD tier, provider 1)
 * 4. Provider 2 "US: ESPN" (HD tier, provider 2)
 */
export function sortStreamsByQuality<T extends { name: string; m3u_account?: number | null }>(streams: T[]): T[] {
  // Group streams by quality tier
  const qualityGroups = new Map<number, T[]>();

  for (const stream of streams) {
    const priority = getStreamQualityPriority(stream.name);
    if (!qualityGroups.has(priority)) {
      qualityGroups.set(priority, []);
    }
    qualityGroups.get(priority)!.push(stream);
  }

  // Sort quality tiers (lowest priority number = highest quality = first)
  const sortedPriorities = [...qualityGroups.keys()].sort((a, b) => a - b);

  const result: T[] = [];

  for (const priority of sortedPriorities) {
    const tierStreams = qualityGroups.get(priority)!;

    // Group by provider within this quality tier
    const providerGroups = new Map<number | null, T[]>();
    for (const stream of tierStreams) {
      const providerId = stream.m3u_account ?? null;
      if (!providerGroups.has(providerId)) {
        providerGroups.set(providerId, []);
      }
      providerGroups.get(providerId)!.push(stream);
    }

    // Sort provider IDs to ensure consistent ordering
    const sortedProviderIds = [...providerGroups.keys()].sort((a, b) => {
      if (a === null) return 1;
      if (b === null) return -1;
      return a - b;
    });

    // Interleave streams from different providers (round-robin)
    // This ensures failover: Provider1-FHD, Provider2-FHD, Provider3-FHD, etc.
    const providerIterators = sortedProviderIds.map(id => ({
      id,
      streams: providerGroups.get(id)!,
      index: 0
    }));

    let hasMore = true;
    while (hasMore) {
      hasMore = false;
      for (const iter of providerIterators) {
        if (iter.index < iter.streams.length) {
          result.push(iter.streams[iter.index]);
          iter.index++;
          hasMore = true;
        }
      }
    }
  }

  return result;
}

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
 *
 * @param name - The stream name to process
 * @param customPrefixes - Optional additional prefixes to check (merged with built-in list)
 */
export function stripNetworkPrefix(name: string, customPrefixes?: string[]): string {
  const trimmedName = name.trim();

  // Merge built-in prefixes with custom prefixes (if provided)
  const allPrefixes = customPrefixes && customPrefixes.length > 0
    ? [...NETWORK_PREFIXES, ...customPrefixes]
    : NETWORK_PREFIXES;

  // Sort prefixes by length (longest first) to match more specific ones first
  const sortedPrefixes = [...allPrefixes].sort((a, b) => b.length - a.length);

  for (const prefix of sortedPrefixes) {
    // Pattern: prefix at start, followed by separator (|, :, -, /)
    // Capture everything after the separator
    const pattern = new RegExp(`^${prefix}\\s*[|:\\-/]\\s*(.+)$`, 'i');
    const match = trimmedName.match(pattern);
    if (match) {
      const content = match[1].trim();
      // Only strip if the trimmed content is at least 3 characters
      // This avoids stripping when content is too short (e.g., "PPV | AB" stays unchanged)
      if (content.length >= 3) {
        return content;
      }
    }
  }

  return trimmedName;
}

/**
 * Detect if a stream name has a network prefix that can be stripped.
 */
export function hasNetworkPrefix(name: string, customPrefixes?: string[]): boolean {
  return stripNetworkPrefix(name, customPrefixes) !== name.trim();
}

/**
 * Detect if a list of streams has network prefixes.
 */
export function detectNetworkPrefixes(streams: { name: string }[], customPrefixes?: string[]): boolean {
  for (const stream of streams) {
    if (hasNetworkPrefix(stream.name, customPrefixes)) {
      return true;
    }
  }
  return false;
}

/**
 * Strip network suffix from a stream name if present.
 * Network suffixes are things like "(ENGLISH)", "[LIVE]", "BACKUP" that follow content names.
 * Handles parentheses, brackets, and bare suffixes.
 *
 * Examples:
 * - "ESPN (ENGLISH)" → "ESPN"
 * - "Sky Sports [LIVE]" → "Sky Sports"
 * - "HBO BACKUP" → "HBO"
 * - "CNN Feed" → "CNN"
 *
 * @param name - The stream name to process
 * @param customSuffixes - Optional additional suffixes to check (merged with built-in list)
 */
export function stripNetworkSuffix(name: string, customSuffixes?: string[]): string {
  let result = name.trim();

  // Merge built-in suffixes with custom suffixes (if provided)
  const allSuffixes = customSuffixes && customSuffixes.length > 0
    ? [...NETWORK_SUFFIXES, ...customSuffixes]
    : NETWORK_SUFFIXES;

  // Sort suffixes by length (longest first) to match more specific ones first
  const sortedSuffixes = [...allSuffixes].sort((a, b) => b.length - a.length);

  for (const suffix of sortedSuffixes) {
    // Pattern 1: Suffix in parentheses at end - e.g., "ESPN (ENGLISH)"
    const parenPattern = new RegExp(`\\s*\\(\\s*${suffix}\\s*\\)\\s*$`, 'i');
    if (parenPattern.test(result)) {
      result = result.replace(parenPattern, '').trim();
      continue;
    }

    // Pattern 2: Suffix in brackets at end - e.g., "ESPN [LIVE]"
    const bracketPattern = new RegExp(`\\s*\\[\\s*${suffix}\\s*\\]\\s*$`, 'i');
    if (bracketPattern.test(result)) {
      result = result.replace(bracketPattern, '').trim();
      continue;
    }

    // Pattern 3: Bare suffix at end with separator - e.g., "ESPN - ENGLISH", "ESPN | BACKUP"
    // The content before must be at least 3 characters (to avoid stripping too much)
    const bareSepPattern = new RegExp(`^(.{3,})[\\s\\-|:]+${suffix}\\s*$`, 'i');
    const bareSepMatch = result.match(bareSepPattern);
    if (bareSepMatch) {
      result = bareSepMatch[1].trim();
      continue;
    }

    // Pattern 4: Bare suffix at end with just space - e.g., "ESPN BACKUP"
    // Must have word boundary before suffix
    const bareSpacePattern = new RegExp(`^(.{3,})\\s+${suffix}\\s*$`, 'i');
    const bareSpaceMatch = result.match(bareSpacePattern);
    if (bareSpaceMatch) {
      result = bareSpaceMatch[1].trim();
      continue;
    }
  }

  return result;
}

/**
 * Detect if a stream name has a network suffix that can be stripped.
 */
export function hasNetworkSuffix(name: string, customSuffixes?: string[]): boolean {
  return stripNetworkSuffix(name, customSuffixes) !== name.trim();
}

/**
 * Detect if a list of streams has network suffixes.
 */
export function detectNetworkSuffixes(streams: { name: string }[], customSuffixes?: string[]): boolean {
  for (const stream of streams) {
    if (hasNetworkSuffix(stream.name, customSuffixes)) {
      return true;
    }
  }
  return false;
}

/**
 * Detect if a stream name has a country prefix.
 * Returns the country code if found, null otherwise.
 */
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

/**
 * Strip country prefix and any trailing punctuation from a name.
 */
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

/**
 * Detect if a list of streams has country prefixes.
 */
export function detectCountryPrefixes(streams: { name: string }[]): boolean {
  for (const stream of streams) {
    if (getCountryPrefix(stream.name) !== null) {
      return true;
    }
  }
  return false;
}

/**
 * Get all unique country prefixes found in a list of streams.
 */
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

/**
 * Check if a stream name has a regional suffix (East or West).
 */
export function getRegionalSuffix(name: string): 'east' | 'west' | null {
  // Check for East/West at the end with optional separator
  if (/[\s\-_|:]+EAST\s*$/i.test(name)) return 'east';
  if (/[\s\-_|:]+WEST\s*$/i.test(name)) return 'west';
  return null;
}

/**
 * Strip regional suffix from a name (private helper).
 */
function stripRegionalSuffix(name: string): string {
  return name.replace(/[\s\-_|:]+(?:EAST|WEST)\s*$/i, '').trim();
}

/**
 * Detect if a list of streams has regional variants (both East and West versions, or base + West).
 */
export function detectRegionalVariants(streams: { name: string }[]): boolean {
  // Build a set of base names (without regional suffix) and track which variants exist
  const baseNames = new Map<string, Set<'east' | 'west' | 'none'>>();

  for (const stream of streams) {
    // First strip quality suffixes to get consistent base comparison
    let nameWithoutQuality = stripQualitySuffixes(stream.name);
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

/**
 * Get enabled tags for a specific group based on normalization settings.
 * If no settings provided, returns all tags enabled.
 */
function getEnabledTagsForGroup(
  group: TagGroupName,
  normalizationSettings?: NormalizationSettings
): string[] {
  const groupTags = TAG_GROUPS[group].tags;

  if (!normalizationSettings) {
    return groupTags;
  }

  // Filter out disabled tags
  return groupTags.filter((tag) => {
    const key = `${group}:${tag}`;
    return !normalizationSettings.disabledBuiltinTags.includes(key);
  });
}

/**
 * Strip a tag from a name based on its mode (prefix, suffix, or both).
 * Returns the modified name.
 */
function stripTagByMode(name: string, tag: string, mode: NormalizationTagMode): string {
  let result = name;

  if (mode === 'prefix' || mode === 'both') {
    // Try to strip as prefix with separator
    const prefixPattern = new RegExp(`^${tag}\\s*[|:\\-/]+\\s*`, 'i');
    result = result.replace(prefixPattern, '');
  }

  if (mode === 'suffix' || mode === 'both') {
    // Try to strip as suffix (with various patterns)
    // Pattern 1: In parentheses
    const parenPattern = new RegExp(`\\s*\\(\\s*${tag}\\s*\\)\\s*$`, 'i');
    result = result.replace(parenPattern, '');
    // Pattern 2: In brackets
    const bracketPattern = new RegExp(`\\s*\\[\\s*${tag}\\s*\\]\\s*$`, 'i');
    result = result.replace(bracketPattern, '');
    // Pattern 3: With separator
    const sepPattern = new RegExp(`[\\s\\-|:]+${tag}\\s*$`, 'i');
    result = result.replace(sepPattern, '');
    // Pattern 4: Just space
    const spacePattern = new RegExp(`\\s+${tag}\\s*$`, 'i');
    result = result.replace(spacePattern, '');
  }

  return result.trim();
}

/**
 * Apply custom tags from normalization settings.
 */
function applyCustomTags(name: string, customTags: NormalizationTag[]): string {
  let result = name;
  for (const tag of customTags) {
    result = stripTagByMode(result, tag.value, tag.mode);
  }
  return result;
}

/**
 * Normalize a stream name for matching purposes.
 * Strips quality suffixes and normalizes whitespace.
 * timezonePreference controls how regional variants are handled:
 * - 'both': keep East/West as separate channels (don't merge regional variants)
 * - 'east': prefer East timezone - merge West into base name, treat non-suffixed as East
 * - 'west': prefer West timezone - merge East/non-suffixed into base, keep West
 * stripCountryPrefix: if true, removes country prefix (e.g., "US: Sports Channel" -> "Sports Channel")
 * keepCountryPrefix: if true, keeps country prefix but normalizes format (e.g., "US: Sports Channel" -> "US | Sports Channel")
 */
export function normalizeStreamName(name: string, timezonePreferenceOrOptions: TimezonePreference | NormalizeOptions = 'both'): string {
  // Handle both old signature (just TimezonePreference) and new signature (NormalizeOptions)
  let timezonePreference: TimezonePreference = 'both';
  let stripCountry = false;
  let keepCountry = false;
  let countrySeparator: NumberSeparator = '|';
  let stripNetwork = false;
  let customNetworkPrefixes: string[] | undefined;
  let stripSuffix = false;
  let customNetworkSuffixes: string[] | undefined;
  let normalizationSettings: NormalizationSettings | undefined;

  if (typeof timezonePreferenceOrOptions === 'object') {
    timezonePreference = timezonePreferenceOrOptions.timezonePreference ?? 'both';
    stripCountry = timezonePreferenceOrOptions.stripCountryPrefix ?? false;
    keepCountry = timezonePreferenceOrOptions.keepCountryPrefix ?? false;
    countrySeparator = timezonePreferenceOrOptions.countrySeparator ?? '|';
    stripNetwork = timezonePreferenceOrOptions.stripNetworkPrefix ?? false;
    customNetworkPrefixes = timezonePreferenceOrOptions.customNetworkPrefixes;
    stripSuffix = timezonePreferenceOrOptions.stripNetworkSuffix ?? false;
    customNetworkSuffixes = timezonePreferenceOrOptions.customNetworkSuffixes;
    normalizationSettings = timezonePreferenceOrOptions.normalizationSettings;
  } else {
    timezonePreference = timezonePreferenceOrOptions;
  }

  // If normalizationSettings provided, use tag-based filtering
  // Otherwise fall back to the stripNetwork/stripSuffix boolean flags
  const useTagBasedNormalization = !!normalizationSettings;

  let normalized = name.trim();

  // Tag-based normalization (new system) vs. legacy boolean flags
  if (useTagBasedNormalization && normalizationSettings) {
    // Apply tag-based normalization using enabled tags from each group

    // 1. Strip league prefixes (e.g., "NFL: Cardinals" → "Cardinals")
    const enabledLeagueTags = getEnabledTagsForGroup('league', normalizationSettings);
    for (const tag of enabledLeagueTags) {
      normalized = stripTagByMode(normalized, tag, 'prefix');
    }

    // 2. Strip network prefixes and suffixes
    const enabledNetworkTags = getEnabledTagsForGroup('network', normalizationSettings);
    for (const tag of enabledNetworkTags) {
      // Network tags can be both prefix and suffix
      normalized = stripTagByMode(normalized, tag, 'both');
    }

    // 3. Strip country prefixes (e.g., "US: ESPN" → "ESPN")
    if (stripCountry && !keepCountry) {
      const enabledCountryTags = getEnabledTagsForGroup('country', normalizationSettings);
      for (const tag of enabledCountryTags) {
        normalized = stripTagByMode(normalized, tag, 'prefix');
      }
    } else if (keepCountry) {
      // Keep the country prefix but normalize its format
      const countryCode = getCountryPrefix(normalized);
      if (countryCode) {
        const nameWithoutPrefix = stripCountryPrefix(normalized);
        normalized = `${countryCode} ${countrySeparator} ${nameWithoutPrefix}`;
      }
    }

    // 4. Strip quality suffixes (e.g., "ESPN HD" → "ESPN")
    const enabledQualityTags = getEnabledTagsForGroup('quality', normalizationSettings);
    for (const tag of enabledQualityTags) {
      normalized = stripTagByMode(normalized, tag, 'suffix');
    }
    // Also strip numeric resolution patterns (e.g., 1080p, 720p)
    normalized = normalized.replace(/[\s\-_|:]*\d+[pPiI]\s*$/, '').trim();

    // 5. Strip timezone suffixes (e.g., "ESPN EAST" → "ESPN")
    if (timezonePreference !== 'both') {
      const enabledTimezoneTags = getEnabledTagsForGroup('timezone', normalizationSettings);
      for (const tag of enabledTimezoneTags) {
        normalized = stripTagByMode(normalized, tag, 'suffix');
      }
    }

    // 6. Apply custom tags
    if (normalizationSettings.customTags.length > 0) {
      normalized = applyCustomTags(normalized, normalizationSettings.customTags);
    }
  } else {
    // Legacy boolean-based normalization

    // Strip network prefix first (before country prefix, as network prefix may come before country)
    // e.g., "CHAMP | US: Queens Park Rangers" → "US: Queens Park Rangers" → then handle country
    if (stripNetwork) {
      normalized = stripNetworkPrefix(normalized, customNetworkPrefixes);
    }

    // Strip network suffix (after prefix, before country handling)
    // e.g., "ESPN (ENGLISH)" → "ESPN"
    if (stripSuffix) {
      normalized = stripNetworkSuffix(normalized, customNetworkSuffixes);
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

    // Strip quality/resolution suffixes (FHD, UHD, 4K, HD, SD, and any numeric resolution like 1080p, 720p, 476p)
    normalized = stripQualitySuffixes(normalized);

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
  }

  // Normalize multiple spaces to single space and trim
  normalized = normalized.replace(/\s+/g, ' ').trim();

  // Normalize separator spacing: ensure consistent spacing around common separators
  // This ensures "PL| ID" and "PL | ID" are treated as the same channel
  normalized = normalized.replace(/\s*\|\s*/g, ' | ');
  normalized = normalized.replace(/\s*:\s*/g, ': ');
  normalized = normalized.replace(/\s*-\s*/g, ' - ');
  // Re-trim in case we added leading/trailing spaces
  normalized = normalized.trim();

  // If normalization resulted in empty string, fall back to original name
  // This can happen when a channel name matches both a country code (e.g., "ID" for Indonesia)
  // and a quality suffix (e.g., "ID FHD" -> strip "ID " as country -> "FHD" -> strip as quality -> "")
  if (!normalized) {
    return name.trim();
  }

  return normalized;
}

/**
 * Filter streams based on timezone preference.
 * - 'east': include streams without suffix OR with East suffix, exclude West
 * - 'west': include streams with West suffix, exclude East and non-suffixed
 * - 'both': include all streams
 */
export function filterStreamsByTimezone<T extends { name: string }>(
  streams: T[],
  timezonePreference: TimezonePreference
): T[] {
  if (timezonePreference === 'both') {
    return streams;
  }

  return streams.filter((stream) => {
    // First normalize quality to check regional suffix properly
    const nameWithoutQuality = stripQualitySuffixes(stream.name);

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
