import { naturalCompare } from './naturalSort';

/**
 * Category bucket for groups with no recognizable prefix delimiter.
 */
export const OTHER_CATEGORY = 'Other';

// Prefix delimiters observed in live provider stream-group naming
// conventions (bead enhancedchannelmanager-09x38.5). The bead's written
// spec called out `|` only (e.g. "CA| Documentary", "Sports| PPV"), but a
// field-value survey of the live /api/stream-groups response (137 groups,
// captured 2026-07-17) showed a second, equally common convention using
// `:` (e.g. "UK: SPORTS [1080p]", "USA: ESPN PLUS [1080p]", "CAN: KIDS
// [1080p]"). Restricting to `|` alone would dump ~35 of the 137 live groups
// into "Other" for no good reason -- both delimiters mark the same kind of
// provider-prefix convention, so both are honored here. Whichever delimiter
// appears first in the name wins; the token before it (trimmed) is the
// category. This does NOT normalize semantically-equivalent prefixes (e.g.
// "US" / "USA" / "CA" / "CAN" all remain distinct categories) -- categories
// are derived purely from the literal text, never a hardcoded country list.
const CATEGORY_DELIMITERS = ['|', ':'] as const;

/**
 * Derive the category name for a stream group from its naming-convention
 * prefix -- the token before the earliest `|` or `:` delimiter, trimmed.
 * Groups with no delimiter (or an empty prefix, e.g. a name starting with
 * the delimiter itself) fall into the `OTHER_CATEGORY` bucket.
 */
export function extractGroupCategory(groupName: string): string {
  let earliestIndex = -1;
  for (const delimiter of CATEGORY_DELIMITERS) {
    const idx = groupName.indexOf(delimiter);
    if (idx !== -1 && (earliestIndex === -1 || idx < earliestIndex)) {
      earliestIndex = idx;
    }
  }

  if (earliestIndex <= 0) {
    return OTHER_CATEGORY;
  }

  const prefix = groupName.slice(0, earliestIndex).trim();
  return prefix.length > 0 ? prefix : OTHER_CATEGORY;
}

export interface CategorizedStreamGroups<T> {
  category: string;
  groups: T[];
}

/**
 * Bucket a list of stream groups (or anything with a `.name`) under their
 * derived category, sorted naturally by category name with `OTHER_CATEGORY`
 * always last regardless of where it would otherwise sort alphabetically.
 * Relative order of groups within a category is preserved from the input.
 */
export function categorizeStreamGroups<T extends { name: string }>(
  groups: T[]
): CategorizedStreamGroups<T>[] {
  const byCategory = new Map<string, T[]>();

  for (const group of groups) {
    const category = extractGroupCategory(group.name);
    const bucket = byCategory.get(category);
    if (bucket) {
      bucket.push(group);
    } else {
      byCategory.set(category, [group]);
    }
  }

  return Array.from(byCategory.entries())
    .map(([category, categoryGroups]) => ({ category, groups: categoryGroups }))
    .sort((a, b) => {
      if (a.category === OTHER_CATEGORY) return 1;
      if (b.category === OTHER_CATEGORY) return -1;
      return naturalCompare(a.category, b.category);
    });
}
