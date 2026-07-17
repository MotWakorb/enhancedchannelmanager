/**
 * Unit tests for LogoModal — Cancel button (bead enhancedchannelmanager-09x38.3).
 *
 * The Add Logo modal had X-only header close, single primary "Add Logo"
 * footer button, yet is a mutating form (name/file/URL). Verify Cancel is
 * present, closes without saving, and doesn't disturb the primary action.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LogoModal } from './LogoModal';

vi.mock('../services/api', () => ({
  createLogo: vi.fn(),
  updateLogo: vi.fn(),
  uploadLogo: vi.fn(),
}));

import * as api from '../services/api';

const BASE_PROPS = {
  isOpen: true,
  onClose: vi.fn(),
  onSaved: vi.fn(),
  logo: null,
};

describe('LogoModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button alongside the primary Add Logo button', () => {
    render(<LogoModal {...BASE_PROPS} />);

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add Logo' })).toBeInTheDocument();
  });

  it('clicking Cancel closes the modal without calling createLogo/uploadLogo', () => {
    const onClose = vi.fn();
    render(<LogoModal {...BASE_PROPS} onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(api.createLogo).not.toHaveBeenCalled();
    expect(api.uploadLogo).not.toHaveBeenCalled();
    expect(api.updateLogo).not.toHaveBeenCalled();
  });

  it('primary Add Logo button is unaffected by the Cancel button addition', async () => {
    vi.mocked(api.createLogo).mockResolvedValue({ id: 1 } as never);
    const onSaved = vi.fn();
    render(<LogoModal {...BASE_PROPS} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText('Logo Name'), { target: { value: 'My Logo' } });
    fireEvent.change(screen.getByLabelText('Logo URL'), { target: { value: 'https://example.com/logo.png' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Logo' }));

    await vi.waitFor(() => expect(api.createLogo).toHaveBeenCalledTimes(1));
  });
});
