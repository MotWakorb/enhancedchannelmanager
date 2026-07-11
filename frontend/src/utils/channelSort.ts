/**
 * Shared channel-name sort semantics for the manual Sort & Renumber modal
 * (ChannelsPane.tsx) and — via a 1:1 backend port — the `sort_group`
 * Channel Pipeline action (backend/channel_pipeline_sort.py, run once per
 * group as Pass 3.6 in channel_pipeline_engine.py). Keep both sides in
 * lock-step: if either the regexes or the natural-sort algorithm changes
 * here, update backend/channel_pipeline_sort.py and re-run:
 *
 *   - frontend: npx vitest run src/utils/channelSort.test.ts
 *   - backend:  python -m pytest tests/unit/test_channel_pipeline_sort.py
 *
 * enhancedchannelmanager-hf8t9 (manual modal descending toggle) /
 * enhancedchannelmanager-vy4fl (sort_group pipeline action).
 */
import { naturalCompare } from './naturalSort';

/**
 * Strip a leading/trailing/embedded channel number from a name for sorting
 * purposes. Matches the same patterns as computeAutoRename: "123 | Name",
 * "123-Name", "US | 5034 - Name", "Name | 123".
 *
 * Extracted from ChannelsPane.tsx's former inline `getNameForSorting`
 * (originally a `useCallback` with no deps — a pure function that didn't
 * need component scope). Behavior is unchanged, including one quirk: on a
 * mid-position match ("US | 5034 - Name") the pipe/spaces captured in
 * group 1 are kept verbatim, so the result is "US | - Name", not the
 * "US - Name" the original inline comment described. Ported as-is for
 * parity with existing manually-sorted results — see
 * backend/channel_pipeline_sort.py for the matching Python port.
 */
export function getNameForSorting(channelName: string): string {
  // Try stripping mid-position number first: "US | 5034 - Name" -> "US | - Name"
  const midMatch = channelName.match(/^([A-Za-z].+?\s*\|\s*)\d+(?:\.\d+)?\s*([-:]\s*.+)$/);
  if (midMatch) {
    return (midMatch[1] + midMatch[2]).trim();
  }

  // Try stripping prefix: "123 | Name" or "123-Name" or "123.Name" or "123 Name"
  const prefixMatch = channelName.match(/^(\d+(?:\.\d+)?)\s*[|\-.\s]\s*(.+)$/);
  if (prefixMatch) {
    return prefixMatch[2].trim();
  }

  // Try stripping suffix: "Name | 123"
  const suffixMatch = channelName.match(/^(.+)\s*[|\-.]\s*(\d+(?:\.\d+)?)$/);
  if (suffixMatch) {
    return suffixMatch[1].trim();
  }

  // No number prefix/suffix found, return as-is
  return channelName;
}

/**
 * Strip a leading country-code prefix from a channel name for sorting.
 * Common patterns: "US | Name", "UK: Name", "CA - Name", "AU Name".
 * Country codes are 2-3 uppercase letters at the start.
 *
 * Extracted from ChannelsPane.tsx's former inline `stripCountryPrefix`.
 */
export function stripCountryPrefix(channelName: string): string {
  const match = channelName.match(/^[A-Z]{2,3}\s*[|:-]\s*(.+)$/);
  if (match) {
    return match[1].trim();
  }
  const noSepMatch = channelName.match(/^[A-Z]{2,3}\s+([A-Z].+)$/);
  if (noSepMatch) {
    return noSepMatch[1].trim();
  }
  return channelName;
}

export type ChannelSortOrder = 'asc' | 'desc';

export interface ChannelNameCompareOptions {
  /** Ignore embedded channel numbers in names when sorting. Default true. */
  stripNumbers?: boolean;
  /** Ignore a leading country prefix (e.g. "US | ") when sorting. Default false. */
  ignoreCountry?: boolean;
  /** Sort direction. Default 'asc'. */
  order?: ChannelSortOrder;
}

/**
 * Compare two channel names using the manual Sort & Renumber modal's
 * semantics: optional number-stripping, optional country-prefix-stripping,
 * then case-insensitive natural sort. Negating the ascending comparator's
 * result for 'desc' (rather than sorting ascending and reversing the
 * array) preserves the original relative order of equal-key items —
 * Array.prototype.sort is stable, and a comparator returning 0 for ties
 * leaves them untouched regardless of sign.
 */
export function compareChannelNames(
  nameA: string,
  nameB: string,
  options: ChannelNameCompareOptions = {}
): number {
  const { stripNumbers = true, ignoreCountry = false, order = 'asc' } = options;

  let a = nameA;
  let b = nameB;
  if (stripNumbers) {
    a = getNameForSorting(a);
    b = getNameForSorting(b);
  }
  if (ignoreCountry) {
    a = stripCountryPrefix(a);
    b = stripCountryPrefix(b);
  }

  const cmp = naturalCompare(a.toLowerCase(), b.toLowerCase());
  return order === 'desc' ? -cmp : cmp;
}

/**
 * Sort a list of items by a channel-style name, using
 * {@link compareChannelNames}. `nameOf` extracts the display name from
 * each item (default: `item.name` for objects that have one).
 */
export function sortByChannelName<T>(
  items: T[],
  nameOf: (item: T) => string,
  options: ChannelNameCompareOptions = {}
): T[] {
  return [...items].sort((a, b) => compareChannelNames(nameOf(a), nameOf(b), options));
}
