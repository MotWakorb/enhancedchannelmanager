/**
 * Unit tests for M3UAccountModal — Cancel button (bead enhancedchannelmanager-09x38.3).
 *
 * The Add/Edit M3U Account modal footer had ONLY the "Create Account" /
 * "Save Changes" primary button — no Cancel/secondary, violating the
 * documented Cancel+Primary modal pattern (docs/css_guidelines.md → Modal
 * Patterns). Verify Cancel is present, closes without saving, and doesn't
 * disturb the primary action.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { M3UAccountModal } from './M3UAccountModal';

vi.mock('../services/api', () => ({
  createM3UAccount: vi.fn(),
  updateM3UAccount: vi.fn(),
  refreshM3UAccount: vi.fn(),
  uploadM3UFile: vi.fn(),
}));

import * as api from '../services/api';

const BASE_PROPS = {
  isOpen: true,
  onClose: vi.fn(),
  onSaved: vi.fn(),
  account: null,
  serverGroups: [],
};

describe('M3UAccountModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button alongside the primary Create Account button', () => {
    render(<M3UAccountModal {...BASE_PROPS} />);

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create Account' })).toBeInTheDocument();
  });

  it('clicking Cancel closes the modal without calling createM3UAccount', () => {
    const onClose = vi.fn();
    render(<M3UAccountModal {...BASE_PROPS} onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(api.createM3UAccount).not.toHaveBeenCalled();
    expect(api.updateM3UAccount).not.toHaveBeenCalled();
  });

  it('primary Create Account button is unaffected by the Cancel button addition', async () => {
    vi.mocked(api.createM3UAccount).mockResolvedValue({ id: 1 } as never);
    vi.mocked(api.refreshM3UAccount).mockResolvedValue(undefined as never);
    const onSaved = vi.fn();
    render(<M3UAccountModal {...BASE_PROPS} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText('Account Name'), { target: { value: 'My Provider' } });
    fireEvent.change(screen.getByLabelText('M3U URL'), { target: { value: 'http://example.com/get.php' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));

    await vi.waitFor(() => expect(api.createM3UAccount).toHaveBeenCalledTimes(1));
  });
});
