/**
 * Unit tests for StreamProfilesListModal (enhancedchannelmanager-hq3de.j).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { StreamProfilesListModal } from './StreamProfilesListModal';
import type { StreamProfile } from '../types';

vi.mock('../services/api', () => ({
  createStreamProfile: vi.fn(),
}));

const mockSuccess = vi.fn();
const mockError = vi.fn();
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({ success: mockSuccess, error: mockError, warning: vi.fn(), info: vi.fn() }),
}));

import * as api from '../services/api';

const existingProfile: StreamProfile = {
  id: 1, name: 'Default', command: 'ffmpeg', parameters: '-i {streamUrl}', is_active: true, locked: false,
};

describe('StreamProfilesListModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows an empty state when there are no stream profiles', () => {
    render(<StreamProfilesListModal streamProfiles={[]} onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText(/no stream profiles yet/i)).toBeInTheDocument();
  });

  it('lists existing stream profiles', () => {
    render(<StreamProfilesListModal streamProfiles={[existingProfile]} onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText('Default')).toBeInTheDocument();
    expect(screen.getByText('ffmpeg')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('opens the create form and disables Create Profile until name+command are set', () => {
    render(<StreamProfilesListModal streamProfiles={[]} onClose={vi.fn()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText('New Stream Profile'));

    const createBtn = screen.getByRole('button', { name: 'Create Profile' });
    expect(createBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('e.g., FFmpeg Transcode'), { target: { value: 'Custom' } });
    expect(createBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('e.g., ffmpeg'), { target: { value: 'streamlink' } });
    expect(createBtn).toBeEnabled();
  });

  it('creates a profile and calls onChanged', async () => {
    vi.mocked(api.createStreamProfile).mockResolvedValue({
      id: 2, name: 'Custom', command: 'streamlink', parameters: '', is_active: true, locked: false,
    });
    const onChanged = vi.fn();

    render(<StreamProfilesListModal streamProfiles={[]} onClose={vi.fn()} onChanged={onChanged} />);
    fireEvent.click(screen.getByText('New Stream Profile'));
    fireEvent.change(screen.getByPlaceholderText('e.g., FFmpeg Transcode'), { target: { value: 'Custom' } });
    fireEvent.change(screen.getByPlaceholderText('e.g., ffmpeg'), { target: { value: 'streamlink' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Profile' }));

    await waitFor(() => {
      expect(api.createStreamProfile).toHaveBeenCalledWith({
        name: 'Custom', command: 'streamlink', parameters: '', is_active: true,
      });
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it('surfaces an error notification when creation fails', async () => {
    vi.mocked(api.createStreamProfile).mockRejectedValue(new Error('Dispatcharr rejected the profile'));

    render(<StreamProfilesListModal streamProfiles={[]} onClose={vi.fn()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText('New Stream Profile'));
    fireEvent.change(screen.getByPlaceholderText('e.g., FFmpeg Transcode'), { target: { value: 'Custom' } });
    fireEvent.change(screen.getByPlaceholderText('e.g., ffmpeg'), { target: { value: 'streamlink' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Profile' }));

    await waitFor(() => {
      expect(mockError).toHaveBeenCalledWith('Dispatcharr rejected the profile', 'Stream Profiles');
    });
  });

  it('Cancel hides the create form without calling the API', () => {
    render(<StreamProfilesListModal streamProfiles={[]} onClose={vi.fn()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText('New Stream Profile'));
    fireEvent.change(screen.getByPlaceholderText('e.g., FFmpeg Transcode'), { target: { value: 'Custom' } });

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByPlaceholderText('e.g., FFmpeg Transcode')).not.toBeInTheDocument();
    expect(api.createStreamProfile).not.toHaveBeenCalled();
  });
});
