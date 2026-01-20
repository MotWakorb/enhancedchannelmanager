/**
 * Unit tests for epgMatching utility.
 */
import { describe, it, expect } from 'vitest';
import {
  extractLeaguePrefix,
  extractBroadcastCallSign,
  normalizeForEPGMatch,
  normalizeForEPGMatchWithLeague,
  parseTvgId,
  normalizeChannelDisplayName,
  previewNameNormalizations,
} from './epgMatching';

describe('extractLeaguePrefix', () => {
  it('extracts NFL prefix with colon separator', () => {
    const result = extractLeaguePrefix('NFL: Arizona Cardinals');
    expect(result).toEqual({ league: 'nfl', name: 'Arizona Cardinals' });
  });

  it('extracts NFL prefix with pipe separator', () => {
    const result = extractLeaguePrefix('NFL | Atlanta Falcons');
    expect(result).toEqual({ league: 'nfl', name: 'Atlanta Falcons' });
  });

  it('extracts NFL prefix with space only', () => {
    const result = extractLeaguePrefix('NFL ARIZONA CARDINALS');
    expect(result).toEqual({ league: 'nfl', name: 'ARIZONA CARDINALS' });
  });

  it('extracts NBA prefix', () => {
    const result = extractLeaguePrefix('NBA: Chicago Bulls');
    expect(result).toEqual({ league: 'nba', name: 'Chicago Bulls' });
  });

  it('extracts Premier League prefix', () => {
    const result = extractLeaguePrefix('PREMIER LEAGUE: Arsenal');
    expect(result).toEqual({ league: 'premierleague', name: 'Arsenal' });
  });

  it('returns null for no league prefix', () => {
    expect(extractLeaguePrefix('ESPN')).toBeNull();
    expect(extractLeaguePrefix('HBO')).toBeNull();
    expect(extractLeaguePrefix('Regular Channel')).toBeNull();
  });
});

describe('extractBroadcastCallSign', () => {
  it('extracts call signs starting with K', () => {
    expect(extractBroadcastCallSign('KATU Portland')).toBe('katu');
    expect(extractBroadcastCallSign('21.1 | PBS: WHA-DT Madison')).toBe('wha');
  });

  it('extracts call signs starting with W', () => {
    expect(extractBroadcastCallSign('WKOW News')).toBe('wkow');
    expect(extractBroadcastCallSign('6.1 | CBS: KOIN Portland')).toBe('koin');
  });

  it('handles call signs with suffixes', () => {
    expect(extractBroadcastCallSign('WHA-DT Madison')).toBe('wha');
    expect(extractBroadcastCallSign('KPTV-HD Portland')).toBe('kptv');
  });

  it('returns null for non-broadcast call signs', () => {
    expect(extractBroadcastCallSign('ESPN')).toBeNull();
    expect(extractBroadcastCallSign('CNN')).toBeNull();
  });
});

describe('normalizeForEPGMatch', () => {
  it('strips channel number prefix', () => {
    expect(normalizeForEPGMatch('100 | ESPN')).toBe('espn');
    expect(normalizeForEPGMatch('50.1 | ABC')).toBe('abc');
  });

  it('strips country prefix', () => {
    expect(normalizeForEPGMatch('US: ESPN')).toBe('espn');
    expect(normalizeForEPGMatch('UK | BBC')).toBe('bbc');
  });

  it('strips quality suffixes', () => {
    expect(normalizeForEPGMatch('ESPN FHD')).toBe('espn');
    expect(normalizeForEPGMatch('HBO HD')).toBe('hbo');
  });

  it('normalizes to lowercase alphanumeric', () => {
    expect(normalizeForEPGMatch('ESPN-2')).toBe('espn2');
    expect(normalizeForEPGMatch('CNN International')).toBe('cnninternational');
  });

  it('converts + to "plus"', () => {
    expect(normalizeForEPGMatch('AMC+')).toBe('amcplus');
    expect(normalizeForEPGMatch('ESPN+')).toBe('espnplus');
  });

  it('converts & to "and"', () => {
    expect(normalizeForEPGMatch('A&E')).toBe('aande');
  });

  it('strips leading article "the" if long enough', () => {
    expect(normalizeForEPGMatch('The Bob Ross Channel')).toBe('bobrosschannel');
  });
});

