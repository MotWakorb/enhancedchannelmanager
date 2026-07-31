/**
 * Renders a provider's live connections against its upstream capacity.
 *
 * `max_streams: 0` means unlimited — M3U Manager and the profile modal both
 * render it as "Unlimited". Printing it as "0/0" said the opposite: that the
 * provider had no capacity at all. Unlimited shows as ∞ instead.
 *
 * `max: null` is the Unknown bucket (bd-lhxfu): capacity is not knowable there,
 * so it stays a bare count rather than gaining an invented denominator.
 */
export function formatConnections(current: number, max: number | null): string {
  if (max === null) return String(current);
  return max === 0 ? `${current}/∞` : `${current}/${max}`;
}

/** Spoken form for the tooltip; "∞" alone is terse read aloud. */
export function describeConnections(name: string, current: number, max: number | null): string {
  if (max === null) return `${name}: ${current} active, provider not attributed`;
  return max === 0
    ? `${name}: ${current} active of unlimited`
    : `${name}: ${current} active of ${max}`;
}
