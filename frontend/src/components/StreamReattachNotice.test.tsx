/**
 * Tests for StreamReattachNotice (bead d0bd3).
 *
 * Backup/restore drill run 2026-08-06-run9 restored a redacted artifact and the
 * report said, correctly, `channels_needing_stream_reattach: 12`,
 * `channels_with_no_playable_stream: 12`, with all twelve channels named in
 * `stream_reattach_details`. The restore-complete dialog rendered ONLY the
 * credentials panel and the per-category count grid — nothing anywhere on screen
 * said that not one channel could play, while playback measured 0/2, HTTP 500.
 *
 * The docs tell operators "the UI shows matching panels after a restore … use
 * these instead of re-deriving the same information by hand", so an operator who
 * did exactly that concluded the only outstanding item was a credential.
 *
 * What this pins:
 *  - the panel appears whenever a channel is left holding a placeholder;
 *  - it SEPARATES the two populations, because only one of them is broken:
 *    `has_playable_stream: false` cannot play at all, while a channel holding a
 *    leftover placeholder alongside a real stream plays fine (bead ixdaw/daziw);
 *  - it names the channels, the same way the credentials panel names accounts;
 *  - it says nothing on a clean restore, and nothing on a dry run (the preview
 *    cannot predict these counters — bead dgnms — and `null` means "not
 *    predicted", never `0`).
 */
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { StreamReattachNotice } from './StreamReattachNotice';
import type { RestoreReport, StreamReattachDetail } from '../services/api';

function report(over: Partial<RestoreReport> = {}): RestoreReport {
  return {
    contract_version: 1,
    is_dry_run: false,
    outcome: 'completed_with_failures',
    categories: [],
    logo_misses: 0,
    notes: [],
    ...over,
  } as RestoreReport;
}

function detail(over: Partial<StreamReattachDetail> = {}): StreamReattachDetail {
  return {
    channel_id: 101,
    name: 'AL | Birmingham | PBS WBIQ',
    placeholder_streams: ['PBS WBIQ (placeholder)'],
    has_playable_stream: false,
    ...over,
  };
}

/** The drill's shape: every restored channel dead, all twelve named. */
function drillReport(): RestoreReport {
  return report({
    channels_needing_stream_reattach: 12,
    channels_with_no_playable_stream: 12,
    stream_reattach_details: Array.from({ length: 12 }, (_, index) =>
      detail({ channel_id: 101 + index, name: `Channel ${index + 1}` }),
    ),
  });
}

