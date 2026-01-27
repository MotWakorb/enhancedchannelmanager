/**
 * Stream Normalization Utilities
 *
 * Functions for normalizing, parsing, and filtering stream names.
 * The main normalization is now handled by the backend rules engine.
 * This file contains utility functions for quality detection, sorting, and prefix/suffix handling.
 */

import {
  QUALITY_SUFFIXES,
  NETWORK_PREFIXES,
  NETWORK_SUFFIXES,
  QUALITY_PRIORITY,
  DEFAULT_QUALITY_PRIORITY,
  COUNTRY_PREFIXES,
} from '../constants/streamNormalization';

/**
 * Map of Unicode superscript/subscript/special characters to their ASCII equivalents.
 * Covers modifier letters, subscripts, superscripts, and other common variants.
 */
const UNICODE_TO_ASCII_MAP: Record<string, string> = {
  // Superscript letters (Modifier Letter Capital)
  '\u1D2C': 'A', '\u1D2E': 'B', '\u1D30': 'D', '\u1D31': 'E', '\u1D33': 'G',
  '\u1D34': 'H', '\u1D35': 'I', '\u1D36': 'J', '\u1D37': 'K', '\u1D38': 'L',
  '\u1D39': 'M', '\u1D3A': 'N', '\u1D3C': 'O', '\u1D3E': 'P', '\u1D3F': 'R',
  '\u1D40': 'T', '\u1D41': 'U', '\u1D42': 'W',
  // Superscript letters (Modifier Letter Small)
  '\u1D43': 'a', '\u1D47': 'b', '\u1D48': 'd', '\u1D49': 'e', '\u1D4D': 'g',
  '\u02B0': 'h', '\u2071': 'i', '\u02B2': 'j', '\u1D4F': 'k', '\u02E1': 'l',
  '\u1D50': 'm', '\u207F': 'n', '\u1D52': 'o', '\u1D56': 'p', '\u02B3': 'r',
  '\u02E2': 's', '\u1D57': 't', '\u1D58': 'u', '\u1D5B': 'v', '\u02B7': 'w',
  '\u02E3': 'x', '\u02B8': 'y', '\u1DBB': 'z',
  // Common superscript characters
  '\u00B2': '2', '\u00B3': '3', '\u00B9': '1', '\u2070': '0', '\u2074': '4',
  '\u2075': '5', '\u2076': '6', '\u2077': '7', '\u2078': '8', '\u2079': '9',
  '\u207A': '+', '\u207B': '-', '\u207C': '=', '\u207D': '(', '\u207E': ')',
  // Subscript numbers
  '\u2080': '0', '\u2081': '1', '\u2082': '2', '\u2083': '3', '\u2084': '4',
  '\u2085': '5', '\u2086': '6', '\u2087': '7', '\u2088': '8', '\u2089': '9',
  '\u208A': '+', '\u208B': '-', '\u208C': '=', '\u208D': '(', '\u208E': ')',
  // Small capitals (often used stylistically)
  '\u1D00': 'A', '\u0299': 'B', '\u1D04': 'C', '\u1D05': 'D', '\u1D07': 'E',
  '\u0493': 'F', '\u0262': 'G', '\u029C': 'H', '\u026A': 'I', '\u1D0A': 'J',
  '\u1D0B': 'K', '\u029F': 'L', '\u1D0D': 'M', '\u0274': 'N', '\u1D0F': 'O',
  '\u1D18': 'P', '\u0280': 'R', '\u0455': 'S', '\u1D1B': 'T', '\u1D1C': 'U',
  '\u1D20': 'V', '\u1D21': 'W', '\u028F': 'Y', '\u1D22': 'Z',
  // Full-width letters (A-Z, a-z)
  '\uFF21': 'A', '\uFF22': 'B', '\uFF23': 'C', '\uFF24': 'D', '\uFF25': 'E',
  '\uFF26': 'F', '\uFF27': 'G', '\uFF28': 'H', '\uFF29': 'I', '\uFF2A': 'J',
  '\uFF2B': 'K', '\uFF2C': 'L', '\uFF2D': 'M', '\uFF2E': 'N', '\uFF2F': 'O',
  '\uFF30': 'P', '\uFF31': 'Q', '\uFF32': 'R', '\uFF33': 'S', '\uFF34': 'T',
  '\uFF35': 'U', '\uFF36': 'V', '\uFF37': 'W', '\uFF38': 'X', '\uFF39': 'Y',
  '\uFF3A': 'Z',
  '\uFF41': 'a', '\uFF42': 'b', '\uFF43': 'c', '\uFF44': 'd', '\uFF45': 'e',
  '\uFF46': 'f', '\uFF47': 'g', '\uFF48': 'h', '\uFF49': 'i', '\uFF4A': 'j',
  '\uFF4B': 'k', '\uFF4C': 'l', '\uFF4D': 'm', '\uFF4E': 'n', '\uFF4F': 'o',
  '\uFF50': 'p', '\uFF51': 'q', '\uFF52': 'r', '\uFF53': 's', '\uFF54': 't',
  '\uFF55': 'u', '\uFF56': 'v', '\uFF57': 'w', '\uFF58': 'x', '\uFF59': 'y',
  '\uFF5A': 'z',
  // Full-width numbers
  '\uFF10': '0', '\uFF11': '1', '\uFF12': '2', '\uFF13': '3', '\uFF14': '4',
  '\uFF15': '5', '\uFF16': '6', '\uFF17': '7', '\uFF18': '8', '\uFF19': '9',
};

/**
 * Normalize Unicode characters to their ASCII equivalents.
 * Converts superscript, subscript, small caps, and full-width characters to standard ASCII.
 * This allows quality suffixes like "ᵁᴴᴰ" to be detected as "UHD".
 */
