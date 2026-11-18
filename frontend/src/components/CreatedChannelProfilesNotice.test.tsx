import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CreatedChannelProfilesNotice } from './CreatedChannelProfilesNotice';

it('names unresolved profile intent and offers a membership-only retry', () => {
  const retry = vi.fn();
  render(<CreatedChannelProfilesNotice pending={[{ channelId: 50000, profileIds: [6] }]}
    busy={false} unknown storageUnavailable={false} profiles={[{ id: 6, name: 'Kids', channels: [] }]} onRetry={retry} onDismiss={vi.fn()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Profile assignments unresolved');
  expect(screen.getByText(/The channels are already created/)).toBeInTheDocument();
  expect(screen.getByText('Channel #50000: Kids')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry profile assignments' }));
  expect(retry).toHaveBeenCalledOnce();
});

it('disables retry while busy and reports unavailable tab storage', () => {
  render(<CreatedChannelProfilesNotice pending={[{ channelId: 50000, profileIds: [] }]}
    busy unknown={false} storageUnavailable profiles={[]} onRetry={vi.fn()} onDismiss={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Retry profile assignments' })).toBeDisabled();
  expect(screen.getByText(/Keep this page open/)).toBeInTheDocument();
  expect(screen.getByText('Channel #50000: No profiles')).toBeInTheDocument();
});

it('requires explicit confirmation before discarding saved intent', () => {
  const dismiss = vi.fn();
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<CreatedChannelProfilesNotice pending={[{ channelId: 50000, profileIds: [] }]}
    busy={false} unknown storageUnavailable={false} profiles={[]} onRetry={vi.fn()} onDismiss={dismiss} />);
  fireEvent.click(screen.getByRole('button', { name: 'Discard saved profile intent' }));
  expect(dismiss).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: 'Discard saved profile intent' }));
  expect(dismiss).toHaveBeenCalledOnce();
  confirm.mockRestore();
});

it('does not offer immediate profile writes underneath a new Edit Mode session', () => {
  render(<CreatedChannelProfilesNotice pending={[{ channelId: 50000, profileIds: [6] }]}
    busy={false} unknown storageUnavailable={false} profiles={[]} onRetry={vi.fn()} onDismiss={vi.fn()} retryUnavailable />);
  expect(screen.getByRole('button', { name: 'Retry profile assignments' })).toBeDisabled();
  expect(screen.getByText(/Finish the current Edit Mode session/)).toBeInTheDocument();
});