describe('StreamReattachNotice', () => {
  it('says nothing when every restored channel plays', () => {
    render(
      <StreamReattachNotice
        report={report({
          outcome: 'success',
          channels_needing_stream_reattach: 0,
          channels_with_no_playable_stream: 0,
          stream_reattach_details: [],
        })}
      />,
    );
    expect(screen.queryByTestId('stream-reattach-notice')).not.toBeInTheDocument();
  });

  it('says nothing on a report from a build that predates the counters', () => {
    render(<StreamReattachNotice report={report({ outcome: 'success' })} />);
    expect(screen.queryByTestId('stream-reattach-notice')).not.toBeInTheDocument();
  });

  it('says nothing on a dry run, where the counters are not predicted', () => {
    // `null` means NOT PREDICTED (bead dgnms). Rendering it as a count would be
    // the confident claim that null exists to stop making.
    render(
      <StreamReattachNotice
        report={report({
          is_dry_run: true,
          outcome: null,
          channels_needing_stream_reattach: null,
          channels_with_no_playable_stream: null,
        })}
        mode="dry-run"
      />,
    );
    expect(screen.queryByTestId('stream-reattach-notice')).not.toBeInTheDocument();
  });

  it('THE drill case: says that not one channel can play, and names them', () => {
    render(<StreamReattachNotice report={drillReport()} />);

    const notice = screen.getByTestId('stream-reattach-notice');
    expect(notice.textContent).toContain('12 channels have no playable stream');
    expect(notice.getAttribute('role')).toBe('alert');

    const rows = within(notice).getAllByTestId('stream-reattach-unplayable-row');
    expect(rows).toHaveLength(12);
    expect(rows[0].textContent).toContain('Channel 1');
    expect(rows[0].textContent).toContain('101');
  });

  it('uses the singular for a single dead channel', () => {
    render(
      <StreamReattachNotice
        report={report({
          channels_needing_stream_reattach: 1,
          channels_with_no_playable_stream: 1,
          stream_reattach_details: [detail()],
        })}
      />,
    );
    expect(screen.getByTestId('stream-reattach-notice').textContent).toContain(
      '1 channel has no playable stream',
    );
  });

  it('does NOT call a channel broken when it kept a real stream', () => {
    // The designed output of the ixdaw fix: one leftover placeholder slot on a
    // channel that plays. An action item, not a failure.
    render(
      <StreamReattachNotice
        report={report({
          outcome: 'success',
          channels_needing_stream_reattach: 1,
          channels_with_no_playable_stream: 0,
          stream_reattach_details: [detail({ has_playable_stream: true })],
        })}
      />,
    );

    const notice = screen.getByTestId('stream-reattach-notice');
    expect(notice.textContent).not.toContain('no playable stream');
    expect(notice.textContent).toContain('1 channel still holds a placeholder stream');
    expect(notice.getAttribute('role')).toBe('status');
    expect(within(notice).queryAllByTestId('stream-reattach-unplayable-row')).toHaveLength(0);
    expect(within(notice).getAllByTestId('stream-reattach-holding-row')).toHaveLength(1);
  });

  it('separates the two populations when both are present', () => {
    render(
      <StreamReattachNotice
        report={report({
          channels_needing_stream_reattach: 3,
          channels_with_no_playable_stream: 1,
          stream_reattach_details: [
            detail({ channel_id: 201, name: 'Dead Channel', has_playable_stream: false }),
            detail({ channel_id: 202, name: 'Playing A', has_playable_stream: true }),
            detail({ channel_id: 203, name: 'Playing B', has_playable_stream: true }),
          ],
        })}
      />,
    );

    const notice = screen.getByTestId('stream-reattach-notice');
    const dead = within(notice).getAllByTestId('stream-reattach-unplayable-row');
    const holding = within(notice).getAllByTestId('stream-reattach-holding-row');
    expect(dead).toHaveLength(1);
    expect(dead[0].textContent).toContain('Dead Channel');
    expect(holding).toHaveLength(2);
    expect(notice.textContent).toContain('1 channel has no playable stream');
    expect(notice.textContent).toContain('2 channels still hold a placeholder stream');
  });

  it('still reports the count when the report carries no per-channel detail', () => {
    // The aggregate is the gate; the drill-down is additive, exactly as with the
    // credential and logo-miss panels.
    render(
      <StreamReattachNotice
        report={report({
          channels_needing_stream_reattach: 4,
          channels_with_no_playable_stream: 4,
        })}
      />,
    );
    const notice = screen.getByTestId('stream-reattach-notice');
    expect(notice.textContent).toContain('4 channels have no playable stream');
    expect(within(notice).queryAllByTestId('stream-reattach-unplayable-row')).toHaveLength(0);
  });

  it('treats a detail row with no has_playable_stream field as playable', () => {
    // The field is additive-optional and defaults True on the backend, so a row
    // from an older report must never be retroactively counted as dead.
    render(
      <StreamReattachNotice
        report={report({
          outcome: 'success',
          channels_needing_stream_reattach: 1,
          channels_with_no_playable_stream: 0,
          stream_reattach_details: [
            { channel_id: 301, name: 'Legacy Row', placeholder_streams: [] },
          ],
        })}
      />,
    );
    expect(
      within(screen.getByTestId('stream-reattach-notice')).getAllByTestId(
        'stream-reattach-holding-row',
      ),
    ).toHaveLength(1);
  });
});
