/**
 * The M3U password hint must be honest about whether there is a stored
 * password to keep (bead enhancedchannelmanager-tsbdq — drill run
 * 2026-08-06-run9 finding P-7).
 *
 * WHAT WENT WRONG. A standard (redacted) DBAS artifact deliberately carries no
 * provider credentials, so after a restore the account's password is genuinely
 * empty — the restore report says so, naming the exact account and field in
 * `credentials_needing_reentry`. The edit form still told the operator
 * "Leave blank to keep current". There is nothing current to keep, and taking
 * the hint literally guarantees the account keeps failing to authenticate —
 * in exactly the situation the documented Step 6a recovery puts them in.
 *
 * The signal is the account's own `password` field. It is truthful, not a
 * sentinel: the drill's inventory captures recorded `present` (with a stable
 * fingerprint) before the backup and `absent` after a redacted restore, on the
 * same account — see `~/ecm/backup-restore-runs/2026-08-06-run9/inventory/`
 * `before.json` vs `after-standard-PRE-recovery.json`. The generic wording
 * stays for the case where a value really does exist, because there it is
 * correct.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { M3UAccountModal } from './M3UAccountModal';
import type { M3UAccount } from '../types';

vi.mock('../services/api', () => ({
  createM3UAccount: vi.fn(),
  updateM3UAccount: vi.fn(),
  refreshM3UAccount: vi.fn(),
  uploadM3UFile: vi.fn(),
}));

/** The drill's XtreamCodes account, as the API returns it. */
function xcAccount(password: string | null): M3UAccount {
  return {
    id: 7,
    name: 'Infinity',
    server_url: 'https://infinity.gives',
    file_path: null,
    server_group: null,
    max_streams: 0,
    is_active: true,
    created_at: '2026-08-06T08:47:00Z',
    updated_at: null,
    user_agent: null,
    profiles: [],
    locked: false,
    channel_groups: [],
    refresh_interval: 24,
    custom_properties: null,
    account_type: 'XC',
    username: 'run9user',
    password,
    stale_stream_days: 7,
    priority: 0,
    status: 'success',
    last_message: null,
    enable_vod: false,
    auto_enable_new_groups_live: false,
    auto_enable_new_groups_vod: false,
    auto_enable_new_groups_series: false,
  };
}

const BASE_PROPS = {
  isOpen: true,
  onClose: vi.fn(),
  onSaved: vi.fn(),
  serverGroups: [],
};

const MISSING_HINT = /no stored password/i;
const KEEP_CURRENT_HINT = /leave blank to keep current/i;

function passwordField(): HTMLInputElement {
  return screen.getByLabelText('Password') as HTMLInputElement;
}

describe('M3UAccountModal — password hint (bead tsbdq)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('when the stored credential is absent (post redacted restore)', () => {
    it('says the password is missing and must be entered, instead of offering to keep it', () => {
      render(<M3UAccountModal {...BASE_PROPS} account={xcAccount(null)} />);

      expect(passwordField().value).toBe('');
      expect(passwordField().placeholder).toMatch(MISSING_HINT);
      expect(passwordField().placeholder).not.toMatch(KEEP_CURRENT_HINT);
      expect(screen.getByText(/must be re-entered/i)).toBeInTheDocument();
      expect(screen.queryByText(/only fill in if changing password/i)).not.toBeInTheDocument();
    });

    it('treats an empty string the same as null — both mean "no credential"', () => {
      render(<M3UAccountModal {...BASE_PROPS} account={xcAccount('')} />);

      expect(passwordField().placeholder).toMatch(MISSING_HINT);
    });
  });

  describe('when a stored credential exists', () => {
    it('keeps the generic leave-blank wording, which is correct there', () => {
      render(<M3UAccountModal {...BASE_PROPS} account={xcAccount('s3cret')} />);

      expect(passwordField().value).toBe('');
      expect(passwordField().placeholder).toMatch(KEEP_CURRENT_HINT);
      expect(passwordField().placeholder).not.toMatch(MISSING_HINT);
      expect(screen.getByText(/only fill in if changing password/i)).toBeInTheDocument();
    });
  });

  describe('when creating a new account', () => {
    it('says neither — there is no stored credential to reason about yet', () => {
      render(<M3UAccountModal {...BASE_PROPS} account={null} />);
      // Account type defaults to STD; XtreamCodes is what exposes the field.
      fireEvent.click(screen.getByLabelText('XtreamCodes'));

      expect(passwordField().placeholder).not.toMatch(MISSING_HINT);
      expect(passwordField().placeholder).not.toMatch(KEEP_CURRENT_HINT);
      expect(screen.queryByText(/must be re-entered/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/only fill in if changing password/i)).not.toBeInTheDocument();
    });
  });
});
