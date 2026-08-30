/**
 * Tests for the shared HDHomeRun inference (bead enhancedchannelmanager-sccol).
 *
 * The positive cases are the endpoints a real device serves. The negative
 * cases are the ones that make the inference safe to put on a badge: a
 * generic provider that merely mentions the vendor, or happens to contain
 * the digits 5004, must never be relabelled as a tuner.
 *
 * `http://192.168.1.105/lineup.m3u` is the live account this bead was filed
 * against — the URL `stream_prober.py`'s `':5004/' or 'hdhomerun' in url`
 * heuristic misses.
 */
import { describe, it, expect } from 'vitest';
import { isHDHomerunAccount, isHDHomerunLineupUrl, isHDHomerunUrl } from './hdhomerun';

describe('isHDHomerunUrl', () => {
  it.each([
    ['the live lineup URL this bead was filed against', 'http://192.168.1.105/lineup.m3u'],
    ['the m3u8 lineup flavour', 'http://192.168.1.105/lineup.m3u8'],
    ['the JSON lineup flavour', 'http://192.168.1.105/lineup.json'],
    ['an uppercase lineup path', 'http://192.168.1.105/Lineup.M3U'],
    ['the tuner streaming port', 'http://192.168.1.105:5004/'],
    ['a per-channel stream path', 'http://192.168.1.105:5004/auto/v703'],
    ['a per-channel stream path on the default port', 'http://192.168.1.105/auto/v703'],
    ['an mDNS tuner hostname', 'http://hdhomerun.local/lineup.m3u'],
    ['a hostname that embeds the model', 'http://hdhomerun-1234ABCD.lan/anything'],
  ])('matches %s', (_label, url) => {
    expect(isHDHomerunUrl(url)).toBe(true);
  });

  it.each([
    ['a bare Xtream host', 'https://crx.watch'],
    ['a bare Xtream host on a custom port', 'http://qazws23.xyz:8080/get.php?username=a&password=b'],
    // The failure mode the badge must not have: stream_prober.py's
    // `'hdhomerun' in url.lower()` matches all three of these.
    ['the vendor name in a query string', 'http://provider.example/get.php?category=hdhomerun-uk'],
    ['the vendor name in a path segment', 'http://provider.example/hdhomerun/playlist.m3u'],
    ['the vendor name in a playlist filename', 'http://provider.example/hdhomerun.m3u'],
    // ...and `':5004/' in url` matches these.
    ['5004 inside a host octet', 'http://10.0.5004.1/playlist.m3u'],
    ['5004 as a query parameter', 'http://provider.example/get.php?bitrate=5004/'],
    ['a lineup path nested under a proxy prefix', 'http://proxy.example/hdhr/lineup.m3u'],
    ['a plain playlist at the root', 'http://provider.example/playlist.m3u'],
    ['a non-URL string', 'not a url'],
    ['an empty string', ''],
  ])('rejects %s', (_label, url) => {
    expect(isHDHomerunUrl(url)).toBe(false);
  });

  it('rejects null and undefined', () => {
    expect(isHDHomerunUrl(null)).toBe(false);
    expect(isHDHomerunUrl(undefined)).toBe(false);
  });
});

describe('isHDHomerunLineupUrl', () => {
  it('matches only the round-trippable lineup form the modal authors', () => {
    expect(isHDHomerunLineupUrl('http://192.168.1.105/lineup.m3u')).toBe(true);
    expect(isHDHomerunLineupUrl('http://192.168.1.105/lineup.m3u8')).toBe(true);
  });

  it('does not widen to the other tuner signals', () => {
    // These are HDHomeRun, but they are not URLs the modal may rewrite into
    // `http://<host>/lineup.m3u` without destroying what the user entered.
    expect(isHDHomerunUrl('http://192.168.1.105:5004/auto/v703')).toBe(true);
    expect(isHDHomerunLineupUrl('http://192.168.1.105:5004/auto/v703')).toBe(false);
    expect(isHDHomerunLineupUrl('http://192.168.1.105/lineup.json')).toBe(false);
    expect(isHDHomerunLineupUrl('http://hdhomerun.local/anything')).toBe(false);
  });
});

describe('isHDHomerunAccount', () => {
  it('labels the live HD Homerun account', () => {
    expect(isHDHomerunAccount({
      account_type: 'STD',
      server_url: 'http://192.168.1.105/lineup.m3u',
    })).toBe(true);
  });

  it('never labels an XtremeCodes account, whatever its URL says', () => {
    expect(isHDHomerunAccount({
      account_type: 'XC',
      server_url: 'http://192.168.1.105:5004/lineup.m3u',
    })).toBe(false);
  });

  it('leaves a generic standard playlist alone', () => {
    expect(isHDHomerunAccount({
      account_type: 'STD',
      server_url: 'http://provider.example/playlist.m3u',
    })).toBe(false);
  });

  it('leaves a file-backed account alone', () => {
    expect(isHDHomerunAccount({ account_type: 'STD', server_url: null })).toBe(false);
  });
});
