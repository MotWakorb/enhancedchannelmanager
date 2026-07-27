/**
 * Unit tests for M3ULinkedAccountsModal — Cancel button (bead
 * enhancedchannelmanager-09x38.3 audit follow-up). The footer only appears
 * once a link group is created/edited/deleted, and only ever shows the
 * mutating "Save Changes" primary button — header X-close was the only
 * escape hatch. Add a Cancel secondary so the footer follows the documented
 * Cancel+Primary pattern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { M3ULinkedAccountsModal } from './M3ULinkedAccountsModal';
import type { M3UAccount } from '../types';

function makeAccount(id: number, name: string): M3UAccount {
  return {
    id,
    name,
    account_type: 'STD',
    server_url: null,
    file_path: null,
    username: null,
    server_group: null,
    max_streams: 0,
    refresh_interval: 24,
    stale_stream_days: 7,
    enable_vod: false,
    auto_enable_new_groups_live: true,
    auto_enable_new_groups_vod: false,
    auto_enable_new_groups_series: false,
    is_active: true,
  } as unknown as M3UAccount;
}

const ACCOUNTS = [makeAccount(1, 'Provider A'), makeAccount(2, 'Provider B')];

const BASE_PROPS = {
  isOpen: true,
  onClose: vi.fn(),
  onSave: vi.fn(),
  accounts: ACCOUNTS,
  linkGroups: [],
};

describe('M3ULinkedAccountsModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button alongside the primary Save Changes button once a link group is created', () => {
    render(<M3ULinkedAccountsModal {...BASE_PROPS} />);

    fireEvent.click(screen.getByRole('button', { name: /Create Link Group/i }));
    fireEvent.click(screen.getByText('Provider A'));
    fireEvent.click(screen.getByText('Provider B'));
    fireEvent.click(screen.getByRole('button', { name: /^Create Group$/i }));

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeInTheDocument();
  });

  it('clicking Cancel closes the modal without calling onSave', () => {
    const onClose = vi.fn();
    const onSave = vi.fn();
    render(<M3ULinkedAccountsModal {...BASE_PROPS} onClose={onClose} onSave={onSave} />);

    fireEvent.click(screen.getByRole('button', { name: /Create Link Group/i }));
    fireEvent.click(screen.getByText('Provider A'));
    fireEvent.click(screen.getByText('Provider B'));
    fireEvent.click(screen.getByRole('button', { name: /^Create Group$/i }));

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });
});
