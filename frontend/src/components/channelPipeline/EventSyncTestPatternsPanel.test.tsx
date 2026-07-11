/**
 * Tests for the Event Sync Test Patterns panel (bead ti939.1.5).
 *
 * Pattern extraction runs server-side through POST /api/dummy-epg/preview/batch
 * — these tests pin the RENDERING contract: raw name → extracted title/date/
 * time per pattern, with the never-guess incomplete-date flag, the
 * matcher-level validity verdict (bead hirm6: 'Parsed' ONLY when the backend
 * says the matcher would build a start time — captured-but-invalid '45 Jul'
 * shows as a parse failure), and the no-match state as text (not color alone).
 */
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server, resetMockDataStore } from '../../test/mocks/server';
import { EventSyncTestPatternsPanel } from './EventSyncTestPatternsPanel';
import type { LabeledEventSyncPattern } from './EventSyncTestPatternsPanel';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

const PATTERN: LabeledEventSyncPattern = {
  label: 'Title @ Day-first date (built-in)',
  pattern: {
    name: 'slot-title-day-first-date',
    title_pattern: '^(?P<title>.+?)$',
    time_pattern: '(?P<hour>\\d{1,2}):(?P<minute>\\d{2})',
    date_pattern: '(?P<day>\\d{1,2})\\s+(?P<month>[A-Za-z]{3,9})',
  },
};

/** Batch-preview stub: keyed by sample name so assertions stay explicit. */
function stubBatchPreview(byName: Record<string, { matched: boolean; groups: Record<string, string | null> | null; event_sync_start_valid?: boolean }>) {
  server.use(
    http.post('/api/dummy-epg/preview/batch', async ({ request }) => {
      const body = (await request.json()) as { sample_names: string[] };
      return HttpResponse.json(
        body.sample_names.map(name => ({
          original_name: name,
          substituted_name: name,
          substitution_steps: [],
          matched_variant: null,
          time_variables: null,
          rendered: {},
          ...byName[name],
        }))
      );
    })
  );
}

