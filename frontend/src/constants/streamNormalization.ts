/**
 * Stream Normalization Constants
 *
 * Shared constants for normalizing stream and channel names.
 * Used by both api.ts (stream quality sorting) and epgMatching.ts (EPG matching).
 */

// Quality suffixes to strip when normalizing stream names for matching
// These are common quality/resolution indicators that don't change the channel identity
export const QUALITY_SUFFIXES = [
  'FHD', 'UHD', '4K', 'HD', 'SD',
  '1080P', '1080I', '720P', '480P', '2160P',
  'HEVC', 'H264', 'H265',
];

// Timezone/regional suffixes to strip
export const TIMEZONE_SUFFIXES = ['EAST', 'WEST', 'ET', 'PT', 'CT', 'MT'];

// League/network prefixes that appear before team names
// These should be stripped from channel names and matched as EPG suffixes
// e.g., "NFL: Arizona Cardinals" -> "arizonacardinals" which matches "arizonacardinals.nfl"
export const LEAGUE_PREFIXES = [
  'NFL', 'NBA', 'MLB', 'NHL', 'MLS', 'WNBA', 'NCAA', 'CFB', 'CBB',
  'EPL', 'PREMIER LEAGUE', 'LA LIGA', 'LALIGA', 'BUNDESLIGA', 'SERIE A', 'LIGUE 1',
  'UEFA', 'FIFA', 'F1', 'NASCAR', 'PGA', 'ATP', 'WTA',
  'WWE', 'UFC', 'AEW', 'BOXING',
];

// Network/channel prefixes that should be stripped when followed by content names
// These are networks that often prefix their content with their branding
// Format: "NETWORK | Content Name" or "NETWORK: Content Name"
export const NETWORK_PREFIXES = [
  // Sports networks
  'CHAMP', 'CHAMPIONSHIP', 'PPV', 'PAY PER VIEW',
  'PREMIER', 'PREMIER LEAGUE', 'PL', 'PRIME',
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

// Network/channel suffixes that should be stripped from channel names
// These are commonly appended tags that don't contribute to channel identity
// Format: "Channel Name (SUFFIX)" or "Channel Name [SUFFIX]" or "Channel Name SUFFIX"
export const NETWORK_SUFFIXES = [
  // Language/region tags
  'ENGLISH', 'ENG', 'SPANISH', 'ESP', 'FRENCH', 'FRA', 'GERMAN', 'DEU', 'PORTUGUESE', 'POR',
  // Content type tags
  'LIVE', 'REPLAY', 'DELAY', 'BACKUP', 'ALT', 'ALTERNATE', 'MAIN',
  // Source/provider tags
  'FEED', 'MULTI', 'CLEAN', 'RAW', 'PRIMARY', 'SECONDARY',
  // Event-specific tags
  'PPV', 'EVENT', 'SPECIAL', 'EXCLUSIVE',
  // Technical tags (less commonly used - quality tags are handled separately)
  'MPEG2', 'MPEG4', 'AVC', 'STEREO', 'MONO', '5.1', 'SURROUND',
];

// Quality priority for stream ordering (lower number = higher priority/quality)
// Streams without quality indicators default to 720p position (priority 30)
export const QUALITY_PRIORITY: Record<string, number> = {
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
export const DEFAULT_QUALITY_PRIORITY = 30;

// Common country prefixes found in stream names
// These typically appear at the start of the name followed by a separator
export const COUNTRY_PREFIXES = [
  'US', 'USA', 'UK', 'CA', 'AU', 'NZ', 'IE', 'IN', 'PH', 'MX', 'BR', 'DE', 'FR', 'ES', 'IT', 'NL', 'BE', 'CH', 'AT', 'PL', 'SE', 'NO', 'DK', 'FI', 'PT', 'GR', 'TR', 'RU', 'JP', 'KR', 'CN', 'TW', 'HK', 'SG', 'MY', 'TH', 'ID', 'VN', 'PK', 'BD', 'LK', 'ZA', 'EG', 'NG', 'KE', 'GH', 'AR', 'CL', 'CO', 'PE', 'VE', 'EC', 'PR', 'DO', 'CU', 'JM', 'TT', 'BB', 'CR', 'PA', 'HN', 'SV', 'GT', 'NI', 'BZ', 'IL', 'AE', 'SA', 'QA', 'KW', 'BH', 'OM', 'JO', 'LB', 'IR', 'IQ', 'AF', 'LATAM', 'LATINO', 'LATIN',
];

/**
 * Tag group metadata for the normalization UI.
 * Each group contains a display title, Material icon name, and array of available tags.
 * Tags are user-configurable patterns that get stripped during stream name normalization.
 */
export interface TagGroupMetadata {
  title: string;
  icon: string;
  description: string;
  tags: string[];
}

export type TagGroupName = 'country' | 'league' | 'network' | 'quality' | 'timezone';

export const TAG_GROUPS: Record<TagGroupName, TagGroupMetadata> = {
  country: {
    title: 'Country Prefixes',
    icon: 'public',
    description: 'Country codes stripped from the beginning of stream names (e.g., "US: ESPN" → "ESPN")',
    tags: [...COUNTRY_PREFIXES],
  },
  league: {
    title: 'League Prefixes',
    icon: 'sports_football',
    description: 'Sports league names stripped from stream names (e.g., "NFL: Cardinals" → "Cardinals")',
    tags: [...LEAGUE_PREFIXES],
  },
  network: {
    title: 'Network Tags',
    icon: 'tv',
    description: 'Network prefixes and suffixes stripped from channel names',
    tags: [...new Set([...NETWORK_PREFIXES, ...NETWORK_SUFFIXES])],
  },
  quality: {
    title: 'Quality Suffixes',
    icon: 'high_quality',
    description: 'Resolution and codec tags stripped from stream names (e.g., "ESPN HD" → "ESPN")',
    tags: [...QUALITY_SUFFIXES],
  },
  timezone: {
    title: 'Timezone Suffixes',
    icon: 'schedule',
    description: 'Regional timezone markers stripped from stream names (e.g., "ESPN EAST" → "ESPN")',
    tags: [...TIMEZONE_SUFFIXES],
  },
};
