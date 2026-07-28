/**
 * HDHomeRun detection (bead enhancedchannelmanager-sccol).
 *
 * Dispatcharr has no stored account type for an HDHomeRun tuner: the device
 * is added as a plain `STD` M3U account pointed at the tuner's lineup
 * endpoint. Every surface that wants to say "this is a tuner, not a
 * playlist" therefore has to infer it from the URL, and before this module
 * there were two independent spellings of that inference:
 *
 *   - `M3UAccountModal.tsx` matched the lineup path exactly, because it
 *     round-trips the URL it authored (`http://<host>/lineup.m3u`).
 *   - `backend/stream_prober.py` matched `':5004/'` or the literal substring
 *     `'hdhomerun'` anywhere in a *stream* URL, to cap concurrent probes at
 *     two tuners.
 *
 * Neither one alone covers the account row, so both live here now (the
 * Python side is a separate runtime and still carries its own copy — see the
 * bead).
 *
 * ## What counts as HDHomeRun
 *
 * All four checks below are read off a parsed `URL`, never off the raw
 * string, so a signal that appears in a query string or a path segment of an
 * unrelated provider cannot trigger them:
 *
 *   1. `pathname` is `/lineup.m3u` or `/lineup.m3u8` — the device's HTTP
 *      lineup endpoint, and the exact URL the "HD Homerun" option in
 *      `M3UAccountModal` writes.
 *   2. `pathname` is `/lineup.json` — same endpoint, JSON flavour.
 *   3. `port` is `5004` — the tuner's streaming port.
 *   4. `pathname` starts with `/auto/` — the tuner's per-channel stream
 *      endpoint (`/auto/v703`).
 *   5. `hostname` contains `hdhomerun` — e.g. `hdhomerun.local`.
 *
 * ## What deliberately does NOT count
 *
 *   - `hdhomerun` anywhere other than the hostname. `stream_prober.py`'s
 *     `'hdhomerun' in url.lower()` matches
 *     `http://provider.example/get.php?category=hdhomerun-uk`, which is a
 *     generic playlist. Mislabelling a real provider is the failure mode
 *     this module is most conservative about.
 *   - `5004` as a substring. `http://10.0.5004.1/x` and
 *     `?bitrate=5004` are not tuners; only the parsed port counts.
 *   - `lineup.m3u` deeper in a path (`/hdhr/lineup.m3u` from a proxy) —
 *     the device serves it at the root and nowhere else.
 *   - Anything on an `XC` (XtremeCodes) account. That type is stored and
 *     authoritative; a tuner is never an XC account.
 *   - File-backed accounts (no `server_url`).
 */
import type { M3UAccount } from '../types';

/** Path the HDHomeRun HTTP server publishes its channel lineup on. */
const LINEUP_PATHS = ['/lineup.m3u', '/lineup.m3u8'] as const;

/** Same endpoint in its JSON flavour — accepted for detection, not authoring. */
const LINEUP_JSON_PATH = '/lineup.json';

/** The tuner's streaming port. */
const STREAM_PORT = '5004';

/** Prefix of the tuner's per-channel stream paths (`/auto/v703`). */
const STREAM_PATH_PREFIX = '/auto/';

function parse(url: string | null | undefined): URL | null {
  if (!url) return null;
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

/**
 * True for the exact lineup URL shape that `M3UAccountModal` authors and
 * reads back — `http://<host>/lineup.m3u` or `.m3u8`.
 *
 * This is the narrow, round-trippable form. It stays separate from
 * {@link isHDHomerunUrl} on purpose: the modal rewrites the URL it opens in
 * HD Homerun mode, so widening *its* test would silently rewrite a URL the
 * user typed by hand. Detection may be generous; authoring may not.
 */
export function isHDHomerunLineupUrl(url: string | null | undefined): boolean {
  const parsed = parse(url);
  if (!parsed) return false;
  return (LINEUP_PATHS as readonly string[]).includes(parsed.pathname.toLowerCase());
}

/**
 * True when a URL carries any HDHomeRun signal. See the module docstring for
 * the full list of what matches and what is deliberately rejected.
 */
export function isHDHomerunUrl(url: string | null | undefined): boolean {
  const parsed = parse(url);
  if (!parsed) return false;
  const path = parsed.pathname.toLowerCase();
  return (
    isHDHomerunLineupUrl(url)
    || path === LINEUP_JSON_PATH
    || parsed.port === STREAM_PORT
    || path.startsWith(STREAM_PATH_PREFIX)
    || parsed.hostname.toLowerCase().includes('hdhomerun')
  );
}

/**
 * True when an M3U account is an HDHomeRun tuner rather than a playlist.
 *
 * Only `STD` accounts are candidates — `XC` is a stored, authoritative type.
 */
export function isHDHomerunAccount(
  account: Pick<M3UAccount, 'account_type' | 'server_url'>,
): boolean {
  if (account.account_type !== 'STD') return false;
  return isHDHomerunUrl(account.server_url);
}
