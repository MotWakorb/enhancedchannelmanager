import { StrictMode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HttpError } from '../../services/httpClient';
import { OperatorDashboard, type OperatorDashboardProps } from './OperatorDashboard';

const mocks = vi.hoisted(() => ({
  getM3UChangesSummary: vi.fn(), getTasks: vi.fn(), getJournalStats: vi.fn(),
}));
vi.mock('../../services/api', () => mocks);

const retry = vi.fn();
const props = (): OperatorDashboardProps => ({
  health: { value: { status: 'healthy', service: 'ECM', version: '1.2.3', release_channel: 'stable', git_commit: 'abc' }, state: 'success', hasSnapshot: true, retry },
  channels: { value: 12, state: 'success', hasSnapshot: true, retry },
  streams: { value: 34, state: 'success', hasSnapshot: true, retry },
  providers: { value: 1, state: 'success', hasSnapshot: true, retry },
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getM3UChangesSummary.mockResolvedValue({ total_changes: 3, streams_added: 2, streams_removed: 1, since: '2026-07-26T12:00:00Z' });
  mocks.getTasks.mockResolvedValue({ tasks: [{ task_id: 'a', enabled: true, effective_enabled: true, status: 'running', last_run: '2026-07-27T01:00:00Z' }] });
  mocks.getJournalStats.mockResolvedValue({ total_entries: 9, by_category: { channel: 9 }, date_range: { oldest: null, newest: '2026-07-27T02:00:00Z' } });
});

describe('OperatorDashboard', () => {
  it('uses App-owned inventory snapshots and only loads dashboard-specific sources', async () => {
    render(<OperatorDashboard {...props()} />);
    expect(screen.getByText('12 channels')).toBeVisible();
    expect(screen.getByText('34 streams')).toBeVisible();
    expect(screen.getByText('1 account')).toBeVisible();
    expect(await screen.findByText('3 changes')).toBeVisible();
    expect(mocks.getM3UChangesSummary).toHaveBeenCalledTimes(1);
    expect(mocks.getTasks).toHaveBeenCalledTimes(1);
    expect(mocks.getJournalStats).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: /Open Recent M3U changes/ })).toHaveAttribute('href', '#m3u-changes?hours=24');
  });

  it('does not replay requests when an App-owned snapshot transitions from loading', async () => {
    const initial = props();
    initial.health = { ...initial.health, value: null, state: 'loading', hasSnapshot: false };
    const { rerender } = render(<OperatorDashboard {...initial} />);
    await screen.findByText('3 changes');
    rerender(<OperatorDashboard {...props()} />);
    expect(screen.getByText('healthy')).toBeVisible();
    expect(mocks.getM3UChangesSummary).toHaveBeenCalledTimes(1);
    expect(mocks.getTasks).toHaveBeenCalledTimes(1);
    expect(mocks.getJournalStats).toHaveBeenCalledTimes(1);
  });

  it('deduplicates StrictMode rehearsal requests and still commits results', async () => {
    render(<StrictMode><OperatorDashboard {...props()} /></StrictMode>);
    expect(await screen.findByText('9 entries')).toBeVisible();
    expect(mocks.getM3UChangesSummary).toHaveBeenCalledTimes(1);
    expect(mocks.getTasks).toHaveBeenCalledTimes(1);
    expect(mocks.getJournalStats).toHaveBeenCalledTimes(1);
  });

  it('hides a protected destination and retries only a recoverable card', async () => {
    const user = userEvent.setup();
    mocks.getTasks.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ tasks: [] });
    mocks.getJournalStats.mockRejectedValue(new HttpError('Forbidden', 403));
    render(<OperatorDashboard {...props()} />);
    const tasksCard = screen.getByRole('heading', { name: 'Scheduled work' }).closest('article')!;
    const journalCard = screen.getByRole('heading', { name: 'Recent journal' }).closest('article')!;
    expect(await within(tasksCard).findByText('Couldn’t load scheduled tasks')).toBeVisible();
    expect(within(journalCard).getByText('Destination unavailable with current permissions')).toBeVisible();
    expect(within(journalCard).queryByRole('link')).not.toBeInTheDocument();
    await user.click(within(tasksCard).getByRole('button', { name: 'Retry' }));
    expect(await within(tasksCard).findByText('No scheduled tasks configured')).toBeVisible();
    await waitFor(() => expect(mocks.getTasks).toHaveBeenCalledTimes(2));
    expect(mocks.getM3UChangesSummary).toHaveBeenCalledTimes(1);
  });

  it('retries only the failed half of the lineup snapshot', async () => {
    const user = userEvent.setup();
    const dashboardProps = props();
    const retryChannels = vi.fn();
    const retryStreams = vi.fn();
    dashboardProps.channels = { value: 0, state: 'error', hasSnapshot: false, retry: retryChannels };
    dashboardProps.streams = { ...dashboardProps.streams, retry: retryStreams };
    render(<OperatorDashboard {...dashboardProps} />);
    const lineup = screen.getByRole('heading', { name: 'Lineup inventory' }).closest('article')!;
    await user.click(within(lineup).getByRole('button', { name: 'Retry' }));
    expect(retryChannels).toHaveBeenCalledOnce();
    expect(retryStreams).not.toHaveBeenCalled();
  });
});