describe('EventSyncTestPatternsPanel', () => {
  it('renders extracted title, date and time per sample name after testing', async () => {
    const user = userEvent.setup();
    stubBatchPreview({
      'Yankees vs Red Sox @ 11 Jul 06:00 PM ET': {
        matched: true,
        groups: {
          title: 'Yankees vs Red Sox',
          day: '11',
          month: 'Jul',
          hour: '06',
          minute: '00',
          ampm: 'P',
        },
        event_sync_start_valid: true,
      },
    });
    render(<EventSyncTestPatternsPanel patterns={[PATTERN]} groupOptions={[]} />);

    await user.type(
      screen.getByLabelText(/sample stream names/i),
      'Yankees vs Red Sox @ 11 Jul 06:00 PM ET'
    );
    await user.click(screen.getByRole('button', { name: /test patterns/i }));

    const table = await screen.findByRole('table');
    expect(within(table).getByText('Title @ Day-first date (built-in)')).toBeInTheDocument();
    const row = within(table).getAllByRole('row')[1];
    const cells = within(row).getAllByRole('cell');
    expect(cells[0]).toHaveTextContent('Yankees vs Red Sox @ 11 Jul 06:00 PM ET');
    expect(cells[1]).toHaveTextContent('Yankees vs Red Sox');
    expect(cells[2]).toHaveTextContent('11 Jul');
    expect(cells[3]).toHaveTextContent('06:00 PM');
    expect(cells[4]).toHaveTextContent('Parsed');
  });

  it('flags a title-only match as a parse failure (start times are never guessed)', async () => {
    const user = userEvent.setup();
    stubBatchPreview({
      'Big Fight Night': {
        matched: true,
        groups: { title: 'Big Fight Night' },
        event_sync_start_valid: false,
      },
    });
    render(<EventSyncTestPatternsPanel patterns={[PATTERN]} groupOptions={[]} />);

    await user.type(screen.getByLabelText(/sample stream names/i), 'Big Fight Night');
    await user.click(screen.getByRole('button', { name: /test patterns/i }));

    const table = await screen.findByRole('table');
    expect(
      within(table).getByText(/incomplete date\/time — would be a parse failure/i)
    ).toBeInTheDocument();
  });

  it('flags a captured-but-invalid date as a parse failure, never Parsed (bead hirm6)', async () => {
    // The '45 Jul' garbage-month/day case: every group captured, so the old
    // group-presence check showed 'Parsed' — but the matcher would never
    // build a start time (July 45 is not a real date). The backend flag is
    // authoritative and the panel must render it honestly.
    const user = userEvent.setup();
    stubBatchPreview({
      'A vs B @ 45 Jul 06:00 PM ET': {
        matched: true,
        groups: {
          title: 'A vs B',
          day: '45',
          month: 'Jul',
          hour: '06',
          minute: '00',
          ampm: 'P',
        },
        event_sync_start_valid: false,
      },
    });
    render(<EventSyncTestPatternsPanel patterns={[PATTERN]} groupOptions={[]} />);

    await user.type(
      screen.getByLabelText(/sample stream names/i),
      'A vs B @ 45 Jul 06:00 PM ET'
    );
    await user.click(screen.getByRole('button', { name: /test patterns/i }));

    const table = await screen.findByRole('table');
    const row = within(table).getAllByRole('row')[1];
    // The extracted parts still render (the operator sees WHAT was captured)...
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('45 Jul');
    // ...but the verdict is a parse failure with the invalid-date wording.
    expect(
      within(row).getByText(/invalid date\/time — not a real calendar date\/time/i)
    ).toBeInTheDocument();
    expect(within(row).queryByText('Parsed')).toBeNull();
  });

  it("renders 'Parsed' only from the backend validity flag, not group presence", async () => {
    // Same complete-looking groups; only the flag differs.
    const user = userEvent.setup();
    stubBatchPreview({
      'Good @ 11 Jul 06:00 PM ET': {
        matched: true,
        groups: { title: 'Good', day: '11', month: 'Jul', hour: '06', minute: '00', ampm: 'P' },
        event_sync_start_valid: true,
      },
      'Bad @ 45 Jul 06:00 PM ET': {
        matched: true,
        groups: { title: 'Bad', day: '45', month: 'Jul', hour: '06', minute: '00', ampm: 'P' },
        event_sync_start_valid: false,
      },
    });
    render(<EventSyncTestPatternsPanel patterns={[PATTERN]} groupOptions={[]} />);

    await user.type(
      screen.getByLabelText(/sample stream names/i),
      'Good @ 11 Jul 06:00 PM ET\nBad @ 45 Jul 06:00 PM ET'
    );
    await user.click(screen.getByRole('button', { name: /test patterns/i }));

    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(within(rows[1]).getByText('Parsed')).toBeInTheDocument();
    expect(within(rows[2]).queryByText('Parsed')).toBeNull();
    expect(
      within(rows[2]).getByText(/would be a parse failure/i)
    ).toBeInTheDocument();
  });

  it('renders a No match status when the title pattern does not match', async () => {
    const user = userEvent.setup();
    stubBatchPreview({
      garbage: { matched: false, groups: null },
    });
    render(<EventSyncTestPatternsPanel patterns={[PATTERN]} groupOptions={[]} />);

    await user.type(screen.getByLabelText(/sample stream names/i), 'garbage');
    await user.click(screen.getByRole('button', { name: /test patterns/i }));

    const table = await screen.findByRole('table');
    const row = within(table).getAllByRole('row')[1];
    expect(within(row).getByText('No match')).toBeInTheDocument();
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('—');
  });

  it('runs one extraction request per pattern and renders one table per pattern', async () => {
    const user = userEvent.setup();
    const requestedTitlePatterns: string[] = [];
    server.use(
      http.post('/api/dummy-epg/preview/batch', async ({ request }) => {
        const body = (await request.json()) as { sample_names: string[]; title_pattern: string };
        requestedTitlePatterns.push(body.title_pattern);
        return HttpResponse.json(
          body.sample_names.map(name => ({
            original_name: name,
            substituted_name: name,
            substitution_steps: [],
            matched: false,
            matched_variant: null,
            groups: null,
            time_variables: null,
            rendered: {},
          }))
        );
      })
    );
    const second: LabeledEventSyncPattern = {
      label: 'Second pattern',
      pattern: { name: 'second', title_pattern: '^second-(?P<title>.+)$' },
    };
    render(<EventSyncTestPatternsPanel patterns={[PATTERN, second]} groupOptions={[]} />);

    await user.type(screen.getByLabelText(/sample stream names/i), 'anything');
    await user.click(screen.getByRole('button', { name: /test patterns/i }));

    await waitFor(() => {
      expect(screen.getAllByRole('table')).toHaveLength(2);
    });
    expect(requestedTitlePatterns).toEqual([
      PATTERN.pattern.title_pattern,
      second.pattern.title_pattern,
    ]);
    expect(screen.getByText('Second pattern')).toBeInTheDocument();
  });

  it('disables the test button when there are no sample names', () => {
    render(<EventSyncTestPatternsPanel patterns={[PATTERN]} groupOptions={[]} />);
    expect(screen.getByRole('button', { name: /test patterns/i })).toBeDisabled();
  });
});
