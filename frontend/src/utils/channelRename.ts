/**
 * The channel-name rewrite a channel-number change causes.
 *
 * Many channel names carry their own number ("5 | ESPN", "US | 5034 - DABL",
 * "ESPN | 5"), so moving the channel leaves the name lying unless the number
 * inside it moves too. The `auto_rename_channel_number` setting decides
 * whether that happens; every caller checks the setting and this module only
 * answers what the new name WOULD be.
 *
 * The three shapes are recognised by one matcher (bead
 * `enhancedchannelmanager-ic884.5`), so {@link nameCarriesChannelNumber} and
 * {@link computeAutoRename} can never disagree about whether a name has a
 * number in it. That matters because the two are used together: clearing a
 * channel number cannot rewrite the name (there is no new number to write), so
 * the operator has to be told when clearing will strand a number in the name,
 * and "does this name carry a number" has to be the same question the rename
 * itself asks.
 *
 * Tests: `channelRename.test.ts`.
 */

/** A number found inside a channel name, and how to put a different one back. */
interface EmbeddedNumber {
  /** The number exactly as it appears in the name. */
  text: string;
  /** The name with `text` replaced by `next`. */
  rebuild: (next: string) => string;
}

/**
 * The number embedded in `channelName`, or `null` when it carries none.
 *
 * The three shapes, in the order they are tried. The order is load-bearing:
 * "US | 5034 - DABL" would also match the suffix pattern on a different
 * number, so the mid pattern has to win.
 */
function matchEmbeddedNumber(channelName: string): EmbeddedNumber | null {
  // Number in the middle: "US | 5034 - DABL" or "US | 5034: DABL".
  // PREFIX | NUMBER - SUFFIX, where PREFIX does not start with a digit.
  const midMatch = channelName.match(/^([A-Za-z].+?\s*\|\s*)(\d+(?:\.\d+)?)\s*([-:]\s*.+)$/);
  if (midMatch) {
    const [, prefix, text, suffix] = midMatch;
    return { text, rebuild: (next) => `${prefix}${next} ${suffix}` };
  }

  // Number at the beginning: "123 | Name", "123 - Name", "123: Name",
  // "123 Name" — a number followed by a separator.
  const prefixMatch = channelName.match(/^(\d+(?:\.\d+)?)\s*([|\-:.\s])\s*(.*)$/);
  if (prefixMatch) {
    const [, text, separator, rest] = prefixMatch;
    const joiner = separator === ' ' ? ' ' : ` ${separator} `;
    return { text, rebuild: (next) => `${next}${joiner}${rest}` };
  }

  // Number at the end: "Channel Name | 123".
  const suffixMatch = channelName.match(/^(.*)\s*([|\-.])\s*(\d+(?:\.\d+)?)$/);
  if (suffixMatch) {
    const [, prefix, separator, text] = suffixMatch;
    return { text, rebuild: (next) => `${prefix} ${separator} ${next}` };
  }

  return null;
}

/**
 * Whether `channelName` carries a channel number that a numbering change would
 * rewrite.
 *
 * The question clearing a number has to ask: {@link computeAutoRename} answers
 * `undefined` for a cleared number because there is no number to write, which
 * is indistinguishable from "this name has no number in it" — and those two
 * cases want opposite treatment from an operator's point of view.
 */
export function nameCarriesChannelNumber(channelName: string): boolean {
  return matchEmbeddedNumber(channelName) !== null;
}

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
  const embedded = matchEmbeddedNumber(channelName);
  if (!embedded) {
    return undefined;
  }
  // Already the new number: nothing to change.
  if (embedded.text === newNumberStr) {
    return undefined;
  }
  const newName = embedded.rebuild(newNumberStr);
  return newName !== channelName ? newName : undefined;
}
