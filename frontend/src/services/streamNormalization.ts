/**
 * Stream Normalization Utilities
 *
 * Functions for normalizing, parsing, and filtering stream names.
 * The main normalization is now handled by the backend rules engine.
 * This file contains utility functions for quality detection, sorting, and prefix/suffix handling.
 */

import { logger } from '../utils/logger';
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
function normalizeUnicodeToAscii(input: string): string {
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
 * A normalization response that did not answer the question it was asked.
 *
 * The completeness rule (bead enhancedchannelmanager-e9e5o, fix round 4): a
 * name resolution either covers every requested name with exactly one result,
 * or it is a failure. There is no partial success. A 200 carrying results for
 * two of the three names asked about is a FAILED resolution, not a resolution
 * that two callers then interpret differently.
 */
export class NormalizationIncompleteError extends Error {
  /** Requested names the response said nothing about. */
  readonly missing: readonly string[];
  /** Requested names the response answered more than once. */
  readonly duplicated: readonly string[];
  /** Results for names that were never requested. */
  readonly unexpected: readonly string[];

  constructor(missing: string[], duplicated: string[], unexpected: string[]) {
    const parts: string[] = [];
    if (missing.length > 0) parts.push(`${missing.length} name(s) missing`);
    if (duplicated.length > 0) parts.push(`${duplicated.length} name(s) answered twice`);
    if (unexpected.length > 0) parts.push(`${unexpected.length} unrequested name(s) returned`);
    super(`Normalization response did not cover the request: ${parts.join(', ')}`);
    this.name = 'NormalizationIncompleteError';
    this.missing = missing;
    this.duplicated = duplicated;
    this.unexpected = unexpected;
  }
}

/**
 * Normalize stream names using the backend normalization engine.
 * This uses the configurable rules defined in the Settings tab.
 *
 * REJECTS on a backend failure. It used to swallow the error and return each
 * original name mapped to itself, which made "normalization is off" and
 * "normalization broke" the same value to every caller — and, once the
 * Create Channels toggle became a real control (bead
 * enhancedchannelmanager-e9e5o), the same thing on screen too. Callers that
 * want to carry on with the raw names must now say so explicitly; the
 * bulk-create path does that in {@link resolveCreateChannelNames}.
 *
 * REJECTS on an INCOMPLETE response too. A 200 was previously accepted
 * whatever it contained, so a response covering only some of the requested
 * names produced a partial map that was stamped as a clean success; every
 * consumer then fell back to the raw name for the entries that were not there,
 * which is the same swallowed failure one layer down. Completeness is checked
 * here, at the boundary, because a caller handed a partial map has no way to
 * tell "this name normalizes to itself" from "nobody answered about this name".
 *
 * Names are de-duplicated before the request, so "exactly one result per
 * requested name" is a property of the response and not an artefact of the
 * caller happening to pass a name once.
 *
 * @param names Array of stream names to normalize
 * @returns Promise resolving to map of original name -> normalized name,
 *   containing exactly one entry per DISTINCT requested name
 * @throws whatever the `/api/normalization/normalize` call throws, or
 *   {@link NormalizationIncompleteError} if the response does not cover the
 *   request exactly
 */
export async function normalizeStreamNamesWithBackend(names: string[]): Promise<Map<string, string>> {
  const requested = Array.from(new Set(names));
  if (requested.length === 0) {
    return new Map();
  }

  const response = await normalizeTexts(requested);
  const requestedSet = new Set(requested);
  const resultMap = new Map<string, string>();
  const duplicated: string[] = [];
  const unexpected: string[] = [];

  for (const result of response.results ?? []) {
    const original = result?.original;
    const normalized = result?.normalized;
    if (typeof original !== 'string' || typeof normalized !== 'string') {
      // A malformed entry cannot be attributed to a requested name, so it is
      // counted as unexpected rather than dropped — dropping it would show up
      // only as a missing name, which reads as a different fault.
      unexpected.push(String(original));
      continue;
    }
    if (!requestedSet.has(original)) {
      unexpected.push(original);
      continue;
    }
    if (resultMap.has(original)) {
      duplicated.push(original);
      continue;
    }
    resultMap.set(original, normalized);
  }

  const missing = requested.filter((name) => !resultMap.has(name));
  if (missing.length > 0 || duplicated.length > 0 || unexpected.length > 0) {
    throw new NormalizationIncompleteError(missing, duplicated, unexpected);
  }

  return resultMap;
}

/**
 * The answer to "what name does each of these streams get?", complete by
 * construction (bead enhancedchannelmanager-e9e5o).
 *
 * This is deliberately NOT a `Map`. A map has a missing case, and every caller
 * handed one wrote `map.get(stream.name) ?? stream.name` — each of them
 * independently deciding that "no entry" means "use the raw provider name".
 * That decision is exactly the swallowed failure this bead has now been through
 * four rounds of: the dialog enabled Create, the conflict plan was sized off a
 * raw name, and a raw name was submitted, with nothing on screen saying so.
 *
 * The class has no missing case for a requested name. {@link nameFor} answers
 * for every name the resolution was built from and throws for anything else,
 * so a caller cannot substitute a plausible default for an answer it does not
 * have. Callers whose set of names can drift from the resolution's — a React
 * memo that recomputes before the resolving effect runs — ask
 * {@link coversAll} first and represent the gap as "not resolved yet", which is
 * an explicit unknown rather than a silent default.
 */
export class ResolvedCreateChannelNames {
  private readonly resolved: ReadonlyMap<string, string>;

  /**
   * True only when normalization was REQUESTED and the backend call failed or
   * came back incomplete, so the names are raw provider names the operator did
   * not ask for. False when the operator turned normalization off — those raw
   * names are the requested outcome, not a failure.
   */
  readonly normalizationFailed: boolean;

  constructor(resolved: ReadonlyMap<string, string>, normalizationFailed: boolean) {
    this.resolved = resolved;
    this.normalizationFailed = normalizationFailed;
  }

  /** How many distinct names this resolution answers for. */
  get size(): number {
    return this.resolved.size;
  }

  /** Whether this resolution answers for `streamName`. */
  has(streamName: string): boolean {
    return this.resolved.has(streamName);
  }

  /** Whether this resolution answers for every one of `streamNames`. */
  coversAll(streamNames: readonly string[]): boolean {
    for (const name of streamNames) {
      if (!this.resolved.has(name)) return false;
    }
    return true;
  }

  /**
   * The final name the channel created from `streamName` gets.
   *
   * Throws when this resolution was not built from `streamName`. There is no
   * defaulting overload on purpose: the raw-name default is the defect, and a
   * caller that can be asking about an unresolved name must find that out
   * through {@link coversAll} and say so, not receive a plausible answer.
   */
  nameFor(streamName: string): string {
    const resolved = this.resolved.get(streamName);
    if (resolved === undefined) {
      throw new Error(
        `No resolved channel name for stream "${streamName}". The name resolution ` +
        'was built from a different set of streams; nothing may be created from an ' +
        'unresolved name.'
      );
    }
    return resolved;
  }

  /** `[original, resolved]` pairs, for rendering the preview. */
  entries(): [string, string][] {
    return Array.from(this.resolved.entries());
  }
}

/**
 * Decide, in ONE place, what name a channel created from a stream gets
 * (bead enhancedchannelmanager-e9e5o).
 *
 * The Create Channels dialog's "Normalization Rules" toggle used to drive
 * only the preview: the caller normalized unconditionally and then passed the
 * toggle along as a separate flag that was dropped before it reached the
 * backend. Resolving the name here — before it is staged — is what makes the
 * toggle real, and it is deliberately the ONLY place the question is asked.
 * The name this returns is final; nothing downstream may normalize again, or
 * an already-normalized name would be normalized twice.
 *
 * @param streamNames Raw provider names of the streams being created from.
 * @param normalize The operator's "Normalization Rules" toggle.
 */
export async function resolveCreateChannelNames(
  streamNames: string[],
  normalize: boolean,
): Promise<ResolvedCreateChannelNames> {
  const identity = (): Map<string, string> => {
    const map = new Map<string, string>();
    for (const name of streamNames) {
      map.set(name, name);
    }
    return map;
  };

  /**
   * The single exit. Every branch leaves through here, and here is where the
   * completeness rule is enforced rather than assumed: a map that does not
   * cover every requested name is not returned in a weaker form, it is
   * replaced by the identity resolution and reported as a failure. A future
   * branch added below cannot leak a partial map without going around this
   * function, and there is nowhere else to return from.
   */
  const finish = (
    names: Map<string, string>,
    normalizationFailed: boolean,
  ): ResolvedCreateChannelNames => {
    const missing = streamNames.filter((name) => !names.has(name));
    if (missing.length > 0) {
      logger.error(
        'Name resolution did not cover every requested stream name; falling back to the raw names',
        missing,
      );
      return new ResolvedCreateChannelNames(identity(), true);
    }
    return new ResolvedCreateChannelNames(names, normalizationFailed);
  };

  if (!normalize || streamNames.length === 0) {
    return finish(identity(), false);
  }

  try {
    return finish(await normalizeStreamNamesWithBackend(streamNames), false);
  } catch (error) {
    // Carry on with the raw names rather than abandoning a create the
    // operator already confirmed — but say so, so the resulting names are
    // explainable. An INCOMPLETE response lands here too: a resolution that
    // covers only some of the names is a failed resolution, not a partial
    // success the callers get to interpret one by one. The caller surfaces
    // `normalizationFailed` to the operator.
    logger.error('Backend normalization failed:', error);
    return finish(identity(), true);
  }
}

// normalizeStreamNameWithBackend (singular) removed — use normalizeStreamNamesWithBackend (plural) instead
