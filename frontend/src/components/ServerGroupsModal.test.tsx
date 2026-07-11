/**
 * Unit tests for ServerGroupsModal (enhancedchannelmanager-hq3de.c).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ServerGroupsModal } from './ServerGroupsModal';

vi.mock('../services/api', () => ({
  getServerGroups: vi.fn(),
  createServerGroup: vi.fn(),
  updateServerGroup: vi.fn(),
  deleteServerGroup: vi.fn(),
}));

const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockNotifications = { success: mockSuccess, error: mockError, warning: vi.fn(), info: vi.fn() };
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

import * as api from '../services/api';

describe('ServerGroupsModal', () => {
  let confirmSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    confirmSpy = vi.spyOn(window, 'confirm');
  });

  it('shows an empty state when no server groups exist', async () => {
    vi.mocked(api.getServerGroups).mockResolvedValue([]);
    render(<ServerGroupsModal onClose={vi.fn()} onChanged={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/no server groups yet/i)).toBeInTheDocument();
    });
  });

  it('lists existing server groups', async () => {
    vi.mocked(api.getServerGroups).mockResolvedValue([
      { id: 1, name: 'US Providers' },
      { id: 2, name: 'UK Providers' },
    ]);
    render(<ServerGroupsModal onClose={vi.fn()} onChanged={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('US Providers')).toBeInTheDocument();
      expect(screen.getByText('UK Providers')).toBeInTheDocument();
    });
  });

  it('creates a new server group and calls onChanged', async () => {
    vi.mocked(api.getServerGroups).mockResolvedValue([]);
    vi.mocked(api.createServerGroup).mockResolvedValue({ id: 3, name: 'New Group' });
    const onChanged = vi.fn();

    render(<ServerGroupsModal onClose={vi.fn()} onChanged={onChanged} />);
    await waitFor(() => screen.getByPlaceholderText('New server group name...'));

    fireEvent.change(screen.getByPlaceholderText('New server group name...'), { target: { value: 'New Group' } });
    fireEvent.click(screen.getByText('Create'));

    await waitFor(() => {
      expect(api.createServerGroup).toHaveBeenCalledWith({ name: 'New Group' });
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it('renames a server group', async () => {
    vi.mocked(api.getServerGroups).mockResolvedValue([{ id: 1, name: 'US Providers' }]);
    vi.mocked(api.updateServerGroup).mockResolvedValue({ id: 1, name: 'North America' });
    const onChanged = vi.fn();

    render(<ServerGroupsModal onClose={vi.fn()} onChanged={onChanged} />);
    await waitFor(() => screen.getByText('US Providers'));

    fireEvent.click(screen.getByLabelText('Rename US Providers'));
    const input = screen.getByDisplayValue('US Providers');
    fireEvent.change(input, { target: { value: 'North America' } });
    fireEvent.click(screen.getByLabelText('Save name'));

    await waitFor(() => {
      expect(api.updateServerGroup).toHaveBeenCalledWith(1, { name: 'North America' });
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it('deletes a server group after confirming', async () => {
    vi.mocked(api.getServerGroups).mockResolvedValue([{ id: 1, name: 'US Providers' }]);
    vi.mocked(api.deleteServerGroup).mockResolvedValue({ status: 'deleted' });
    confirmSpy.mockReturnValue(true);
    const onChanged = vi.fn();

    render(<ServerGroupsModal onClose={vi.fn()} onChanged={onChanged} />);
    await waitFor(() => screen.getByText('US Providers'));

    fireEvent.click(screen.getByLabelText('Delete US Providers'));

    await waitFor(() => {
      expect(api.deleteServerGroup).toHaveBeenCalledWith(1);
      expect(onChanged).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByText('US Providers')).not.toBeInTheDocument();
    });
  });

  it('does not delete when the confirm dialog is declined', async () => {
    vi.mocked(api.getServerGroups).mockResolvedValue([{ id: 1, name: 'US Providers' }]);
    confirmSpy.mockReturnValue(false);

    render(<ServerGroupsModal onClose={vi.fn()} onChanged={vi.fn()} />);
    await waitFor(() => screen.getByText('US Providers'));

    fireEvent.click(screen.getByLabelText('Delete US Providers'));

    expect(api.deleteServerGroup).not.toHaveBeenCalled();
    expect(screen.getByText('US Providers')).toBeInTheDocument();
  });
});
