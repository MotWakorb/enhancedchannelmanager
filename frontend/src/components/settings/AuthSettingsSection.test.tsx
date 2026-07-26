/**
 * Unit tests for AuthSettingsSection component.
 *
 * Written for bead enhancedchannelmanager-in1o0: the visible "Minimum
 * Password Length" text was a bare <label> with no htmlFor, and the
 * number input had no id/aria-label/aria-labelledby -- assistive
 * technology exposed a purposeless number input. These tests pin the
 * programmatic association (WCAG 1.3.1, 3.3.2, 4.1.2), the constraint
 * guidance exposure, and that the labeled input still drives the save
 * payload.
 *
 * Layer: component wiring (AuthSettingsSection rendered directly with
 * the api module mocked).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AuthSettingsSection } from './AuthSettingsSection';

vi.mock('../../services/api', () => ({
  getAuthSettings: vi.fn(),
  updateAuthSettings: vi.fn(),
}));

// Stable singleton: AuthSettingsSection's load effect lists `notifications`
// in its dependency array, so a mock returning a fresh object per render
// would re-trigger the load forever.
const { mockNotifications } = vi.hoisted(() => ({
  mockNotifications: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

import * as api from '../../services/api';
import type { AuthSettingsPublic } from '../../types';

const authSettings: AuthSettingsPublic = {
  require_auth: true,
  primary_auth_mode: 'local',
  local_enabled: true,
  local_min_password_length: 8,
  dispatcharr_enabled: false,
  dispatcharr_auto_create_users: true,
};

describe('AuthSettingsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getAuthSettings).mockResolvedValue(authSettings);
    vi.mocked(api.updateAuthSettings).mockResolvedValue({ message: 'ok' });
  });

  it('shows admin-required message for non-admins', () => {
    render(<AuthSettingsSection isAdmin={false} />);
    expect(screen.getByText(/admin access required/i)).toBeInTheDocument();
  });

  describe('minimum password length accessibility (bead in1o0)', () => {
    it('associates the visible "Minimum Password Length" label with the number input', async () => {
      render(<AuthSettingsSection isAdmin={true} />);

      // getByLabelText must locate the control uniquely -- the visible
      // label is programmatically associated via htmlFor/id.
      const input = await screen.findByLabelText('Minimum Password Length');
      expect(input).toHaveAttribute('type', 'number');
      expect(input).toHaveValue(8);
    });

    it('exposes the 6-32 constraint guidance to assistive technology', async () => {
      render(<AuthSettingsSection isAdmin={true} />);

      const input = await screen.findByLabelText('Minimum Password Length');
      expect(input).toHaveAccessibleDescription(/6-32/);
      expect(input).toHaveAttribute('min', '6');
      expect(input).toHaveAttribute('max', '32');
    });

    it('saves a value edited through the labeled input', async () => {
      render(<AuthSettingsSection isAdmin={true} />);

      const input = await screen.findByLabelText('Minimum Password Length');
      fireEvent.change(input, { target: { value: '12' } });

      fireEvent.click(screen.getByRole('button', { name: /save authentication settings/i }));

      await waitFor(() => {
        expect(api.updateAuthSettings).toHaveBeenCalledWith(
          expect.objectContaining({ local_min_password_length: 12 })
        );
      });
    });
  });
});
