/**
 * Client-side frontend error reporter — ADR-006 (Phase 1), bead i6a1m.
 *
 * Sends runtime error telemetry to the local ECM sink at
 * ``POST /api/client-errors``. The reporter is deny-by-default:
 *
 *  - Field allowlist (stack, message, pathname, UA major, viewport)
 *  - No query strings, no referrer, no cookies, no localStorage, no
 *    user-typed form content
 *  - Basename-stripped stack frames (no absolute filesystem paths)
 *  - Client-side rate limit (10 events / rolling 60s) so a crash-loop
 *    never hammers the sink — the backend enforces the same cap, but
 *    the local cap means we don't even make the request
 *  - The reporter wraps its own work in try/catch; a reporter exception
 *    MUST NEVER aggravate the crash it was meant to report
 *
 * Transport: `fetch` with `credentials: 'include'` so the JWT cookie
 * rides along. ADR-006 §7 calls for `navigator.sendBeacon` as the
 * primary transport for fire-and-forget delivery on page unload; we
 * use `sendBeacon` when it's available and fall back to `fetch(...,
 * {keepalive: true})` otherwise.
 *
 * Feature flag: ``VITE_ECM_ERROR_TELEMETRY_ENABLED`` — default ON. Set
 * to the literal string ``"false"`` (case-insensitive) to disable the
 * reporter entirely at build time.
 */

export type ClientErrorKind =
  | 'boundary'
  | 'unhandled_rejection'
  | 'chunk_load'
  | 'resource'
  | 'other';

export interface ClientErrorInput {
  kind: ClientErrorKind;
  /** Error message or description — truncated to 512 chars. */
  message: string;
  /** Stack trace — truncated to 4096 chars and basename-stripped. */
  stack?: string;
  /** Override pathname (used for tests); defaults to `window.location.pathname`. */
  route?: string;
}

export interface ClientErrorPayload {
  kind: ClientErrorKind;
  message: string;
  stack: string;
  release: string;
  route: string;
  user_agent_hash: string;
  ts: string;
}

export const MAX_MESSAGE_CHARS = 512;
export const MAX_STACK_CHARS = 4096;
export const RATE_LIMIT_EVENTS = 10;
export const RATE_LIMIT_WINDOW_MS = 60_000;
export const CLIENT_ERRORS_ENDPOINT = '/api/client-errors';

