/**
 * Shared config dirty-comparator (bead m1s38.1).
 *
 * A single source of truth for "did the rule config change?" so the Event
 * Sync editor and the standard rule builder (bead m1s38.3) do not drift in
 * what counts as a change. Its first use is stale-marking a live preview: the
 * editor snapshots the built config when a preview runs, and marks the results
 * stale when the current built config diverges from that snapshot.
 *
 * Semantics:
 * - Object key order is irrelevant — a config rebuilt with the same values in a
 *   different key order is NOT a change.
 * - Array order IS significant — pattern order and scope order carry meaning in
 *   a rule config, so reordering them is a real change.
 */

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(canonicalize);
  const obj = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(obj).sort()) {
    out[key] = canonicalize(obj[key]);
  }
  return out;
}

/** Deterministic JSON with sorted object keys (arrays kept in order). */
export function stableStringify(value: unknown): string {
  return JSON.stringify(canonicalize(value)) ?? 'undefined';
}

/** True when two configs are value-equal under {@link stableStringify}. */
export function sameConfig(a: unknown, b: unknown): boolean {
  return stableStringify(a) === stableStringify(b);
}
