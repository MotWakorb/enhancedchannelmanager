/**
 * Unit tests for EventSyncReviewQueue (bead ti939.3.2) — the operator
 * review surface for ambiguous event_sync pairings.
 *
 * Locks the contract:
 *   - Renders one card per EventSyncReviewRecord from
 *     GET /api/event-sync-reviews?status=pending, with PER-CANDIDATE
 *     evidence visible: both raw names, parsed titles/starts, score, band,
 *     team-token verdict, time delta — never just an aggregate number.
 *   - Empty state explains where rows come from.
 *   - Accept calls POST /{id}/accept, removes the card (and superseded
 *     siblings sharing the stream fingerprint), and surfaces the
 *     attach/deferred outcome in a status banner.
 *   - Reject calls POST /{id}/reject and removes the card.
 *   - Backend errors surface inline; the card stays for retry.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { EventSyncReviewQueue } from './EventSyncReviewQueue';
import type { EventSyncReviewRecord } from '../../types/eventSync';
import * as api from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    getEventSyncReviews: vi.fn(),
    acceptEventSyncReview: vi.fn(),
    rejectEventSyncReview: vi.fn(),
    bulkDiscardEventSyncReviews: vi.fn(),
    createEventSyncExclusion: vi.fn(),
  };
});

function makeRecord(
  overrides: Partial<EventSyncReviewRecord> = {},
): EventSyncReviewRecord {
  return {
    id: 1,
    rule_id: 3,
    provider_id: 7,
    stream_name_hash: 'a'.repeat(64),
    event_key: 'fury vs usyk prelims|2026-07-12T00:00:00+00:00',
    status: 'pending',
    created_at: 1_752_300_000_000,
    last_seen_at: 1_752_300_000_000,
    resolved_at: null,
    resolution_source: null,
    evidence: {
      rule_name: 'Event Sync PPV',
      stream_name: 'BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET',
      provider: 'BoxProvider',
      stream_id: 4242,
      stream_parsed_title: 'Fury vs. Usyk',
      stream_parsed_start: '2026-07-11T20:00:00-04:00',
      master_channel_name: 'PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET',
      master_channel_id: 501,
      master_parsed_title: 'Fury vs. Usyk Prelims',
      master_parsed_start: '2026-07-11T20:00:00-04:00',
      score: 1.0,
      band: 'attach',
      team_verdict: 'agree',
      time_delta_minutes: 0,
      ambiguous_reason: 'contested_top_candidates',
    },
    ...overrides,
  };
}

function mockList(reviews: EventSyncReviewRecord[]) {
  vi.mocked(api.getEventSyncReviews).mockResolvedValue({
    reviews,
    total: reviews.length,
    page: 1,
    page_size: 50,
    total_pages: reviews.length ? 1 : 0,
  });
}

describe('EventSyncReviewQueue — evidence rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders per-candidate evidence, not just an aggregate score', async () => {
    mockList([makeRecord()]);
    render(<EventSyncReviewQueue />);

    // Both raw names side by side.
    expect(
      await screen.findByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
    // Parsed identities.
    expect(screen.getByText('Fury vs. Usyk')).toBeInTheDocument();
    expect(screen.getByText('Fury vs. Usyk Prelims')).toBeInTheDocument();
    // Score + band + team verdict + time delta as text (never color alone).
    expect(screen.getByText('Score 1.00')).toBeInTheDocument();
    expect(screen.getByText('Attach band')).toBeInTheDocument();
    expect(screen.getByText('Teams agree')).toBeInTheDocument();
    expect(screen.getByText('Start delta 0 min')).toBeInTheDocument();
    // Contested marker.
    expect(screen.getByText('Contested between masters')).toBeInTheDocument();
    // Count badge.
    expect(screen.getByTestId('event-sync-review-count')).toHaveTextContent('1');
  });

  it('shows the empty state when the queue is drained', async () => {
    mockList([]);
    render(<EventSyncReviewQueue />);
    expect(
      await screen.findByText('No pairings awaiting review'),
    ).toBeInTheDocument();
  });

  it('warns on contested rows that rejecting may auto-attach the sibling (bead 8rzkq)', async () => {
    mockList([makeRecord()]); // default record is contested
    render(<EventSyncReviewQueue />);
    expect(
      await screen.findByText(/rejecting this pairing may let the other/i),
    ).toBeInTheDocument();
  });

  it('omits the contested warning on a non-contested (band) ambiguity', async () => {
    const rec = makeRecord();
    mockList([{
      ...rec,
      evidence: { ...rec.evidence, ambiguous_reason: 'top_candidate_ambiguous_band' },
    }]);
    render(<EventSyncReviewQueue />);
    await screen.findByText('Fury vs. Usyk'); // wait for the card to render
    expect(
      screen.queryByText(/rejecting this pairing may let the other/i),
    ).not.toBeInTheDocument();
  });
});

describe('EventSyncReviewQueue — accept', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('accepts a pairing, removes superseded siblings, reports the attach', async () => {
    const winner = makeRecord({ id: 1 });
    const sibling = makeRecord({
      id: 2,
      event_key: 'fury vs usyk|2026-07-12T00:00:00+00:00',
      evidence: {
        ...makeRecord().evidence,
        master_channel_name: 'PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET',
      },
    });
    const unrelated = makeRecord({
      id: 3,
      stream_name_hash: 'b'.repeat(64),
      evidence: {
        ...makeRecord().evidence,
        stream_name: 'BOX HD: Canelo vs. Crawford @ 11 Jul 09:00 PM ET',
        master_channel_name: 'PPV 03: Canelo vs. Crawford @ 11 Jul 09:00 PM ET',
      },
    });
    mockList([winner, sibling, unrelated]);
    vi.mocked(api.acceptEventSyncReview).mockResolvedValue({
      status: 'accepted',
      attached: true,
      already_attached: false,
      attach_deferred_reason: null,
      superseded_siblings: 1,
    });

    render(<EventSyncReviewQueue />);
    const acceptButtons = await screen.findAllByRole('button', {
      name: /accept & attach/i,
    });
    fireEvent.click(acceptButtons[0]);

    await waitFor(() => {
      expect(api.acceptEventSyncReview).toHaveBeenCalledWith(1);
    });
    // Winner + fingerprint sibling removed; unrelated stream stays.
    await waitFor(() => {
      expect(
        screen.queryByText('PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET'),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.queryByText('PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('PPV 03: Canelo vs. Crawford @ 11 Jul 09:00 PM ET'),
    ).toBeInTheDocument();
    // Outcome banner reports the attach and the durable decision.
    expect(screen.getByRole('status')).toHaveTextContent(/stream attached/i);
  });

  it('reports a deferred attach without failing the accept', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.acceptEventSyncReview).mockResolvedValue({
      status: 'accepted',
      attached: false,
      already_attached: false,
      attach_deferred_reason:
        'snapshot stream id no longer resolves (provider refresh)',
      superseded_siblings: 0,
    });

    render(<EventSyncReviewQueue />);
    fireEvent.click(
      await screen.findByRole('button', { name: /accept & attach/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/attach deferred/i);
    });
    expect(screen.getByRole('status')).toHaveTextContent(
      /snapshot stream id no longer resolves/i,
    );
  });

  it('surfaces accept errors inline and keeps the card', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.acceptEventSyncReview).mockRejectedValue(
      new Error('review is already rejected'),
    );

    render(<EventSyncReviewQueue />);
    fireEvent.click(
      await screen.findByRole('button', { name: /accept & attach/i }),
    );

    expect(
      await screen.findByText('review is already rejected'),
    ).toBeInTheDocument();
    // Card stays for retry.
    expect(
      screen.getByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
  });
});

describe('EventSyncReviewQueue — reject', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('rejects a pairing and removes the card', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.rejectEventSyncReview).mockResolvedValue({
      status: 'rejected',
    });

    render(<EventSyncReviewQueue />);
    fireEvent.click(
      await screen.findByRole('button', { name: /reject pairing/i }),
    );

    await waitFor(() => {
      expect(api.rejectEventSyncReview).toHaveBeenCalledWith(1);
    });
    await waitFor(() => {
      expect(
        screen.queryByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole('status')).toHaveTextContent(/never attach/i);
  });
});

describe('EventSyncReviewQueue — never attach (bead ti939.3.5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('creates a fingerprint-keyed exclusion, then rejects the row', async () => {
    const record = makeRecord();
    mockList([record]);
    vi.mocked(api.createEventSyncExclusion).mockResolvedValue({
      id: 11,
      rule_id: record.rule_id,
      provider_id: record.provider_id,
      stream_name_hash: record.stream_name_hash,
      event_key: record.event_key,
      created_at: 1_752_800_000_000,
      note: null,
      evidence: record.evidence,
    });
    vi.mocked(api.rejectEventSyncReview).mockResolvedValue({
      status: 'rejected',
    });

    render(<EventSyncReviewQueue />);
    fireEvent.click(
      await screen.findByRole('button', { name: /never attach/i }),
    );

    await waitFor(() => {
      // Identity is the CONTENT FINGERPRINT from the row — never
      // channel/stream ids (those ride only inside evidence).
      expect(api.createEventSyncExclusion).toHaveBeenCalledWith({
        rule_id: record.rule_id,
        provider_id: record.provider_id,
        stream_name_hash: record.stream_name_hash,
        event_key: record.event_key,
        evidence: record.evidence,
      });
    });
    await waitFor(() => {
      expect(api.rejectEventSyncReview).toHaveBeenCalledWith(record.id);
    });
    await waitFor(() => {
      expect(
        screen.queryByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole('status')).toHaveTextContent(/excluded on every future run/i);
  });

  it('surfaces a create failure inline and keeps the card', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.createEventSyncExclusion).mockRejectedValue(
      new Error('boom'),
    );

    render(<EventSyncReviewQueue />);
    fireEvent.click(
      await screen.findByRole('button', { name: /never attach/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('boom');
    });
    // The card stays for retry; the reject half never fired.
    expect(
      screen.getByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
    expect(api.rejectEventSyncReview).not.toHaveBeenCalled();
  });
});

describe('EventSyncReviewQueue — bulk discard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('selects the current rendered scope and confirms the exact count', async () => {
    mockList([makeRecord({ id: 1 }), makeRecord({ id: 2 })]);
    render(<EventSyncReviewQueue />);

    await screen.findByText('2', { selector: '[data-testid="event-sync-review-count"]' });
    fireEvent.click(screen.getByRole('checkbox', { name: /select all rendered reviews/i }));
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));

    expect(screen.getByRole('dialog')).toHaveAccessibleName(
      'Discard 2 selected review items?',
    );
    expect(api.bulkDiscardEventSyncReviews).not.toHaveBeenCalled();
  });

  it('announces the initial loading state', () => {
    vi.mocked(api.getEventSyncReviews).mockReturnValue(new Promise(() => {}));

    render(<EventSyncReviewQueue />);

    expect(screen.getByRole('status')).toHaveTextContent(/loading event sync reviews/i);
  });

  it('owns focus, contains Tab, closes on Escape, and restores the opener', async () => {
    mockList([makeRecord()]);
    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select review 1/i }));
    const opener = screen.getByRole('button', { name: /discard selected/i });
    opener.focus();
    fireEvent.click(opener);

    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const discard = screen.getByRole('button', { name: /^discard 1 item$/i });
    await waitFor(() => expect(cancel).toHaveFocus());
    discard.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(cancel).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('keeps the background inert and ignores Escape while discard is active', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.bulkDiscardEventSyncReviews).mockReturnValue(new Promise(() => {}));
    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select review 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));

    expect(screen.getByRole('button', { name: /never attach/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /reject pairing/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /accept & attach/i })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /^discard 1 item$/i }));
    expect(await screen.findByRole('button', { name: /discarding/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('cancel closes confirmation without discarding and preserves selection', async () => {
    mockList([makeRecord()]);
    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select review 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /select review 1/i })).toBeChecked();
    expect(api.bulkDiscardEventSyncReviews).not.toHaveBeenCalled();
  });

  it('discards exact selected ids, refreshes, resets selection, and reports partial outcome', async () => {
    const first = makeRecord({ id: 1 });
    const second = makeRecord({ id: 2 });
    mockList([first, second]);
    vi.mocked(api.bulkDiscardEventSyncReviews).mockResolvedValue({
      requested_ids: [1, 2],
      discarded_ids: [1],
      missing_ids: [2],
      not_pending_ids: [],
    });

    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select all rendered reviews/i }));
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));
    fireEvent.click(screen.getByRole('button', { name: /^discard 2 items$/i }));

    await waitFor(() => {
      expect(api.bulkDiscardEventSyncReviews).toHaveBeenCalledWith([1, 2]);
    });
    await waitFor(() => expect(api.getEventSyncReviews).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('status')).toHaveTextContent(/discarded 1 of 2/i);
    expect(screen.getByRole('status')).toHaveTextContent(/1 was already removed/i);
    expect(screen.queryByText('2 selected')).not.toBeInTheDocument();
  });

  it('removes committed discards even when the refresh fails', async () => {
    const first = makeRecord({ id: 1 });
    const second = makeRecord({ id: 2, evidence: { ...makeRecord().evidence, stream_name: 'Second stream' } });
    vi.mocked(api.getEventSyncReviews)
      .mockResolvedValueOnce({
        reviews: [first, second], total: 2, page: 1, page_size: 50, total_pages: 1,
      })
      .mockRejectedValueOnce(new Error('refresh failed'));
    vi.mocked(api.bulkDiscardEventSyncReviews).mockResolvedValue({
      requested_ids: [1], discarded_ids: [1], missing_ids: [], not_pending_ids: [],
    });
    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select review 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));
    fireEvent.click(screen.getByRole('button', { name: /^discard 1 item$/i }));

    await waitFor(() => expect(api.getEventSyncReviews).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('checkbox', { name: /select review 1/i })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /select review 2/i })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/discarded 1 of 1/i);
    expect(screen.getByRole('alert')).toHaveTextContent('refresh failed');
  });

  it('removes every terminal outcome from the queue when the refresh fails', async () => {
    const discarded = makeRecord({ id: 1 });
    const missing = makeRecord({
      id: 2,
      evidence: { ...makeRecord().evidence, stream_name: 'Missing stream' },
    });
    const notPending = makeRecord({
      id: 3,
      evidence: { ...makeRecord().evidence, stream_name: 'Terminal stream' },
    });
    vi.mocked(api.getEventSyncReviews)
      .mockResolvedValueOnce({
        reviews: [discarded, missing, notPending],
        total: 3,
        page: 1,
        page_size: 50,
        total_pages: 1,
      })
      .mockRejectedValueOnce(new Error('refresh failed'));
    vi.mocked(api.bulkDiscardEventSyncReviews).mockResolvedValue({
      requested_ids: [1, 2, 3],
      discarded_ids: [1],
      missing_ids: [2],
      not_pending_ids: [3],
    });
    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select all rendered reviews/i }));
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));
    fireEvent.click(screen.getByRole('button', { name: /^discard 3 items$/i }));

    await waitFor(() => expect(api.getEventSyncReviews).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('checkbox', { name: /select review 1/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /select review 2/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /select review 3/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/selected$/i)).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/discarded 1 of 3/i);
    expect(screen.getByRole('status')).toHaveTextContent(/1 was already removed/i);
    expect(screen.getByRole('status')).toHaveTextContent(/1 was no longer pending/i);
    expect(screen.getByRole('alert')).toHaveTextContent('refresh failed');
  });

  it('keeps selection and shows API errors in the confirmation dialog', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.bulkDiscardEventSyncReviews).mockRejectedValue(new Error('discard failed'));
    render(<EventSyncReviewQueue />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /select review 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /discard selected/i }));
    fireEvent.click(screen.getByRole('button', { name: /^discard 1 item$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('discard failed');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /select review 1/i })).toBeChecked();
  });

  it('does not count a selected row after an individual action removes it', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.rejectEventSyncReview).mockResolvedValue({ status: 'rejected' });
    render(<EventSyncReviewQueue />);

    fireEvent.click(await screen.findByRole('checkbox', { name: /select review 1/i }));
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /reject pairing/i }));

    await waitFor(() => {
      expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /discard selected/i })).not.toBeInTheDocument();
  });
});
