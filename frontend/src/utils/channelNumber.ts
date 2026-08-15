/**
 * Canonical channel-number contract (bead `enhancedchannelmanager-ic884.1`).
 *
 * A channel number is a NON-NEGATIVE number with AT MOST ONE DECIMAL PLACE.
 * That is the PO's rule: Dispatcharr's channel-number precision is one
 * significant digit, so only the tenths place carries meaning.
 *
 * `null` means "unassigned" and is always allowed; a channel with no number is
 * a normal state in Dispatcharr.
 *
 * What the contract deliberately does NOT say:
 *
 *   - No uniqueness. Dispatcharr declares `channel_number` as a non-unique
 *     float and permits duplicates, and real lineups have them.
 *   - No maximum. The rule named none, and Dispatcharr stores a plain float.
 *
 * Out-of-contract input is REJECTED at the boundary, never silently rounded.
 * That is an explicit PO choice: surfacing bad data beats altering it quietly.
 *
 * This module is the frontend half of the contract. The backend half is
 * `backend/channel_number.py` and enforces the same rule on every write path,
 * so a caller that bypasses the UI still cannot store an out-of-contract value.
 * The two message strings are kept byte-identical on purpose: an operator sees
 * the same sentence whether the check fired in the browser or on the server.
 *
 * Tests: `channelNumber.test.ts`.
 */

/**
 * The one operator-facing sentence every rejection uses.
 * Keep byte-identical with `CHANNEL_NUMBER_RULE_MESSAGE` in
 * `backend/channel_number.py`.
 */
export const CHANNEL_NUMBER_RULE_MESSAGE =
  'Channel numbers must be 0 or greater and support one decimal place (for example 1.0 or 1.1).';

/** How many decimal places a channel number may carry. */
export const CHANNEL_NUMBER_DECIMAL_PLACES = 1;

/**
 * One tick per tenth, matching the grid `channelNumberShift.ts` plans on. The
 * tolerance absorbs binary-float dust only: `0.7 + 0.1` is `0.7999999999999999`
 * and has to read as the channel number `0.8`. It is fourteen orders of
 * magnitude below the `0.05` gap separating an in-contract tenth from the
 * nearest out-of-contract value, so `1.05` can never slip through it.
 */
const TENTHS = 10;
const TENTH_TOLERANCE = 1e-9;

/**
 * Text accepted before the numeric rule is applied: digits, optionally followed
 * by a decimal point and one or more digits. This admits the canonical
 * equivalents that must compare equal (`7`, `7.0`, `07`, `1.10`) and excludes
 * forms whose meaning would have to be guessed (`1e3`, `.5`, `+7`, `7.`). Sign
 * is excluded so a negative reads as out-of-contract rather than unparseable;
 * either way the operator gets the same sentence.
 */
const CHANNEL_NUMBER_TEXT = /^\d+(?:\.\d+)?$/;

/** Whether `value` is a channel number the contract can hold. `null` is not. */
export function isValidChannelNumber(value: unknown): value is number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return false;
  const scaled = value * TENTHS;
  return Math.abs(scaled - Math.round(scaled)) <= TENTH_TOLERANCE;
}

/** Outcome of parsing operator-entered text. */
export type ChannelNumberParseResult =
  | { ok: true; value: number | null }
  | { ok: false; message: string };

/**
 * Parse an operator-entered channel number.
 *
 * Empty or whitespace-only text means "unassigned" and yields `null` when
 * `allowEmpty` (the default). Anything else must both look like a plain
 * decimal number and satisfy the numeric contract. Nothing here rounds: an
 * out-of-contract value comes back as a rejection carrying
 * `CHANNEL_NUMBER_RULE_MESSAGE`.
 */
export function parseChannelNumberInput(
  text: string,
  options: { allowEmpty?: boolean } = {},
): ChannelNumberParseResult {
  const { allowEmpty = true } = options;
  const trimmed = (text ?? '').trim();
  if (!trimmed) {
    return allowEmpty ? { ok: true, value: null } : { ok: false, message: CHANNEL_NUMBER_RULE_MESSAGE };
  }
  if (!CHANNEL_NUMBER_TEXT.test(trimmed)) {
    return { ok: false, message: CHANNEL_NUMBER_RULE_MESSAGE };
  }
  const parsed = Number(trimmed);
  if (!isValidChannelNumber(parsed)) {
    return { ok: false, message: CHANNEL_NUMBER_RULE_MESSAGE };
  }
  return { ok: true, value: parsed };
}

/**
 * The rejection message for `text`, or `null` when it is acceptable. Convenience
 * for render-time field validation, where the component wants a message to show
 * under an input rather than a parsed value.
 */
export function channelNumberInputError(
  text: string,
  options: { allowEmpty?: boolean } = {},
): string | null {
  const result = parseChannelNumberInput(text, options);
  return result.ok ? null : result.message;
}
