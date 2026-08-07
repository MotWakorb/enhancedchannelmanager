/**
 * Tests for ExistingChannelReattachNotice (bead dfkbn, PR review W1).
 *
 * The contract this pins is the one the PO called non-negotiable: the operator
 * must be able to see, BEFORE applying, how many of their EXISTING channels a
 * restore would overwrite versus how many links it would apply to channels the
 * restore creates. So the destructive count has to render on the dry run, from
 * the same report fields the apply populates.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ExistingChannelReattachNotice } from './ExistingChannelReattachNotice';
import type {
  ChannelGroupDriftDetail,
  ReattachPopulation,
  RestoreReport,
} from '../services/api';

function population(over: Partial<ReattachPopulation> = {}): ReattachPopulation {
  return {
    mode: 'preserve',
    created_channels: 0,
    existing_channels: 0,
    preserved_channels: 0,
    existing_channels_named: [],
    preserved_channels_named: [],
    ...over,
  };
}

function report(over: Partial<RestoreReport> = {}): RestoreReport {
  return {
    contract_version: 1,
    is_dry_run: false,
    outcome: 'success',
    categories: [],
    logo_misses: 0,
    notes: [],
    ...over,
  } as RestoreReport;
}

describe('ExistingChannelReattachNotice', () => {
  it('says nothing when every channel was created by this restore', () => {
    // The disaster-recovery case: an empty target, so both populations are zero
    // and the mode never mattered. A notice here would be pure noise.
    render(
      <ExistingChannelReattachNotice
        report={report({
          epg_link_reattach: population({ created_channels: 7 }),
          logo_reattach: population({ created_channels: 7 }),
        })}
      />,
    );
    expect(
      screen.queryByTestId('existing-channel-reattach-notice'),
    ).not.toBeInTheDocument();
  });

  it('warns on the DRY RUN about existing channels it would overwrite', () => {
    render(
      <ExistingChannelReattachNotice
        report={report({
          is_dry_run: true,
          outcome: null,
          epg_link_reattach: population({
            mode: 'overwrite',
            created_channels: 1,
            existing_channels: 2,
            existing_channels_named: ['FOX News', 'CNN'],
          }),
        })}
      />,
    );

    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice).toHaveAttribute('role', 'alert');
    // Future tense on a preview: this has not happened yet.
    expect(notice.textContent).toMatch(/would replace on/i);
    expect(notice.textContent).toMatch(/2/);
    expect(notice.textContent).toMatch(/FOX News/);
    expect(notice.textContent).toMatch(/CNN/);
  });

  it('reads as past tense once the restore has been applied', () => {
    render(
      <ExistingChannelReattachNotice
        report={report({
          logo_reattach: population({
            mode: 'overwrite',
            existing_channels: 3,
            existing_channels_named: ['A', 'B', 'C'],
          }),
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).toMatch(/replaced on/i);
    expect(notice.textContent).not.toMatch(/would replace/i);
  });

  it('reports the reassuring case without the destructive framing', () => {
    render(
      <ExistingChannelReattachNotice
        report={report({
          epg_link_reattach: population({
            created_channels: 4,
            preserved_channels: 200,
            preserved_channels_named: ['kept'],
          }),
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice).toHaveAttribute('role', 'status');
    expect(notice.textContent).toMatch(/left alone/i);
    expect(notice.textContent).toMatch(/200/);
    // No "cannot be undone" scare copy when nothing was touched.
    expect(notice.textContent).not.toMatch(/cannot bring these back/i);
  });

  it('keeps the reassuring case in FUTURE tense on a preview', () => {
    // The title used to read "were left alone" on a dry run, before anything
    // had run. A preview describing the past is not a preview.
    render(
      <ExistingChannelReattachNotice
        report={report({
          is_dry_run: true,
          outcome: null,
          epg_link_reattach: population({
            created_channels: 4,
            preserved_channels: 12,
          }),
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).toMatch(/would be left alone/i);
    expect(notice.textContent).not.toMatch(/were left alone/i);
    expect(notice.textContent).toMatch(/would leave/i);
  });

  it('points at the picker on a preview and at a re-run on the results', () => {
    // The remedy copy has to be actionable where it renders. On the results
    // step ChannelReattachModeField is unmounted, so "choose the other option"
    // points at nothing.
    const touched = {
      logo_reattach: population({
        mode: 'overwrite' as const,
        existing_channels: 2,
        existing_channels_named: ['A', 'B'],
      }),
    };

    const { unmount } = render(
      <ExistingChannelReattachNotice
        report={report({ is_dry_run: true, outcome: null, ...touched })}
      />,
    );
    expect(
      screen.getByTestId('existing-channel-reattach-notice').textContent,
    ).toMatch(/back to options/i);
    unmount();

    render(<ExistingChannelReattachNotice report={report(touched)} />);
    const applied = screen.getByTestId('existing-channel-reattach-notice');
    expect(applied.textContent).toMatch(/run the restore again/i);
    expect(applied.textContent).not.toMatch(/back to options/i);
  });

  it('says how many names it is NOT showing', () => {
    // The server caps each name list at 50 so a five-thousand-channel merge does
    // not write ten thousand names into the task record. Rendering the capped
    // list with a full stop reads as the complete set, turning a deliberate cap
    // into a wrong number on screen.
    render(
      <ExistingChannelReattachNotice
        report={report({
          is_dry_run: true,
          outcome: null,
          epg_link_reattach: population({
            mode: 'overwrite',
            existing_channels: 5000,
            existing_channels_named: Array.from({ length: 50 }, (_, i) => `Ch ${i}`),
          }),
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).toMatch(/5000/);
    expect(notice.textContent).toMatch(/and 4950 more/i);
  });

  it('shows no remainder when the list is complete', () => {
    render(
      <ExistingChannelReattachNotice
        report={report({
          logo_reattach: population({
            mode: 'overwrite',
            existing_channels: 2,
            existing_channels_named: ['A', 'B'],
          }),
        })}
      />,
    );
    expect(
      screen.getByTestId('existing-channel-reattach-notice').textContent,
    ).not.toMatch(/more/i);
  });

  it('names the preserved channels too, not just the replaced ones', () => {
    // preserved_channels_named is populated and capped server-side; leaving it
    // unrendered means paying for it and showing the operator nothing.
    render(
      <ExistingChannelReattachNotice
        report={report({
          epg_link_reattach: population({
            preserved_channels: 3,
            preserved_channels_named: ['Kept A', 'Kept B', 'Kept C'],
          }),
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).toMatch(/Kept A/);
    expect(notice.textContent).toMatch(/Kept C/);
  });

  it('renders nothing for a report predating these fields', () => {
    render(<ExistingChannelReattachNotice report={report()} />);
    expect(
      screen.queryByTestId('existing-channel-reattach-notice'),
    ).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------
  // Channel -> group membership (bead r1ei7)
  // ---------------------------------------------------------------------

  const drift = (over: Partial<ChannelGroupDriftDetail> = {}): ChannelGroupDriftDetail => ({
    name: 'ch101',
    current_group: 'Drill Movies',
    archive_group: 'Drill Sports',
    moved: false,
    ...over,
  });

  it('names the drifted channels and BOTH groups under preserve', () => {
    // Drill run 12: seven channels sat in a group the archive does not assign
    // them, in both relink modes, and the restore reported success with nothing
    // said about it. Naming only the channel would leave the operator to go and
    // diff the group lists by hand — which is what the drill had to do.
    render(
      <ExistingChannelReattachNotice
        report={report({
          channel_group_drift: 2,
          channel_group_drift_details: [
            drift(),
            drift({ name: 'ch201', current_group: 'Drill Sports RENAMED', archive_group: 'Drill Movies' }),
          ],
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).toMatch(/ch101/);
    expect(notice.textContent).toMatch(/Drill Movies/);
    expect(notice.textContent).toMatch(/Drill Sports/);
    expect(notice.textContent).toMatch(/ch201/);
    // preserve changed nothing, so this is not the destructive framing.
    expect(notice.textContent).toMatch(/left .*as (they are|it is)/i);
  });

  it('renders the drift on its own, with no reattach populations at all', () => {
    // The reattach populations are zero whenever the operator deselected logos
    // and EPG sources. The grouping finding must not disappear with them.
    render(
      <ExistingChannelReattachNotice
        report={report({ channel_group_drift: 1, channel_group_drift_details: [drift()] })}
      />,
    );
    expect(screen.getByTestId('existing-channel-reattach-notice')).toBeInTheDocument();
  });

  it('says the channels were MOVED when the mode reconciled them', () => {
    render(
      <ExistingChannelReattachNotice
        report={report({
          channel_group_drift: 1,
          channel_group_drift_details: [drift({ moved: true })],
        })}
      />,
    );
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice).toHaveAttribute('role', 'alert');
    expect(notice.textContent).toMatch(/moved/i);
    expect(notice.textContent).not.toMatch(/would move/i);
  });

  it('keeps the moves in FUTURE tense on a preview', () => {
    render(
      <ExistingChannelReattachNotice
        report={report({
          is_dry_run: true,
          outcome: null,
          channel_group_drift: 1,
          channel_group_drift_details: [drift({ moved: true })],
        })}
      />,
    );
    expect(
      screen.getByTestId('existing-channel-reattach-notice').textContent,
    ).toMatch(/would move/i);
  });

  it('quotes the relink option by its real on-screen label', () => {
    // The remedy copy names a radio the operator has to find. When the option's
    // wording changes, this sentence has to change with it or it points at a
    // control that does not exist.
    render(
      <ExistingChannelReattachNotice
        report={report({
          is_dry_run: true,
          outcome: null,
          logo_reattach: population({
            mode: 'overwrite',
            existing_channels: 1,
            existing_channels_named: ['A'],
          }),
        })}
      />,
    );
    expect(
      screen.getByTestId('existing-channel-reattach-notice').textContent,
    ).toMatch(/Keep their current guide data, logos, and grouping/);
  });
});
