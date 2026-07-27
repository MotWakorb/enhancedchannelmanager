/**
 * Unit tests for EventSyncTeamAliasesSection (bead enhancedchannelmanager-ti939.4.2).
 *
 * Contracts under test:
 *   - Loads alias groups from GET /api/event-sync/team-aliases on mount and
 *     renders each group's terms as removable chips.
 *   - Add group / add term / remove term edit local state only.
 *   - Save sends the FULL group list through updateEventSyncTeamAliases and
 *     surfaces success; a backend validation error is surfaced verbatim.
 *   - Save is disabled until something changed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { EventSyncTeamAliasesSection } from './EventSyncTeamAliasesSection';

vi.mock('../../services/channelPipelineApi', () => ({
  getEventSyncTeamAliases: vi.fn(),
  updateEventSyncTeamAliases: vi.fn(),
}));

const mockSuccess = vi.fn();
const mockError = vi.fn();
// Stable object identity, matching the real NotificationContext's useMemo.
const mockNotifications = {
  success: mockSuccess,
  error: mockError,
  warning: vi.fn(),
  info: vi.fn(),
};
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

import * as api from '../../services/channelPipelineApi';

const getAliases = vi.mocked(api.getEventSyncTeamAliases);
const updateAliases = vi.mocked(api.updateEventSyncTeamAliases);

const STORED = {
  groups: [
    { terms: ['Red Devils', 'Manchester United', 'MUFC'], note: 'corpus 2026-07-18' },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  getAliases.mockResolvedValue({ groups: [] });
});

describe('EventSyncTeamAliasesSection', () => {
  it('renders stored groups with their terms as chips', async () => {
    getAliases.mockResolvedValue(STORED);
    render(<EventSyncTeamAliasesSection />);

    expect(await screen.findByText('Red Devils')).toBeInTheDocument();
    expect(screen.getByText('Manchester United')).toBeInTheDocument();
    expect(screen.getByText('MUFC')).toBeInTheDocument();
    expect(screen.getByDisplayValue('corpus 2026-07-18')).toBeInTheDocument();
  });

  it('shows the empty state when no groups are stored', async () => {
    render(<EventSyncTeamAliasesSection />);
    expect(
      await screen.findByText(/No alias groups configured/i),
    ).toBeInTheDocument();
  });

  it('adds a term to a group via the input and Enter key', async () => {
    getAliases.mockResolvedValue(STORED);
    render(<EventSyncTeamAliasesSection />);
    await screen.findByText('Red Devils');

    const input = screen.getByPlaceholderText(/add a spelling/i);
    fireEvent.change(input, { target: { value: 'Man Utd' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(screen.getByText('Man Utd')).toBeInTheDocument();
  });

  it('save sends the full group list and reports success', async () => {
    getAliases.mockResolvedValue(STORED);
    updateAliases.mockResolvedValue({
      groups: [
        { terms: ['Red Devils', 'Manchester United', 'MUFC', 'Man Utd'], note: 'corpus 2026-07-18' },
      ],
    });
    render(<EventSyncTeamAliasesSection />);
    await screen.findByText('Red Devils');

    const input = screen.getByPlaceholderText(/add a spelling/i);
    fireEvent.change(input, { target: { value: 'Man Utd' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    fireEvent.click(screen.getByRole('button', { name: /save team aliases/i }));

    await waitFor(() => expect(updateAliases).toHaveBeenCalledTimes(1));
    expect(updateAliases).toHaveBeenCalledWith([
      {
        terms: ['Red Devils', 'Manchester United', 'MUFC', 'Man Utd'],
        note: 'corpus 2026-07-18',
      },
    ]);
    await waitFor(() => expect(mockSuccess).toHaveBeenCalled());
  });

  it('surfaces a backend validation error', async () => {
    getAliases.mockResolvedValue(STORED);
    updateAliases.mockRejectedValue(
      new Error("Alias term 'FC' has no identity tokens after normalization"),
    );
    render(<EventSyncTeamAliasesSection />);
    await screen.findByText('Red Devils');

    // Any change enables Save.
    const input = screen.getByPlaceholderText(/add a spelling/i);
    fireEvent.change(input, { target: { value: 'FC' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: /save team aliases/i }));

    await waitFor(() => expect(mockError).toHaveBeenCalled());
    expect(String(mockError.mock.calls[0][0])).toContain('no identity tokens');
  });

  it('save stays disabled until something changes', async () => {
    getAliases.mockResolvedValue(STORED);
    render(<EventSyncTeamAliasesSection />);
    await screen.findByText('Red Devils');

    const save = screen.getByRole('button', { name: /save team aliases/i });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /add alias group/i }));
    expect(save).not.toBeDisabled();
  });

  it('removes a term chip', async () => {
    getAliases.mockResolvedValue(STORED);
    render(<EventSyncTeamAliasesSection />);
    await screen.findByText('Red Devils');

    const chip = screen.getByText('MUFC').closest('.email-recipient-tag');
    expect(chip).not.toBeNull();
    fireEvent.click(within(chip as HTMLElement).getByRole('button'));

    expect(screen.queryByText('MUFC')).not.toBeInTheDocument();
  });

  it('removes a whole group', async () => {
    getAliases.mockResolvedValue(STORED);
    render(<EventSyncTeamAliasesSection />);
    await screen.findByText('Red Devils');

    fireEvent.click(screen.getByRole('button', { name: /remove group/i }));
    expect(screen.queryByText('Red Devils')).not.toBeInTheDocument();
    expect(
      screen.getByText(/No alias groups configured/i),
    ).toBeInTheDocument();
  });
});
