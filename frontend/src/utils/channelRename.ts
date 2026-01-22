/**
 * Computes a new channel name when the channel number changes.
 * Returns the new name if a channel number is detected in the name, undefined otherwise.
 * The caller is responsible for checking whether auto-rename is enabled.
 */
export function computeAutoRename(
  channelName: string,
  _oldNumber: number | null,
  newNumber: number | null
): string | undefined {
  if (newNumber === null) {
    return undefined;
  }

  const newNumberStr = String(newNumber);

  // Check for number in the middle: "US | 5034 - DABL" or "US | 5034: DABL"
  // Pattern: PREFIX | NUMBER - SUFFIX (where PREFIX doesn't start with a digit)
  const midMatch = channelName.match(/^([A-Za-z].+?\s*\|\s*)(\d+(?:\.\d+)?)\s*([-:]\s*.+)$/);
  if (midMatch) {
    const [, prefix, oldNum, suffix] = midMatch;
    // If the number is already the new number, no change needed
    if (oldNum === newNumberStr) {
      return undefined;
    }
    // Replace the number in the middle
    const newName = `${prefix}${newNumberStr} ${suffix}`;
    return newName !== channelName ? newName : undefined;
  }

  // Look for a number at the beginning of the channel name
  // Pattern: "123 | Channel Name" or "123 - Channel Name" or "123: Channel Name" or "123 Channel Name"
  // This matches a number at the start followed by a separator (space, |, -, :, .)
  const prefixMatch = channelName.match(/^(\d+(?:\.\d+)?)\s*([|\-:.\s])\s*(.*)$/);

  if (prefixMatch) {
    const [, oldPrefix, separator, rest] = prefixMatch;
    // If the prefix is already the new number, no change needed
    if (oldPrefix === newNumberStr) {
      return undefined;
    }
    // Replace the prefix with the new number
    const newName = `${newNumberStr}${separator === ' ' ? ' ' : ` ${separator} `}${rest}`;
    return newName !== channelName ? newName : undefined;
  }

  // Also check for number at the end: "Channel Name | 123"
  const suffixMatch = channelName.match(/^(.*)\s*([|\-.])\s*(\d+(?:\.\d+)?)$/);
  if (suffixMatch) {
    const [, prefix, separator, oldSuffix] = suffixMatch;
    // If the suffix is already the new number, no change needed
    if (oldSuffix === newNumberStr) {
      return undefined;
    }
    // Replace the suffix with the new number
    const newName = `${prefix} ${separator} ${newNumberStr}`;
    return newName !== channelName ? newName : undefined;
  }

  return undefined;
}
