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

