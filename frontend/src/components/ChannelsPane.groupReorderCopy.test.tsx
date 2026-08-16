/**
 * Reorder Group dialog copy — "Keep current numbers" (bead
 * enhancedchannelmanager-zll44).
 *
 * Dragging a channel group always opens the Reorder Group dialog. Every
 * numbering option in it EXCEPT "Keep current numbers" stages
 * `channel_number` updates, and because the group list is re-sorted by lowest
 * channel number on load, those options really do survive Apply All and a
 * reload. "Keep current numbers" is the exception: it sets local `groupOrder`
 * state and writes nothing, so the arrangement is gone on the next page load
 * with no unsaved-changes indicator and nothing counted as an Edit Mode
 * change.
 *
 * The PO decided against persisting `groupOrder`, so the sub-label IS the fix.
 * These tests pin the three claims that make it honest, rather than pinning
 * the sentence verbatim: an operator reading it has to learn that the position
 * is not saved, that a reload is what takes it away, and that renumbering is
 * the durable alternative. Wording may be revised; those three claims may not
 * quietly disappear.
 *
 * SCOPE NOTE, deliberately stated: this suite asserts the exported constant,
 * not the rendered dialog. The dialog is reachable ONLY by completing a
 * @dnd-kit group drag, which needs real element rects that jsdom does not
 * provide, and no suite in this repository drives that library. The rendering
 * side is a one-line `<span>{KEEP_CURRENT_NUMBERS_SUBLABEL}</span>` in
 * ChannelsPane.tsx; this is a copy pin, and calling it anything more would
 * overstate what ran.
 */
import { describe, it, expect } from 'vitest';
import { KEEP_CURRENT_NUMBERS_SUBLABEL } from './ChannelsPane';

describe('Reorder Group "Keep current numbers" sub-label', () => {
  it('says the resulting group order is display-only', () => {
    expect(KEEP_CURRENT_NUMBERS_SUBLABEL.toLowerCase()).toContain('display only');
  });

  it('says the new position is not saved', () => {
    expect(KEEP_CURRENT_NUMBERS_SUBLABEL.toLowerCase()).toContain('not saved');
  });

  it('names the reload as what discards the arrangement', () => {
    expect(KEEP_CURRENT_NUMBERS_SUBLABEL.toLowerCase()).toContain('reload');
  });

  it('points at renumbering as the durable alternative', () => {
    expect(KEEP_CURRENT_NUMBERS_SUBLABEL.toLowerCase()).toContain('renumber');
  });

  it('no longer claims only that channel numbers are unchanged', () => {
    // The pre-fix copy was exactly this, and it is what made the gesture look
    // persistent: it described the numbers and said nothing about the move.
    expect(KEEP_CURRENT_NUMBERS_SUBLABEL).not.toBe("Don't change channel numbers");
  });
});
