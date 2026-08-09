/**
 * Numbering rules for the Channels pane's "Move Channel to Group" dialog
 * (bead enhancedchannelmanager-gddai).
 *
 * Split out of `ChannelsPane.tsx` so both the dialog and its tests can reach
 * the rules without rendering the pane.
 */

/**
 * Numbering choices offered by the Move Channel to Group dialog.
 *
 * `'suggested'` is only OFFERED when the destination group already holds a
 * numbered channel — moving into an EMPTY group leaves `suggestedChannelNumber`
 * null and that radio is not rendered at all. Defaulting the selection to
 * `'suggested'` regardless produced a dialog with no radio checked, an ENABLED
 * Move button, and a click that fell through every case and did nothing, three
 * times, with no message anywhere (drill run 2026-08-09-run18).
 */
export type NumberingOption = 'keep' | 'suggested' | 'custom';

/** Safe default when no destination is known yet: always a rendered option. */
export const DEFAULT_NUMBERING_OPTION: NumberingOption = 'keep';

/** The option to preselect for a destination whose suggestion may be absent. */
export function defaultNumberingOption(suggestedChannelNumber: number | null): NumberingOption {
  return suggestedChannelNumber !== null ? 'suggested' : DEFAULT_NUMBERING_OPTION;
}

/**
 * What the Move button will actually do, or why it cannot act.
 *
 * Single source of truth for both the button's `disabled` state and its click
 * handler, so "enabled" and "will do something" cannot drift apart again. Every
 * `ok: false` carries operator-readable prose, because a disabled control with
 * no explanation is the defect this replaced, one step milder.
 */
export type MoveNumberingResolution =
  | { ok: true; keepCurrentNumbers: true; startingNumber?: undefined }
  | { ok: true; keepCurrentNumbers: false; startingNumber: number }
  | { ok: false; reason: string };

export function resolveMoveNumbering(
  option: NumberingOption,
  suggestedChannelNumber: number | null,
  customStartingNumber: string
): MoveNumberingResolution {
  switch (option) {
    case 'keep':
      return { ok: true, keepCurrentNumbers: true };
    case 'suggested':
      if (suggestedChannelNumber === null) {
        return {
          ok: false,
          reason: 'This group has no channel numbers to continue from. Pick another option.',
        };
      }
      return { ok: true, keepCurrentNumbers: false, startingNumber: suggestedChannelNumber };
    case 'custom': {
      const customNumber = parseInt(customStartingNumber, 10);
      if (isNaN(customNumber) || customNumber < 1) {
        return { ok: false, reason: 'Enter a starting channel number of 1 or higher.' };
      }
      return { ok: true, keepCurrentNumbers: false, startingNumber: customNumber };
    }
  }
}
