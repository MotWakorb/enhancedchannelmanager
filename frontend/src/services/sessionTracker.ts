/**
 * Frontend session tracker — emits SLO-6 denominator beacon.
 *
 * Implements bd-arp3o per the spike decision in
 * ``docs/sre/spike-slo-6-session-semantics.md`` (bd-1tl01) — Option C:
 * cookie/sessionStorage lifetime as the session boundary.
 *
 * Behavior on first SPA mount per browser tab:
 *   1. Read ``sessionStorage.getItem('ecm_session_id')``.
 *   2. If present, do nothing — the session has already been counted.
 *   3. If absent (and ``sessionStorage`` is available + telemetry is
 *      enabled), generate ``crypto.randomUUID()`` (SubtleCrypto-backed),
 *      persist it, and POST it to ``/api/session-start``.
 *
 * Fail-open contract: the entire flow is wrapped in try/catch. If
 * ``sessionStorage`` is unavailable (private mode, strict-privacy
 * browser, SecurityError, QuotaExceededError) OR ``crypto.randomUUID``
 * is unavailable (Tor Browser default, very old browsers), the function
 * silently no-ops. The user is excluded from the SLO denominator but is
 * NOT blocked from the app — per spike §3.3 UX rationale.
 *
 * Honors the same gates as ``clientErrorReporter.ts`` —
 * ``VITE_ECM_ERROR_TELEMETRY_ENABLED`` build flag and the runtime
 * operator toggle (``settings.telemetry_client_errors_enabled``). One
 * toggle controls both telemetry surfaces, per spike §6.1.
 *
 * The session_id is NEVER read or written by the rest of the codebase.
 * It exists only to deduplicate the backend's ``ecm_session_starts_total``
 * counter. The reporter never sends it as a label or returns it from
 * any function — once persisted, it's opaque even to its own module.
 */

import { isTelemetryEnabled } from './clientErrorReporter';

export const SESSION_STORAGE_KEY = 'ecm_session_id';
export const SESSION_START_ENDPOINT = '/api/session-start';

/**
 * UUIDv4 regex — matches the server-side ``_UUID_V4_RE`` in
 * ``backend/routers/session_starts.py``. Used as a defensive guard
 * against a corrupt sessionStorage entry (e.g. from a downgrade where
 * the user previously had a non-UUID value cached).
 */
const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * Probe sessionStorage availability without raising.
 *
 * Some browsers (Firefox in dom.storage.enabled=false, Tor Browser
 * with strict privacy, Safari in Private Browsing on quota-exhausted
 * tabs) throw SecurityError or QuotaExceededError on either getItem
 * or setItem. Probing once with a trial round-trip is the only
 * reliable signal — checking ``typeof sessionStorage`` is not enough.
 *
 * Returns the underlying Storage instance when usable, or null when
 * any access throws.
 */
function getSessionStorageOrNull(): Storage | null {
  try {
    if (typeof window === 'undefined' || !window.sessionStorage) {
      return null;
    }
    const probeKey = '__ecm_storage_probe__';
    window.sessionStorage.setItem(probeKey, '1');
    window.sessionStorage.removeItem(probeKey);
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * Generate a UUIDv4 via SubtleCrypto-backed ``crypto.randomUUID``.
 *
 * Returns null if ``crypto.randomUUID`` is unavailable. We do NOT fall
 * back to Math.random() — a non-crypto-strong identifier would
 * regress the spike's security constraint (§3.2: "SubtleCrypto-generated
 * v4 UUID, NOT a hash of UA / IP / user identifiers"). Better to skip
 * the session count than to send a weak identifier.
 */
function generateSessionId(): string | null {
  try {
    const c = globalThis.crypto;
    if (!c || typeof c.randomUUID !== 'function') {
      return null;
    }
    const id = c.randomUUID();
    if (!UUID_V4_RE.test(id)) {
      // crypto.randomUUID is specified to return v4; this branch is
      // pure paranoia for a polyfill that returns a non-v4 string.
      return null;
    }
    return id;
  } catch {
    return null;
  }
}

/**
 * Send the session-start beacon. Fire-and-forget — every error path is
 * caught so a transport failure cannot bubble up and aggravate any
 * crash the page is already experiencing.
 *
 * Mirrors the transport selection in clientErrorReporter.sendPayload:
 * ``navigator.sendBeacon`` first (best-effort delivery on page unload),
 * fallback to ``fetch`` with ``keepalive: true``.
 */
async function sendSessionStart(sessionId: string): Promise<boolean> {
  const body = JSON.stringify({ session_id: sessionId });
  try {
    if (
      typeof navigator !== 'undefined' &&
      typeof navigator.sendBeacon === 'function'
    ) {
      const blob = new Blob([body], { type: 'application/json' });
      const queued = navigator.sendBeacon(SESSION_START_ENDPOINT, blob);
      if (queued) return true;
    }
  } catch {
    // fall through to fetch
  }
  try {
    await fetch(SESSION_START_ENDPOINT, {
      method: 'POST',
      body,
      credentials: 'include',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Idempotency guard — prevents a second invocation in the same tab
 * from re-checking sessionStorage and re-issuing the POST. The
 * sessionStorage entry would already short-circuit the POST, but this
 * guard avoids the storage round-trip entirely on subsequent calls.
 */
let _installed = false;

/**
 * Install the session tracker. Reads ``sessionStorage`` for an existing
 * session_id; if absent, generates one, persists it, and POSTs to
 * ``/api/session-start`` so the backend's
 * ``ecm_session_starts_total`` counter increments by 1 for this tab's
 * lifetime.
 *
 * Idempotent within a single tab: calling more than once never issues
 * a second POST — the sessionStorage entry blocks the network call,
 * and the in-module guard blocks the storage round-trip.
 *
 * Safe to call before the React tree mounts.
 */
export async function installSessionTracker(): Promise<void> {
  if (_installed) return;
  _installed = true;

  // Build-time + runtime telemetry gate (one toggle controls both
  // /api/client-errors and /api/session-start, per spike §6.1).
  if (!isTelemetryEnabled()) return;

  const storage = getSessionStorageOrNull();
  if (!storage) {
    // Fail-open: strict-privacy browsers / private mode are silently
    // excluded from the SLO-6 denominator. They can still use the app.
    return;
  }

  let existing: string | null;
  try {
    existing = storage.getItem(SESSION_STORAGE_KEY);
  } catch {
    // Treat any read failure as "no prior session" but bail rather than
    // attempt a write that will probably also fail.
    return;
  }
  if (existing && UUID_V4_RE.test(existing)) {
    // Session already counted in this tab — nothing to do.
    return;
  }

  const sessionId = generateSessionId();
  if (!sessionId) {
    // SubtleCrypto / crypto.randomUUID unavailable — fail open.
    return;
  }

  try {
    storage.setItem(SESSION_STORAGE_KEY, sessionId);
  } catch {
    // setItem failure (quota or security policy) — skip the POST so we
    // never count a session whose id we couldn't persist for the rest
    // of the tab's lifetime. Otherwise a refresh would re-count.
    return;
  }

  // Fire-and-forget. Do NOT await — the caller (main.tsx) doesn't need
  // to block React mount on a telemetry beacon. Errors are swallowed
  // inside sendSessionStart.
  void sendSessionStart(sessionId);
}

/**
 * Test-only helper — clears the in-module install guard so a fresh
 * test can simulate a brand-new tab. Does NOT touch sessionStorage —
 * tests manage that directly.
 */
export function resetSessionTrackerForTests(): void {
  _installed = false;
}