export function normalizeUnicodeToAscii(input: string): string {
  let result = '';
  for (const char of input) {
    result += UNICODE_TO_ASCII_MAP[char] ?? char;
  }
  return result;
}

/**
 * Strip leading separator characters (pipes, dashes, colons) from a string.
 * Handles patterns like "| UK | Channel Name" -> "UK | Channel Name"
 */
function stripLeadingSeparators(name: string): string {
  return name.replace(/^[\s|:\-/]+/, '');
}

// Separator types for channel number prefix and country prefix
export type NumberSeparator = '-' | ':' | '|';

/**
 * Strip quality/resolution suffixes from a name.
 * Handles both named suffixes (FHD, UHD, 4K, HD, SD) and arbitrary resolutions (1080p, 720p, 476p, etc.)
 * Used to group quality variants of the same channel together.
 */
export function stripQualitySuffixes(name: string): string {
  // Normalize Unicode chars first to strip superscript quality like "ᵁᴴᴰ"
  let result = normalizeUnicodeToAscii(name);

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

// Timezone preference type
export type TimezonePreference = 'east' | 'west' | 'both';

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
  // Normalize Unicode chars first to detect superscript quality like "ᵁᴴᴰ"
  const upperName = normalizeUnicodeToAscii(streamName).toUpperCase();

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
    const pattern = new RegExp(`^${prefix}\\s*[|:\\-/]\\s*(.+)$`, 'i');
    const match = trimmedName.match(pattern);
    if (match) {
      const content = match[1].trim();
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
    // Pattern 1: Suffix in parentheses at end
    const parenPattern = new RegExp(`\\s*\\(\\s*${suffix}\\s*\\)\\s*$`, 'i');
    if (parenPattern.test(result)) {
      result = result.replace(parenPattern, '').trim();
      continue;
    }

    // Pattern 2: Suffix in brackets at end
    const bracketPattern = new RegExp(`\\s*\\[\\s*${suffix}\\s*\\]\\s*$`, 'i');
    if (bracketPattern.test(result)) {
      result = result.replace(bracketPattern, '').trim();
      continue;
    }

    // Pattern 3: Bare suffix at end with separator
    const bareSepPattern = new RegExp(`^(.{3,})[\\s\\-|:]+${suffix}\\s*$`, 'i');
    const bareSepMatch = result.match(bareSepPattern);
    if (bareSepMatch) {
      result = bareSepMatch[1].trim();
      continue;
    }

    // Pattern 4: Bare suffix at end with just space
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
  // Strip leading separators to handle patterns like "| UK | Channel Name"
  const trimmedName = stripLeadingSeparators(name.trim());

  for (const prefix of COUNTRY_PREFIXES) {
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
  // Strip leading separators to handle patterns like "| UK | Channel Name"
  const trimmedName = stripLeadingSeparators(name.trim());

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
  if (/[\s\-_|:]+EAST\s*$/i.test(name)) return 'east';
  if (/[\s\-_|:]+WEST\s*$/i.test(name)) return 'west';
  return null;
}

/**
 * Strip regional suffix from a name.
 */
function stripRegionalSuffix(name: string): string {
  return name.replace(/[\s\-_|:]+(?:EAST|WEST)\s*$/i, '').trim();
}

/**
 * Detect if a list of streams has regional variants (both East and West versions, or base + West).
 */
export function detectRegionalVariants(streams: { name: string }[]): boolean {
  const baseNames = new Map<string, Set<'east' | 'west' | 'none'>>();

  for (const stream of streams) {
    let nameWithoutQuality = stripQualitySuffixes(stream.name);
    nameWithoutQuality = nameWithoutQuality.replace(/\s+/g, ' ').trim();

    const regional = getRegionalSuffix(nameWithoutQuality);
    const baseName = stripRegionalSuffix(nameWithoutQuality).toLowerCase();

    if (!baseNames.has(baseName)) {
      baseNames.set(baseName, new Set());
    }
    baseNames.get(baseName)!.add(regional ?? 'none');
  }

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
    const nameWithoutQuality = stripQualitySuffixes(stream.name);
    const regional = getRegionalSuffix(nameWithoutQuality);

    if (timezonePreference === 'east') {
      return regional === 'east' || regional === null;
    } else {
      return regional === 'west';
    }
  });
}

// =============================================================================
// Backend Normalization API Integration
// =============================================================================

import { normalizeTexts } from './api';

/**
 * Normalize stream names using the backend normalization engine.
 * This uses the configurable rules defined in the Settings tab.
 *
 * @param names Array of stream names to normalize
 * @returns Promise resolving to map of original name -> normalized name
 */
export async function normalizeStreamNamesWithBackend(names: string[]): Promise<Map<string, string>> {
  if (names.length === 0) {
    return new Map();
  }

  try {
    const response = await normalizeTexts(names);
    const resultMap = new Map<string, string>();

    for (const result of response.results) {
      resultMap.set(result.original, result.normalized);
    }

    return resultMap;
  } catch (error) {
    console.error('Backend normalization failed:', error);
    // Return original names as fallback
    const resultMap = new Map<string, string>();
    for (const name of names) {
      resultMap.set(name, name);
    }
    return resultMap;
  }
}

/**
 * Normalize a single stream name using the backend normalization engine.
 *
 * @param name Stream name to normalize
 * @returns Promise resolving to normalized name
 */
export async function normalizeStreamNameWithBackend(name: string): Promise<string> {
  const results = await normalizeStreamNamesWithBackend([name]);
  return results.get(name) ?? name;
}
