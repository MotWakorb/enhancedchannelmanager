/**
 * What to tell the operator after a stream is dropped onto a channel group
 * (bead enhancedchannelmanager-ok8tj).
 *
 * The sibling of the `Create in…` messages that commit `941d9087` put inline
 * in `StreamsPane.tsx`, and deliberately the same vocabulary — "No duplicate
 * found", "Duplicate check unavailable", "Duplicate check skipped" — because
 * drag-drop and `Create in…` are two trigger paths into ONE feature and an
 * operator who learns what silence means on one must not have to learn it
 * again on the other.
 *
 * It lives in its own module rather than inline, unlike the `Create in…`
 * side, for one reason: `StreamsPane.test.tsx` can drive its trigger through
 * the real UI (a menu click), and nothing in the suite can drive a drag-drop
 * onto a `ChannelsPane` group header. Inline text there would be text no test
 * could reach. Composed here, every branch is asserted directly by
 * `dedupDropMessages.test.ts`.
 */
import type { DedupDropReport } from '../hooks/useDedupOnDrop';

export interface DedupDropMessage {
  /** Maps to the matching `useNotifications()` convenience method. */
  type: 'info' | 'warning';
  title: string;
  message: string;
}

/**
 * The sentence for one drop outcome, or `null` when the outcome speaks for
 * itself.
 *
 * `candidate` is the null case: the StreamDedupModal is on screen, so a toast
 * saying a duplicate was found would only repeat it. Same reasoning as the
 * `Create in…` path, where a found candidate also stays quiet.
 *
 * @param report  what the check did, from `App`'s drop handler.
 * @param groupLabel  the target group as the operator sees it, already
 *   quoted or worded by the caller (e.g. `"News"` or `the ungrouped list`).
 */
export function describeDedupDropReport(
  report: DedupDropReport,
  groupLabel: string,
): DedupDropMessage | null {
  switch (report.outcome) {
    case 'candidate':
      return null;

    case 'no_candidate':
      return {
        type: 'info',
        title: 'No duplicate found',
        message:
          `No channel in ${groupLabel} was close enough to `
          + `"${report.streamName}" to offer a merge, so a new channel will be `
          + 'created. Lower the dedup confidence threshold in Settings if you '
          + 'expected a prompt.',
      };

    case 'lookup_failed':
      return {
        type: 'warning',
        title: 'Duplicate check unavailable',
        message:
          `ECM could not check ${groupLabel} for a matching channel, so this `
          + 'stream is being created without a duplicate check.',
      };

    case 'skipped_multi_stream':
      return {
        type: 'info',
        title: 'Duplicate check skipped',
        message:
          'The duplicate check runs on a single-stream drop only, so it did '
          + `not run for these ${report.streamCount} streams.`,
      };

    case 'skipped_unknown_stream':
      return {
        type: 'info',
        title: 'Duplicate check skipped',
        message:
          "ECM could not read the dropped stream's name, so no duplicate "
          + 'check ran for this creation.',
      };
  }
}
