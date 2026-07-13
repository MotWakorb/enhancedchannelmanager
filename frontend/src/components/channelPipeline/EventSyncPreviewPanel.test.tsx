/**
 * Tests for the Event Sync preview panel (bead ti939.1.5 — Phase 1A).
 *
 * Pins: band rendering as text label + icon (never color alone), the
 * reconciling summary line, pre-flight failure surfacing, the distinct
 * unmatched list and parse-failure panel, and — the phase's hard
 * constraint — the ABSENCE of any apply/attach control.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EventSyncPreviewPanel } from './EventSyncPreviewPanel';
import type { EventSyncPreviewResponse } from '../../types/eventSync';

function buildPreview(overrides: Partial<EventSyncPreviewResponse> = {}): EventSyncPreviewResponse {
  return {
    preflight: { ok: true, failures: [] },
    summary: {
      secondary_streams: 54,
      would_attach: 42,
      ambiguous_skipped: 8,
      unmatched: 3,
      parse_failed: 1,
      master_channels: 12,
      master_channels_unparsed: 2,
      would_attach_via_review: 0,
      candidates_pending_review: 0,
    },
    streams: [
      {
        stream_id: 101,
        stream_name: 'Fubo Sports Network 07 : Yankees vs Red Sox @ 11 Jul 06:00 PM ET',
        group_id: 34,
        provider: 'Provider B',
        parsed_title: 'Yankees vs Red Sox',
        parsed_start: '2026-07-11T18:00:00-04:00',
        matched_pattern: 'slot-title-day-first-date',
        disposition: 'would_attach',
        unmatchable_reason: null,
        attach_source: 'threshold',
        would_attach_master: { channel_id: 7, name: 'Peacock 14: Yankees v Red Sox @ 11 Jul 06:00 PM ET' },
        candidates: [
          {
            master_channel_name: 'Peacock 14: Yankees v Red Sox @ 11 Jul 06:00 PM ET',
            master_channel_id: 7,
            master_parsed_title: 'Yankees v Red Sox',
            master_parsed_start: '2026-07-11T18:00:00-04:00',
            score: 0.9412,
            band: 'attach',
            team_verdict: 'agree',
            time_delta_minutes: 0,
            reject_reason: null,
            review_status: null,
          },
          {
            master_channel_name: 'Peacock 09: Mets vs Braves @ 11 Jul 06:10 PM ET',
            master_channel_id: 9,
            master_parsed_title: 'Mets vs Braves',
            master_parsed_start: '2026-07-11T18:10:00-04:00',
            score: 0.0,
            band: 'reject',
            team_verdict: 'conflict',
            time_delta_minutes: 10,
            reject_reason: 'team_token_conflict',
            review_status: null,
          },
        ],
      },
    ],
    unmatched_streams: [
      {
        stream_id: 102,
        stream_name: 'Provider B Exclusive Fight @ 11 Jul 09:00 PM ET',
        group_id: 34,
        provider: 'Provider B',
        parsed_title: 'Provider B Exclusive Fight',
        parsed_start: '2026-07-11T21:00:00-04:00',
        best_candidate: null,
      },
    ],
    parse_failures: [
      {
        group_id: 56,
        group_name: 'Provider C Events',
        reason: 'no_pattern_matched',
        count: 1,
        stream_names: ['???: mystery name'],
      },
    ],
    unparsed_master_channels: ['Peacock TBA slot'],
    truncated: false,
    ...overrides,
  };
}

describe('EventSyncPreviewPanel', () => {
  it('renders the band as a text label with an icon, never color alone', () => {
    render(
      <EventSyncPreviewPanel
        preview={buildPreview()}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    const attachBadge = screen.getByLabelText('Confidence band: Attach');
    expect(attachBadge).toHaveTextContent('Attach');
    expect(within(attachBadge).getByText('check_circle')).toHaveAttribute('aria-hidden', 'true');

    const rejectBadge = screen.getByLabelText('Confidence band: Reject');
    expect(rejectBadge).toHaveTextContent('Reject');
    expect(within(rejectBadge).getByText('cancel')).toHaveAttribute('aria-hidden', 'true');

    // Team-token verdicts render as text too
    expect(screen.getByText('Team conflict (hard reject)')).toBeInTheDocument();
    expect(screen.getByText('team_token_conflict')).toBeInTheDocument();
  });

  it('renders the reconciling summary line', () => {
    render(
      <EventSyncPreviewPanel
        preview={buildPreview()}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    expect(screen.getByTestId('event-sync-summary')).toHaveTextContent(
      '42 would attach, 8 ambiguous (skipped), 3 unmatched, 1 parse failure'
    );
    expect(screen.getByTestId('event-sync-summary')).toHaveTextContent(
      '12 master channels (2 unparsed)'
    );
  });

  it('shows raw provider names side by side with parsed identities', () => {
    render(
      <EventSyncPreviewPanel
        preview={buildPreview()}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    const cards = screen.getByRole('list', { name: 'Match results' });
    const card = within(cards).getAllByRole('listitem')[0];
    expect(within(card).getByText('Secondary stream')).toBeInTheDocument();
    expect(within(card).getByText('Master channel (would attach)')).toBeInTheDocument();
    expect(
      within(card).getByText('Fubo Sports Network 07 : Yankees vs Red Sox @ 11 Jul 06:00 PM ET')
    ).toBeInTheDocument();
    expect(within(card).getByText('Yankees vs Red Sox')).toBeInTheDocument();
    expect(within(card).getByText('Yankees v Red Sox')).toBeInTheDocument();
  });

  it('surfaces pre-flight failures with the teaching message without hiding results', () => {
    render(
      <EventSyncPreviewPanel
        preview={buildPreview({
          preflight: {
            ok: false,
            failures: [
              {
                group_id: 12,
                role: 'master',
                check: 'master_auto_sync_on',
                expected: 'auto_channel_sync ON',
                got: 'auto_channel_sync OFF',
                message: 'Master group 12 has auto_channel_sync OFF in Dispatcharr.',
              },
            ],
          },
        })}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    const preflight = screen.getByTestId('event-sync-preflight');
    expect(preflight).toHaveTextContent('Master group 12 has auto_channel_sync OFF');
    expect(preflight).toHaveTextContent('Expected auto_channel_sync ON; got auto_channel_sync OFF.');
    // Results still render alongside the failure
    expect(screen.getByTestId('event-sync-summary')).toBeInTheDocument();
  });

  it('renders distinct unmatched and parse-failure panels', () => {
    render(
      <EventSyncPreviewPanel
        preview={buildPreview()}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    const unmatched = screen.getByTestId('event-sync-unmatched');
    expect(unmatched).toHaveTextContent('Unmatched secondary streams (1)');
    expect(unmatched).toHaveTextContent('Provider B Exclusive Fight @ 11 Jul 09:00 PM ET');
    expect(unmatched).toHaveTextContent('None in time window');

    const failures = screen.getByTestId('event-sync-parse-failures');
    expect(failures).toHaveTextContent('Provider C Events');
    expect(failures).toHaveTextContent('1 stream (no_pattern_matched)');
    expect(failures).toHaveTextContent('???: mystery name');
  });

  it('has NO apply or attach control anywhere (Phase 1A hard constraint)', () => {
    render(
      <EventSyncPreviewPanel
        preview={buildPreview()}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: /apply|attach/i })).toBeNull();
    // The only buttons are the preview trigger itself (and paging when present)
    const buttons = screen.getAllByRole('button');
    expect(buttons.map(b => b.textContent)).toEqual([
      expect.stringMatching(/preview matches/i),
    ]);
  });

  it('runs the preview callback from the button', async () => {
    const user = userEvent.setup();
    const onRunPreview = vi.fn();
    render(
      <EventSyncPreviewPanel
        preview={null}
        loading={false}
        error={null}
        onRunPreview={onRunPreview}
      />
    );

    await user.click(screen.getByRole('button', { name: /preview matches/i }));
    expect(onRunPreview).toHaveBeenCalledTimes(1);
  });

  it('blocks the preview button with the reason when the config is incomplete', () => {
    render(
      <EventSyncPreviewPanel
        preview={null}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
        disabledReason="Pick a master group first"
      />
    );

    expect(screen.getByRole('button', { name: /preview matches/i })).toBeDisabled();
    expect(screen.getByText('Pick a master group first')).toBeInTheDocument();
  });

  it('marks review-queue state on candidates and queue-driven attaches (ti939.3.2)', () => {
    const preview = buildPreview();
    preview.summary.would_attach_via_review = 1;
    preview.summary.candidates_pending_review = 1;
    preview.streams[0].attach_source = 'review_queue';
    preview.streams[0].candidates[0].review_status = 'accepted';
    preview.streams[0].candidates[1].review_status = 'pending';

    render(
      <EventSyncPreviewPanel
        preview={preview}
        loading={false}
        error={null}
        onRunPreview={vi.fn()}
      />
    );

    // Summary line carries the queue context.
    const summary = screen.getByTestId('event-sync-summary');
    expect(summary).toHaveTextContent('1 via review-queue accept');
    expect(summary).toHaveTextContent('1 pairing pending review');

    // The would-attach card is flagged as decision-driven, not score-driven.
    expect(screen.getByText('Via review-queue accept')).toBeInTheDocument();

    // Per-candidate markers render as text + icon in the Review column.
    expect(screen.getByText('Accepted (auto-attaches)')).toBeInTheDocument();
    expect(screen.getByText('Pending review')).toBeInTheDocument();
  });

  describe('S5 provenance chips (bead sf8dj)', () => {
    it('renders a chip per matched_via entry with a caution title', () => {
      const preview = buildPreview();
      preview.streams[0].matched_via = [
        { key: 'time_window_ignored', label: 'time ignored' },
        { key: 'assume_current_date', label: 'assumed date' },
      ];
      render(
        <EventSyncPreviewPanel
          preview={preview}
          loading={false}
          error={null}
          onRunPreview={vi.fn()}
        />
      );

      const timeChip = screen.getByText('time ignored');
      expect(timeChip).toHaveClass('badge', 'badge-sm');
      expect(timeChip).toHaveAttribute('title', expect.stringMatching(/window gate is off/i));
      expect(screen.getByText('assumed date')).toHaveAttribute(
        'title',
        expect.stringMatching(/assumed to be today/i)
      );
    });

    it('renders no provenance chip on a plain default-threshold match', () => {
      render(
        <EventSyncPreviewPanel
          preview={buildPreview()}
          loading={false}
          error={null}
          onRunPreview={vi.fn()}
        />
      );

      expect(screen.queryByText('time ignored')).toBeNull();
      expect(screen.queryByText('assumed date')).toBeNull();
      expect(screen.queryByText('low threshold')).toBeNull();
      expect(screen.queryByText('master-from-stream')).toBeNull();
    });
  });
});
