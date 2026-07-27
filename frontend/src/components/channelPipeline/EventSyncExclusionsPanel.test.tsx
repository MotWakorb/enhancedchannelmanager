/**
 * Unit tests for EventSyncExclusionsPanel (bead ti939.3.5) — the operator
 * management surface for never-attach exclusions.
 *
 * Locks the contract:
 *   - Renders one card per EventSyncExclusionRecord from
 *     GET /api/event-sync-exclusions, pairing both raw names with rule,
 *     provider and creation date context.
 *   - Renders NOTHING while the list is empty (the review queue above is
 *     the primary surface; empty is the normal state).
 *   - Remove calls DELETE /{id}, removes the card, and explains that the
 *     pairing becomes matchable again.
 *   - Backend errors surface inline; the card stays for retry.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import {
  EventSyncExclusionsPanel,
  EXCLUSIONS_CHANGED_EVENT,
} from './EventSyncExclusionsPanel';
import type { EventSyncExclusionRecord } from '../../types/eventSync';
import * as api from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    getEventSyncExclusions: vi.fn(),
    deleteEventSyncExclusion: vi.fn(),
  };
});

function makeRecord(
  overrides: Partial<EventSyncExclusionRecord> = {},
): EventSyncExclusionRecord {
  return {
    id: 11,
    rule_id: 3,
    provider_id: 7,
    stream_name_hash: 'a'.repeat(64),
    event_key: 'fury vs usyk prelims|2026-07-12T00:00:00+00:00',
    created_at: 1_752_800_000_000,
    note: null,
    evidence: {
      rule_name: 'Event Sync PPV',
      stream_name: 'BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET',
      provider: 'BoxProvider',
      master_channel_name: 'PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET',
    },
    ...overrides,
  };
}

function mockList(exclusions: EventSyncExclusionRecord[]) {
  vi.mocked(api.getEventSyncExclusions).mockResolvedValue({
    exclusions,
    total: exclusions.length,
    page: 1,
    page_size: 50,
    total_pages: exclusions.length ? 1 : 0,
  });
}

describe('EventSyncExclusionsPanel — rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders both raw names with rule and provider context', async () => {
    mockList([makeRecord({ note: 'wrong feed every week' })]);
    render(<EventSyncExclusionsPanel />);

    expect(
      await screen.findByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
    expect(screen.getByText('never attaches to')).toBeInTheDocument();
    expect(screen.getByText('Event Sync PPV')).toBeInTheDocument();
    expect(screen.getByText('BoxProvider')).toBeInTheDocument();
    expect(screen.getByText('wrong feed every week')).toBeInTheDocument();
    expect(
      screen.getByTestId('event-sync-exclusions-count'),
    ).toHaveTextContent('1');
  });

  it('renders nothing when the list is empty (the normal state)', async () => {
    mockList([]);
    const { container } = render(<EventSyncExclusionsPanel />);
    await waitFor(() => {
      expect(api.getEventSyncExclusions).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(container).toBeEmptyDOMElement();
    });
  });

  it('refetches when the review queue signals a new exclusion', async () => {
    // Mount empty (renders nothing), then the review queue's Never attach
    // fires the CustomEvent — the panel must refetch and show the row.
    mockList([]);
    render(<EventSyncExclusionsPanel />);
    await waitFor(() => {
      expect(api.getEventSyncExclusions).toHaveBeenCalledTimes(1);
    });

    mockList([makeRecord()]);
    fireEvent(window, new CustomEvent(EXCLUSIONS_CHANGED_EVENT));

    expect(
      await screen.findByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
    expect(api.getEventSyncExclusions).toHaveBeenCalledTimes(2);
  });

  it('falls back to the fingerprint when evidence has no names', async () => {
    mockList([makeRecord({ evidence: {} })]);
    render(<EventSyncExclusionsPanel />);
    expect(
      await screen.findByText(`fingerprint ${'a'.repeat(12)}…`),
    ).toBeInTheDocument();
    expect(
      screen.getByText('fury vs usyk prelims|2026-07-12T00:00:00+00:00'),
    ).toBeInTheDocument();
  });
});

describe('EventSyncExclusionsPanel — remove', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('removes an exclusion and explains the pairing is matchable again', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.deleteEventSyncExclusion).mockResolvedValue(undefined);

    render(<EventSyncExclusionsPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /remove/i }));

    await waitFor(() => {
      expect(api.deleteEventSyncExclusion).toHaveBeenCalledWith(11);
    });
    await waitFor(() => {
      expect(
        screen.queryByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole('status')).toHaveTextContent(
      /matchable again/i,
    );
  });

  it('surfaces a remove failure inline and keeps the card', async () => {
    mockList([makeRecord()]);
    vi.mocked(api.deleteEventSyncExclusion).mockRejectedValue(
      new Error('nope'),
    );

    render(<EventSyncExclusionsPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /remove/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('nope');
    });
    expect(
      screen.getByText('BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET'),
    ).toBeInTheDocument();
  });
});
