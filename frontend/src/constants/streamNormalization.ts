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
