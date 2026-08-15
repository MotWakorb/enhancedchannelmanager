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
 *   - No maximum among representable numbers. The rule named none, and
 *     Dispatcharr stores a plain float. An absurd-but-finite value such as
 *     `1e308` is therefore in contract: it is an exact integer (every float at
 *     or above 2**53 is), so it carries no fractional part. `NaN` and
 *     `Infinity` are not finite and are rejected.
 *
 * The one limit is representability rather than magnitude. Python's `int` is
 * arbitrary precision, so a JSON body reaching the backend can carry `10**400`,
 * which has no float representation. Both halves reject it: here it has already
 * become `Infinity` by the time it is a `number`, and `backend/channel_number.py`
 * answers `false` rather than raising out of `float()`. A channel number has to
 * survive JSON -> `number` -> Dispatcharr's float column, and that value
 * survives none of it.
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
 * One tick per tenth, matching the grid `channelNumberShift.ts` plans on.
 *
 * What the tolerance guarantees, and over what range. It is compared against
 * `value * 10`, so in value terms it admits a deviation of `1e-10`.
 *
 *   - Reject side. The nearest out-of-contract value to a tenth is half a tick
 *     away, which is `0.5` once scaled -- a margin of 5e8 over the tolerance,
 *     so `1.05` can never slip through. Measured: `10**n + 0.05` scales to a
 *     deviation of exactly `0.5` and is rejected for every n from 0 to 14. That
 *     discrimination holds up to `2**48`; at `2**49` float spacing is `0.125`,
 *     which exceeds the `0.05` half-tick, so `base + 0.05` is not a distinct
 *     float and there is no two-decimal value left to reject.
 *   - Accept side. The tolerance is not what makes ordinary numbers pass. For
 *     every one-decimal value `k / 10` below `EXACT_INTEGER_FLOOR`, scaling
 *     back by ten returns `k` exactly: measured deviation `0.0` over 3.4M
 *     sampled values across all seventeen decades, plus exhaustively over
 *     `0.0`-`200.0`. Both producers of channel numbers draw from that
 *     population -- parsed input (`Number('10000000.1')`) and planned input
 *     (`channelNumberShift.ts` divides integer ticks by ten once, at the end).
 *   - Dust. The tolerance therefore earns its keep on one case only:
 *     arithmetic drift, such as `0.7 + 0.1` being `0.7999999999999999`, which
 *     has to read as the channel number `0.8`. Being absolute, the dust it
 *     absorbs shrinks with magnitude -- about 4.5e5 float steps at magnitude 1,
 *     about 7 at `1e5`, and less than one above roughly `1e6`. Above that the
 *     predicate accepts only values that round-trip exactly, which is what the
 *     two producers deliver. A magnitude-relative tolerance was measured as the
 *     alternative and is worse where it matters: at 8 float steps it accepts
 *     `1e14 + 0.05`, a real two-decimal value that must be rejected.
 *
 * Keep this reasoning in step with `_TENTH_TOLERANCE` in
 * `backend/channel_number.py`, which carries the same numbers.
 */
const TENTHS = 10;
const TENTH_TOLERANCE = 1e-9;

/**
 * Every float at or above `2**53` is an exact integer, because the gap between
 * adjacent floats there is at least 2. Such a value carries no fractional part,
 * so it is in contract under a rule that names no maximum, and the check can
 * short-circuit before any arithmetic. That is also what keeps the scaling
 * safe: the largest value reaching it is just under `2**53`, and `2**53 * 10`
 * is about `9.0e16`, far below `Number.MAX_VALUE`, so nothing overflows.
 */
