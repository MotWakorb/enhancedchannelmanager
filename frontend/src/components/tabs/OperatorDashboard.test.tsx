import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HttpError } from '../../services/httpClient';
import { OperatorDashboard } from './OperatorDashboard';

const mocks = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getChannels: vi.fn(),
  getStreams: vi.fn(),
  getM3UAccounts: vi.fn(),
  getM3UChangesSummary: vi.fn(),
  getTasks: vi.fn(),
  getJournalStats: vi.fn(),
}));
vi.mock('../../services/api', () => mocks);

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getHealth.mockResolvedValue({ status: 'healthy', service: 'ECM', version: '1.2.3', release_channel: 'stable', git_commit: 'abc' });
  mocks.getChannels.mockResolvedValue({ count: 12, next: null, previous: null, results: [] });
  mocks.getStreams.mockResolvedValue({ count: 34, next: null, previous: null, results: [] });
  mocks.getM3UAccounts.mockResolvedValue([{ id: 1, name: 'Primary' }]);
  mocks.getM3UChangesSummary.mockResolvedValue({
    total_changes: 3, groups_added: 0, groups_removed: 0, streams_added: 2,
    streams_removed: 1, accounts_affected: [1], since: '2026-07-26T12:00:00Z',
  });
  mocks.getTasks.mockResolvedValue({ tasks: [
    { task_id: 'a', enabled: true, effective_enabled: true, status: 'running', last_run: '2026-07-27T01:00:00Z' },
    { task_id: 'b', enabled: true, effective_enabled: true, status: 'failed', last_run: '2026-07-27T02:00:00Z' },
  ] });
  mocks.getJournalStats.mockResolvedValue({
    total_entries: 9, by_category: { channel: 5, stream: 4 }, by_action_type: {},
    date_range: { oldest: '2026-07-20T00:00:00Z', newest: '2026-07-27T02:00:00Z' },
  });
});

describe('OperatorDashboard', () => {
  it('reuses the resolved App health snapshot without issuing a duplicate request', async () => {
    render(<OperatorDashboard initialHealth={{
      status: 'healthy', service: 'ECM', version: 'cached', release_channel: 'stable', git_commit: 'abc',
    }} />);
    expect(screen.getByText('ECM cached · stable')).toBeVisible();
    await waitFor(() => expect(mocks.getM3UAccounts).toHaveBeenCalledTimes(1));
    expect(mocks.getHealth).not.toHaveBeenCalled();
  });

  it('uses each approved API contract once and renders exact supported values and links', async () => {
    render(<OperatorDashboard />);
    expect(screen.getAllByLabelText(/^Loading /)).toHaveLength(6);
    expect(await screen.findByText('healthy')).toBeVisible();
    expect(screen.getByText('12 channels')).toBeVisible();
    expect(screen.getByText('34 streams')).toBeVisible();
    expect(screen.getByText('1 account')).toBeVisible();
    expect(screen.getByText('3 changes')).toBeVisible();
    expect(screen.getByText('2 enabled')).toBeVisible();
    expect(screen.getByText('9 entries')).toBeVisible();
    expect(mocks.getChannels).toHaveBeenCalledWith({ page: 1, pageSize: 1 });
    expect(mocks.getStreams).toHaveBeenCalledWith({ page: 1, pageSize: 1 });
    expect(mocks.getM3UChangesSummary).toHaveBeenCalledWith({ hours: 24 });
    for (const fn of Object.values(mocks)) expect(fn).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: /Open Scheduled work/ })).toHaveAttribute('href', '#settings/scheduled-tasks');
    expect(screen.getByRole('link', { name: /Open Recent M3U changes/ })).toHaveAttribute('href', '#m3u-changes');
  });

  it('renders honest zero states without inferring health', async () => {
    mocks.getChannels.mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    mocks.getStreams.mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    mocks.getM3UAccounts.mockResolvedValue([]);
    mocks.getM3UChangesSummary.mockResolvedValue({
      total_changes: 0, groups_added: 0, groups_removed: 0, streams_added: 0,
      streams_removed: 0, accounts_affected: [], since: '2026-07-26T12:00:00Z',
    });
    mocks.getTasks.mockResolvedValue({ tasks: [] });
    mocks.getJournalStats.mockResolvedValue({ total_entries: 0, by_category: {}, by_action_type: {}, date_range: { oldest: null, newest: null } });
    render(<OperatorDashboard />);
    expect(await screen.findByText('No lineup configured')).toBeVisible();
    expect(screen.getByText('No M3U accounts configured')).toBeVisible();
    expect(screen.getByText('No recent M3U changes')).toBeVisible();
    expect(screen.getByText('No scheduled tasks configured')).toBeVisible();
    expect(screen.getByText('No journal entries')).toBeVisible();
    expect(screen.queryByText(/source healthy/i)).not.toBeInTheDocument();
  });

  it('settles cards independently, hides forbidden counts, and retries only the failed card', async () => {
    const user = userEvent.setup();
    mocks.getTasks.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ tasks: [] });
    mocks.getJournalStats.mockRejectedValue(new HttpError('Forbidden', 403));
    render(<OperatorDashboard />);
    const tasksCard = screen.getByRole('heading', { name: 'Scheduled work' }).closest('article')!;
    expect(await within(tasksCard).findByText('Couldn’t load scheduled tasks')).toBeVisible();
    expect(screen.getByText('You don’t have permission to view this summary')).toBeVisible();
    expect(screen.getByText('healthy')).toBeVisible();
    expect(within(tasksCard).getByRole('button', { name: 'Retry' })).toBeVisible();
    await user.click(within(tasksCard).getByRole('button', { name: 'Retry' }));
    expect(await within(tasksCard).findByText('No scheduled tasks configured')).toBeVisible();
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Scheduled work updated.'));
    expect(mocks.getTasks).toHaveBeenCalledTimes(2);
    expect(mocks.getHealth).toHaveBeenCalledTimes(1);
  });
});