describe('normalizeForEPGMatchWithLeague', () => {
  it('returns both normalized name and league', () => {
    const result = normalizeForEPGMatchWithLeague('NFL: Arizona Cardinals');
    expect(result.normalized).toBe('arizonacardinals');
    expect(result.league).toBe('nfl');
  });

  it('returns null league when no prefix', () => {
    const result = normalizeForEPGMatchWithLeague('ESPN');
    expect(result.normalized).toBe('espn');
    expect(result.league).toBeNull();
  });
});

describe('parseTvgId', () => {
  it('parses TVG-ID with country code', () => {
    const [name, country, league] = parseTvgId('ESPN.us');
    expect(name).toBe('espn');
    expect(country).toBe('us');
    expect(league).toBeNull();
  });

  it('parses TVG-ID with league suffix', () => {
    const [name, country, league] = parseTvgId('arizonacardinals.nfl');
    expect(name).toBe('arizonacardinals');
    expect(country).toBeNull();
    expect(league).toBe('nfl');
  });

  it('parses TVG-ID with call sign in parentheses', () => {
    const [name, country, league] = parseTvgId('AdultSwim(ADSM).ca');
    expect(name).toBe('adultswim');
    expect(country).toBe('ca');
    expect(league).toBeNull();
  });

  it('handles TVG-ID without suffix', () => {
    const [name, country, league] = parseTvgId('ESPN');
    expect(name).toBe('espn');
    expect(country).toBeNull();
    expect(league).toBeNull();
  });

  it('handles multiple parentheses', () => {
    const [name] = parseTvgId('AdultSwim(IPFeed)(ASIP).us');
    expect(name).toBe('adultswim');
  });
});

describe('normalizeChannelDisplayName', () => {
  it('normalizes league prefix format', () => {
    expect(normalizeChannelDisplayName('NFL ARIZONA CARDINALS')).toBe('NFL: Arizona Cardinals');
  });

  it('preserves channel number prefix', () => {
    expect(normalizeChannelDisplayName('700 | NFL ARIZONA CARDINALS')).toBe('700 | NFL: Arizona Cardinals');
  });

  it('applies title case without league', () => {
    expect(normalizeChannelDisplayName('ESPN HD')).toBe('Espn Hd');
  });

  it('handles custom separator', () => {
    expect(normalizeChannelDisplayName('NBA CHICAGO BULLS', ' - ')).toBe('NBA - Chicago Bulls');
  });

  it('handles lowercase input', () => {
    expect(normalizeChannelDisplayName('nfl arizona cardinals')).toBe('NFL: Arizona Cardinals');
  });
});

describe('previewNameNormalizations', () => {
  it('returns only changed channels', () => {
    const channels = [
      { id: 1, name: 'NFL ARIZONA CARDINALS' },
      { id: 2, name: 'NFL: Arizona Cardinals' }, // Already normalized
      { id: 3, name: 'NBA CHICAGO BULLS' },
    ];

    const changes = previewNameNormalizations(channels);

    expect(changes).toHaveLength(2);
    expect(changes[0].id).toBe(1);
    expect(changes[1].id).toBe(3);
  });

  it('includes current and normalized names', () => {
    const channels = [
      { id: 1, name: 'NFL ARIZONA CARDINALS' },
    ];

    const changes = previewNameNormalizations(channels);

    expect(changes[0].current).toBe('NFL ARIZONA CARDINALS');
    expect(changes[0].normalized).toBe('NFL: Arizona Cardinals');
  });

  it('returns empty array when nothing to change', () => {
    const channels = [
      { id: 1, name: 'Espn' },
      { id: 2, name: 'Cnn' },
    ];

    const changes = previewNameNormalizations(channels);

    expect(changes).toHaveLength(0);
  });
});