const ABSOLUTE_PATH_RE =
  /(?:file:\/\/)?(?:[A-Za-z]:)?[\\/](?:[^\s\\/:*?"<>|(){}[\],]+[\\/])+/g;

const QUERY_OR_FRAGMENT_RE = /[?#].*$/;

/**
 * Truncate + basename-strip a stack trace.
 *
 * Removes directory prefixes from filesystem-style and `file://` URL
 * paths while preserving the filename and the trailing line/column. A
 * frame like ``at foo (/Users/op/proj/bundle.js:42:17)`` becomes
 * ``at foo (bundle.js:42:17)``.
 */
export function scrubStack(stack: string | undefined | null): string {
  if (!stack) return '';
  const stripped = stack.replace(ABSOLUTE_PATH_RE, '');
  return stripped.length > MAX_STACK_CHARS
    ? stripped.slice(0, MAX_STACK_CHARS)
    : stripped;
}

/** Clip a message to the allowed length; never throws. */
export function scrubMessage(message: string | undefined | null): string {
  const s = message ?? '';
  return s.length > MAX_MESSAGE_CHARS ? s.slice(0, MAX_MESSAGE_CHARS) : s;
}

/** Strip query string and fragment from a pathname (ADR-006 §4 allowlist). */
export function scrubRoute(route: string): string {
  return (route || '').replace(QUERY_OR_FRAGMENT_RE, '');
}

/**
 * SHA-256 hex digest of an arbitrary string, using SubtleCrypto.
 *
 * Returns a 64-char hex string, or an empty string when SubtleCrypto
 * is unavailable (older browsers, non-secure contexts). The Pydantic
 * schema rejects non-hex values, so an empty hash would cause the
 * reporter to drop the event — that's intentional: we'd rather lose
 * signal than send a placeholder.
 */
export async function hashUserAgent(userAgent: string): Promise<string> {
  try {
    const subtle = globalThis.crypto?.subtle;
    if (!subtle) return '';
    const data = new TextEncoder().encode(userAgent);
    const digest = await subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  } catch {
    // Hashing failed — drop the event at build-payload time.
    return '';
  }
}

/**
 * Client-side rate limiter — sliding window, in-memory.
 *
 * The backend enforces the same 10/min cap authoritatively. The local
 * cap exists so a crash-loop (an error that fires an error) doesn't
 * issue 100 fetches/sec and waste CPU on a browser that's already in
 * distress.
 */
export class ClientRateLimiter {
  private readonly events: number[] = [];
  private readonly maxEvents: number;
  private readonly windowMs: number;

  constructor(maxEvents = RATE_LIMIT_EVENTS, windowMs = RATE_LIMIT_WINDOW_MS) {
    this.maxEvents = maxEvents;
    this.windowMs = windowMs;
  }

  /** Return true when the event can be sent; false when the bucket is full. */
  tryConsume(nowMs: number = Date.now()): boolean {
    const cutoff = nowMs - this.windowMs;
    while (this.events.length > 0 && this.events[0] < cutoff) {
      this.events.shift();
    }
    if (this.events.length >= this.maxEvents) {
      return false;
    }
    this.events.push(nowMs);
    return true;
  }

  reset(): void {
    this.events.length = 0;
  }

  get size(): number {
    return this.events.length;
  }
}

/**
 * Build the full request payload from a lightweight error input.
 *
 * Side-effect-free — takes all its inputs from the parameters or from
 * ``window`` / ``navigator`` / ``document`` lookups that never read
 * user data.
 */
export async function buildPayload(
  input: ClientErrorInput,
  options: {
    release?: string;
    userAgent?: string;
    pathname?: string;
    now?: () => Date;
  } = {},
): Promise<ClientErrorPayload | null> {
  try {
    const release = options.release ?? getRelease();
    const userAgent =
      options.userAgent ??
      (typeof navigator !== 'undefined' ? navigator.userAgent : '');
    const pathname =
      options.pathname ??
      input.route ??
      (typeof window !== 'undefined' && window.location
        ? window.location.pathname
        : '');
    const ts = (options.now ? options.now() : new Date()).toISOString();
    const uaHash = await hashUserAgent(userAgent);
    if (!uaHash) {
      return null; // SubtleCrypto unavailable — drop rather than send bogus hash
    }
    return {
      kind: input.kind,
      message: scrubMessage(input.message),
      stack: scrubStack(input.stack),
      release,
      route: scrubRoute(pathname),
      user_agent_hash: uaHash,
      ts,
    };
  } catch {
    return null;
  }
}

/** Read the Vite build-env release identifier; falls back to ``"dev"``. */
export function getRelease(): string {
  try {
    // Vite inlines ``import.meta.env.VITE_ECM_RELEASE`` at build time.
    const fromEnv = (import.meta as { env?: Record<string, string> }).env?.VITE_ECM_RELEASE;
    if (fromEnv) return String(fromEnv).slice(0, 64);
  } catch {
    // import.meta not available in unit tests
  }
  return 'dev';
}

// Runtime override honoring ``settings.telemetry_client_errors_enabled``.
// ``undefined`` means "no runtime decision yet — use the build-time flag".
// ``false`` means the operator explicitly toggled telemetry off; we
// short-circuit before building the payload so a crashed app still
// respects the operator's choice without a page refresh. Updated from
// ``applySettings()`` in ``hooks/useAuth`` (or any settings-loading site)
// via ``setTelemetryRuntimeEnabled``.
let _runtimeTelemetryEnabled: boolean | undefined = undefined;

/**
 * Mirror ``settings.telemetry_client_errors_enabled`` from the backend
 * onto the reporter. Call this after ``GET /api/settings`` resolves.
 * Pass ``undefined`` to defer to the build-time flag only.
 */
export function setTelemetryRuntimeEnabled(enabled: boolean | undefined): void {
  _runtimeTelemetryEnabled = enabled;
}

/** Test-only accessor — returns the current runtime override state. */
export function getTelemetryRuntimeOverride(): boolean | undefined {
  return _runtimeTelemetryEnabled;
}

/**
 * Read the feature flag; defaults to enabled unless explicitly "false".
 *
 * Two gates:
 *  1. Build-time ``VITE_ECM_ERROR_TELEMETRY_ENABLED`` — set at Vite build
 *     to disable the reporter entirely (never sends, never listens).
 *  2. Runtime override mirroring ``settings.telemetry_client_errors_enabled``
 *     — the operator can flip the toggle in ECM's settings UI and the
 *     reporter short-circuits on the next event without a rebuild.
 *
 * Either gate set to false disables sending.
 */
export function isTelemetryEnabled(): boolean {
  // Runtime gate (operator toggle via /api/settings) wins when set.
  if (_runtimeTelemetryEnabled === false) return false;
  try {
    const flag = (import.meta as { env?: Record<string, string> }).env?.VITE_ECM_ERROR_TELEMETRY_ENABLED;
    if (flag !== undefined && String(flag).toLowerCase() === 'false') {
      return false;
    }
  } catch {
    // no-op — default to enabled
  }
  return true;
}

/**
 * Send a payload via the telemetry endpoint.
 *
 * Tries ``navigator.sendBeacon`` first (fire-and-forget, survives page
 * unload) and falls back to ``fetch`` with ``keepalive: true``. Every
 * failure mode is caught — the reporter NEVER rethrows.
 */
export async function sendPayload(payload: ClientErrorPayload): Promise<boolean> {
  const body = JSON.stringify(payload);
  try {
    if (
      typeof navigator !== 'undefined' &&
      typeof navigator.sendBeacon === 'function'
    ) {
      const blob = new Blob([body], { type: 'application/json' });
      const queued = navigator.sendBeacon(CLIENT_ERRORS_ENDPOINT, blob);
      if (queued) return true;
    }
  } catch {
    // fall through to fetch
  }
  try {
    await fetch(CLIENT_ERRORS_ENDPOINT, {
      method: 'POST',
      body,
      credentials: 'include',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
    });
    return true;
  } catch {
    // Reporter failure is swallowed — see ADR-006 §7.
    return false;
  }
}


// ---------------------------------------------------------------------------
// Module-level reporter instance wired to the real window
// ---------------------------------------------------------------------------
const _reporterRateLimiter = new ClientRateLimiter();

/** Report a single client error. Swallows every exception. */
export async function reportClientError(input: ClientErrorInput): Promise<boolean> {
  if (!isTelemetryEnabled()) return false;
  if (!_reporterRateLimiter.tryConsume()) return false;
  try {
    const payload = await buildPayload(input);
    if (!payload) return false;
    return await sendPayload(payload);
  } catch {
    return false;
  }
}

/** Reset module-level rate-limit state — test-only helper. */
export function resetReporterForTests(): void {
  _reporterRateLimiter.reset();
}


// ---------------------------------------------------------------------------
// Global capture wiring (idempotent)
// ---------------------------------------------------------------------------
let _wired = false;

/**
 * Install ``window.onerror`` + ``unhandledrejection`` listeners so
 * escaped runtime errors get reported. Idempotent — calling twice
 * installs listeners once.
 *
 * ``ErrorBoundary`` is wired directly in its ``onError`` prop (see
 * ``main.tsx``), not through this function, because React gives us
 * the error before it reaches window-level handlers.
 */
export function installGlobalErrorHandlers(): void {
  if (_wired || typeof window === 'undefined') return;
  _wired = true;

  window.addEventListener('error', (event: ErrorEvent) => {
    const err = event.error as Error | undefined;
    void reportClientError({
      kind: 'boundary',
      message: (err?.message ?? event.message ?? 'Unknown error').toString(),
      stack: err?.stack ?? '',
    });
  });

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    const reason = event.reason as unknown;
    let message: string;
    let stack = '';
    if (reason instanceof Error) {
      message = reason.message;
      stack = reason.stack ?? '';
    } else if (typeof reason === 'string') {
      message = reason;
    } else {
      try {
        message = JSON.stringify(reason);
      } catch {
        message = 'Unhandled rejection';
      }
    }
    void reportClientError({
      kind: 'unhandled_rejection',
      message,
      stack,
    });
  });

  // Vite 5+ emits ``vite:preloadError`` on the window when a lazy-loaded
  // chunk cannot be fetched (typically a stale client hitting a deployed
  // build whose hashed chunk no longer exists on the server). This is
  // the most common real-world failure after a deploy, and the reason
  // ``kind: 'chunk_load'`` exists in the ADR-006 enum.
  window.addEventListener('vite:preloadError', (event: Event) => {
    const payloadEvent = event as Event & {
      payload?: { message?: string; stack?: string };
    };
    const err = payloadEvent.payload as Error | undefined;
    void reportClientError({
      kind: 'chunk_load',
      message: err?.message ?? 'Vite preload error',
      stack: err?.stack ?? '',
    });
  });
}

// ---------------------------------------------------------------------------
// Dynamic-import catch helper
// ---------------------------------------------------------------------------
/**
 * Wrap a ``React.lazy(() => import(...))`` loader so any failure to
 * fetch the chunk produces a ``kind: 'chunk_load'`` report before the
 * promise rejection propagates to ``window.onerror`` / the React
 * suspense boundary.
 *
 * Usage:
 * ```ts
 * const MyTab = lazy(() => withImportTelemetry(import('./MyTab')));
 * ```
 *
 * The wrapper re-rejects with the original error — it's a pure
 * side-effect site that adds telemetry without changing callers' error
 * handling contract. Suspense / ErrorBoundary still see the failure.
 */
export function withImportTelemetry<T>(loader: Promise<T>): Promise<T> {
  return loader.catch((err: unknown) => {
    const error = err as Error | undefined;
    void reportClientError({
      kind: 'chunk_load',
      message: error?.message ?? 'Dynamic import failed',
      stack: error?.stack ?? '',
    });
    throw err;
  });
}
