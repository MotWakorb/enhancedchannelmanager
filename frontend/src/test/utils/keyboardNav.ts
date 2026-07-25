/**
 * Shared keyboard-navigation test helper.
 *
 * Extracted from StreamsPane.test.tsx (bead enhancedchannelmanager-zwhw4)
 * during bead enhancedchannelmanager-s8xpd, which needed the identical
 * "tab until you land on the real target" proof for ChannelsPane's
 * keyboard-only selection flow. Kept in one place so both panes' tests stay
 * in lockstep instead of drifting copies.
 */
import type userEvent from '@testing-library/user-event';

/**
 * Presses Tab (or Shift+Tab) until `isTarget` reports the focused element,
 * failing loudly if the target is never reached -- proves the target is in
 * the document tab order, not just programmatically focusable.
 */
export async function tabUntil(
  user: ReturnType<typeof userEvent.setup>,
  isTarget: () => boolean,
  { shift = false, max = 100 }: { shift?: boolean; max?: number } = {},
): Promise<void> {
  for (let i = 0; i < max; i++) {
    if (isTarget()) return;
    await user.tab({ shift });
  }
  throw new Error('tabUntil: target element never received focus');
}