const EXACT_INTEGER_FLOOR = 2 ** 53;

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
  // An exact integer: no fractional part to check, and short-circuiting here is
  // what makes the scaling below overflow-proof.
  if (value >= EXACT_INTEGER_FLOOR) return true;
  // Scale the WHOLE value, not just its fractional part. Scaling only the
  // fraction looks like the safer way to dodge the overflow near
  // Number.MAX_VALUE, but it discards the precision that makes an ordinary
  // large channel number work: `10000000.1 % 1` is `0.09999999962747097`, which
  // scales to `0.9999999962747097` and misses a whole tenth by far more than
  // the tolerance, so `10000000.1` reads as out of contract. Scaling the whole
  // value returns `100000001` exactly. The magnitude guard above supplies the
  // overflow safety instead, so both properties hold at once.
  //
  // `Math.round` breaks ties upward while Python's `round` goes to even, so the
  // two halves pick different integers for an exact `.5` such as `1.05 * 10`.
  // The distance is `0.5` either way, so the comparison agrees regardless.
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

/**
 * The one operator-facing sentence every renumber-start rejection uses
 * (bead `enhancedchannelmanager-j3pyx`).
 *
 * A renumber START is a narrower thing than a channel number. Sort and
 * Renumber, Renumber All Groups and its per-group overrides, the group reorder
 * custom number and the cross-group move custom number all seed a SEQUENTIAL
 * run: the field's value is followed by that value plus one, plus two, and so
 * on. Counting in tenths is not a thing any of those operations does, so the
 * field is whole-number-only by design and its output is a strict subset of the
 * canonical contract above.
 *
 * They used to read the field with `parseInt` and guard `>= 1`, which meant a
 * typed `1.5` became `1` with no warning anywhere: the operator asked for one
 * thing and got another. That is the silent normalisation the PO ruled against
 * when choosing reject-over-normalize for the canonical contract, so these
 * fields reject instead, in the same voice and with one sentence for every
 * site.
 */
export const WHOLE_CHANNEL_NUMBER_RULE_MESSAGE =
  'Starting channel numbers must be whole numbers of 1 or greater (for example 1 or 100).';

/**
 * Parse an operator-entered renumber START.
 *
 * Layered on `parseChannelNumberInput` rather than re-deriving the text rule,
 * so the two agree on what a number even looks like (`7`, `7.0` and `07` all
 * read as seven; `1e3`, `.5` and `7.` are not numbers here either). On top of
 * that this adds the two rules the sequential renumbering needs:
 *
 *   - Whole. `1.5` is refused rather than truncated to `1`.
 *   - At least 1, matching the `>= 1` guard every one of these fields already
 *     carried. Channel number 0 is in contract, but no renumber dialog has ever
 *     offered it as a start and widening that is a separate product question.
 *
 * `Number.isSafeInteger` rather than `Number.isInteger`: the run is built by
 * repeatedly adding one, and above `2**53` that addition stops producing
 * distinct numbers, so a start there could not seed the run the operator asked
 * for. Empty text yields `null` ("nothing entered") when `allowEmpty`, which is
 * the default and is what lets a field render no error until something is
 * typed; the Renumber All per-group overrides also read an empty entry as
 * "inherit from the running position" rather than as a value.
 */
export function parseWholeChannelNumberInput(
  text: string,
  options: { allowEmpty?: boolean } = {},
): ChannelNumberParseResult {
  const { allowEmpty = true } = options;
  const trimmed = (text ?? '').trim();
  if (!trimmed) {
    return allowEmpty
      ? { ok: true, value: null }
      : { ok: false, message: WHOLE_CHANNEL_NUMBER_RULE_MESSAGE };
  }
  const parsed = parseChannelNumberInput(trimmed, { allowEmpty: false });
  if (!parsed.ok || parsed.value === null) {
    return { ok: false, message: WHOLE_CHANNEL_NUMBER_RULE_MESSAGE };
  }
  if (!Number.isSafeInteger(parsed.value) || parsed.value < 1) {
    return { ok: false, message: WHOLE_CHANNEL_NUMBER_RULE_MESSAGE };
  }
  return { ok: true, value: parsed.value };
}

/**
 * The rejection message for a renumber START, or `null` when it is acceptable.
 * The render-time counterpart of `parseWholeChannelNumberInput`, mirroring
 * `channelNumberInputError`.
 */
export function wholeChannelNumberInputError(
  text: string,
  options: { allowEmpty?: boolean } = {},
): string | null {
  const result = parseWholeChannelNumberInput(text, options);
  return result.ok ? null : result.message;
}
