/**
 * Tests for the Move Channel to Group dialog's numbering rules
 * (bead enhancedchannelmanager-gddai).
 *
 * WHAT THE DRILL SAW (2026-08-09-run18)
 *
 * Dragging a channel onto another group opened the dialog with NEITHER radio
 * checked — read off the DOM, both `<input type="radio" name="numberingOption">`
 * carried no `checked` — while the Move Channel button reported
 * `disabled=false`. Clicking it did nothing. Three times. No validation
 * message anywhere. Selecting "Keep current numbers" made the identical click
 * work instantly.
 *
 * The cause was a default of `'suggested'` for a destination group whose
 * `suggestedChannelNumber` was null (an EMPTY group), which is exactly the
 * state in which that radio is not rendered at all.
 *
 * THE INVARIANT THESE TESTS DEFEND
 *
 * "The button is enabled" and "clicking the button performs a move" are the
 * same predicate. `resolveMoveNumbering` is that predicate, and the dialog
 * derives both `disabled` and its click behaviour from this one call — so an
 * enabled button that no-ops is no longer expressible.
 */
import { describe, it, expect } from 'vitest';
import {
  DEFAULT_NUMBERING_OPTION,
  defaultNumberingOption,
  resolveMoveNumbering,
  type NumberingOption,
} from './moveChannelNumbering';
import { WHOLE_CHANNEL_NUMBER_RULE_MESSAGE } from '../utils/channelNumber';

describe('defaultNumberingOption', () => {
  it('preselects "suggested" when the destination group offers a number', () => {
    expect(defaultNumberingOption(31)).toBe('suggested');
  });

  it('falls back to an always-rendered option for an EMPTY destination group', () => {
    // The empty-group case: no suggestion, so no "suggested" radio to check.
    expect(defaultNumberingOption(null)).toBe('keep');
    expect(defaultNumberingOption(null)).toBe(DEFAULT_NUMBERING_OPTION);
  });

  it('never defaults to "custom", which starts with an empty input', () => {
    // "custom" is a legal option but a terrible default: it opens the dialog
    // in a state that cannot act until the operator types.
    expect(defaultNumberingOption(null)).not.toBe('custom');
    expect(defaultNumberingOption(1)).not.toBe('custom');
  });
});

describe('resolveMoveNumbering', () => {
  it('keeps current numbers without needing a starting number', () => {
    const result = resolveMoveNumbering('keep', null, '');
    expect(result).toEqual({ ok: true, keepCurrentNumbers: true });
  });

  it('uses the suggested number when there is one', () => {
    const result = resolveMoveNumbering('suggested', 31, '');
    expect(result).toEqual({ ok: true, keepCurrentNumbers: false, startingNumber: 31 });
  });

  it('refuses "suggested" with no suggestion, and says why', () => {
    // The exact state the drill reproduced. It used to fall through the
    // switch and silently return.
    const result = resolveMoveNumbering('suggested', null, '');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(/no channel numbers/i);
  });

  it('accepts a valid custom starting number', () => {
    const result = resolveMoveNumbering('custom', null, '7');
    expect(result).toEqual({ ok: true, keepCurrentNumbers: false, startingNumber: 7 });
  });

  it.each([
    ['empty', ''],
    ['whitespace', '   '],
    ['non-numeric', 'abc'],
    ['zero', '0'],
    ['negative', '-3'],
  ])('refuses a %s custom starting number, and says why', (_label, input) => {
    const result = resolveMoveNumbering('custom', 31, input);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
  });

  it.each(['1.5', '38.1', '1.05'])(
    'refuses the fractional custom starting number %s rather than truncating it',
    (input) => {
      // The move assigns a sequential run from this value, so it counts in
      // whole numbers. `parseInt` used to read `1.5` as `1` and report ok, so
      // the channels landed on numbers nobody asked for and nothing said so
      // (bead enhancedchannelmanager-j3pyx).
      const result = resolveMoveNumbering('custom', 31, input);
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.reason).toBe(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    },
  );

  it('never resolves to ok without something to act on', () => {
    // The whole invariant, swept: every reachable combination either carries
    // an executable instruction or an explanation. Nothing in between.
    const options: NumberingOption[] = ['keep', 'suggested', 'custom'];
    const suggestions = [null, 1, 31];
    const customs = ['', ' ', 'abc', '0', '-1', '1', '31'];

    for (const option of options) {
      for (const suggestion of suggestions) {
        for (const custom of customs) {
          const result = resolveMoveNumbering(option, suggestion, custom);
          if (result.ok) {
            const actionable =
              result.keepCurrentNumbers === true ||
              (typeof result.startingNumber === 'number' && result.startingNumber >= 1);
            expect(actionable).toBe(true);
          } else {
            expect(result.reason.length).toBeGreaterThan(0);
          }
        }
      }
    }
  });

  it('is resolvable for whatever defaultNumberingOption preselects', () => {
    // Guards the pairing directly: whichever option the dialog opens on must
    // be one the Move button can act on, for every destination shape.
    for (const suggestion of [null, 1, 31]) {
      const option = defaultNumberingOption(suggestion);
      expect(resolveMoveNumbering(option, suggestion, '').ok).toBe(true);
    }
  });
});
