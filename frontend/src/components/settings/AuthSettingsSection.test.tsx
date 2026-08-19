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
  describe('disabling authentication (bead enhancedchannelmanager-04c0u.12)', () => {
    // Turning Require Authentication off leaves the whole instance open to
    // anyone who can reach it. It is saved by the same generic "Save" button
    // as every other field on the page, so without a scoped confirmation the
    // most dangerous setting here is the easiest one to change by accident.
    const disableAuthAndSave = async () => {
      render(<AuthSettingsSection isAdmin={true} />);
      const toggle = await screen.findByLabelText('Require Authentication');
      fireEvent.click(toggle);
      fireEvent.click(screen.getByRole('button', { name: /save authentication settings/i }));
    };

    it('does not save until the operator confirms, and names what is lost', async () => {
      await disableAuthAndSave();

      const dialog = await screen.findByRole('dialog', { name: /disable authentication/i });
      expect(dialog).toHaveTextContent(/anyone who can reach this ECM instance/i);
      expect(api.updateAuthSettings).not.toHaveBeenCalled();
    });

    it('saves require_auth false once the exact phrase is typed', async () => {
      await disableAuthAndSave();
      await screen.findByRole('dialog', { name: /disable authentication/i });

      fireEvent.change(screen.getByLabelText(/type DISABLE AUTHENTICATION to confirm/i), {
        target: { value: 'DISABLE AUTHENTICATION' },
      });
      fireEvent.click(screen.getByRole('button', { name: /^disable authentication$/i }));

      await waitFor(() =>
        expect(api.updateAuthSettings).toHaveBeenCalledWith(
          expect.objectContaining({ require_auth: false }),
        ),
      );
    });

    it('abandons the change when the confirmation is cancelled', async () => {
      await disableAuthAndSave();
      await screen.findByRole('dialog', { name: /disable authentication/i });

      fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

      await waitFor(() =>
        expect(screen.queryByRole('dialog', { name: /disable authentication/i })).not.toBeInTheDocument(),
      );
      expect(api.updateAuthSettings).not.toHaveBeenCalled();
    });

    it('names the MCP-capability caveat that turning auth off removes', async () => {
      // The MCP guide promises a list of things a stolen MCP key cannot do.
      // Most of that list is enforced only while auth is required, and this
      // dialog is where the operator decides. Saying it in the guide alone
      // puts it where the decision is not being made.
      await disableAuthAndSave();

      const dialog = await screen.findByRole('dialog', { name: /disable authentication/i });
      expect(dialog).toHaveTextContent(/MCP/i);
      expect(dialog).toHaveTextContent(/backup/i);
    });

    it('does not re-confirm on every save once authentication is already off', async () => {
      // Gating on `!requireAuth` rather than on the transition made the dialog
      // fire on every unrelated save while auth was off — habituation training
      // on exactly the least-protected instances (bead 04c0u.12).
      vi.mocked(api.getAuthSettings).mockResolvedValue({ ...authSettings, require_auth: false });
      render(<AuthSettingsSection isAdmin={true} />);

      const input = await screen.findByLabelText('Minimum Password Length');
      fireEvent.change(input, { target: { value: '12' } });
      fireEvent.click(screen.getByRole('button', { name: /save authentication settings/i }));

      await waitFor(() =>
        expect(api.updateAuthSettings).toHaveBeenCalledWith(
          expect.objectContaining({ require_auth: false, local_min_password_length: 12 }),
        ),
      );
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('does not re-confirm on the next save after the disable has been saved', async () => {
      // The gate reads the last persisted value, so a successful save has to
      // move it. Otherwise the very next save re-prompts, which is the same
      // habituation defect one step later.
      await disableAuthAndSave();
      await screen.findByRole('dialog', { name: /disable authentication/i });
      fireEvent.change(screen.getByLabelText(/type DISABLE AUTHENTICATION to confirm/i), {
        target: { value: 'DISABLE AUTHENTICATION' },
      });
      fireEvent.click(screen.getByRole('button', { name: /^disable authentication$/i }));
      await waitFor(() => expect(api.updateAuthSettings).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /save authentication settings/i }));

      await waitFor(() => expect(api.updateAuthSettings).toHaveBeenCalledTimes(2));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('saves without a confirmation when authentication stays on', async () => {
      render(<AuthSettingsSection isAdmin={true} />);
      await screen.findByLabelText('Require Authentication');
      fireEvent.click(screen.getByRole('button', { name: /save authentication settings/i }));

      await waitFor(() =>
        expect(api.updateAuthSettings).toHaveBeenCalledWith(
          expect.objectContaining({ require_auth: true }),
        ),
      );
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});
