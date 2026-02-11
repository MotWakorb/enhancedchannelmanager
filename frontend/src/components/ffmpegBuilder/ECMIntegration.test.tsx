/**
 * TDD Tests for ECMIntegration component (Spec 1.12).
 *
 * These tests define the expected behavior of the ECMIntegration component
 * BEFORE implementation. They will FAIL until the component is built.
 *
 * ECMIntegration manages FFMPEG channel profiles -- creating, editing, and
 * deleting profiles that link saved FFMPEG configs to channels/groups.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
  ffmpegMockDataStore,
  createMockFFMPEGConfig,
} from '../../test/mocks/server';
import { ECMIntegration } from './ECMIntegration';
import type { FFMPEGChannelProfile, FFMPEGBuilderState } from '../../types/ffmpegBuilder';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultProfile: FFMPEGChannelProfile = {
  id: 1,
  name: 'Test Profile',
  configId: 100,
  applyTo: 'all',
  enabled: true,
};

function seedConfigs(): void {
  ffmpegMockDataStore.configs.push(
    createMockFFMPEGConfig({ id: 100, name: 'Base Config' }),
    createMockFFMPEGConfig({ id: 101, name: 'Streaming Config' }),
  );
}

function renderECMIntegration(
  props: {
    profiles?: FFMPEGChannelProfile[];
    onProfileCreate?: ReturnType<typeof vi.fn>;
    onProfileUpdate?: ReturnType<typeof vi.fn>;
    onProfileDelete?: ReturnType<typeof vi.fn>;
    onSelectProfile?: ReturnType<typeof vi.fn>;
  } = {}
) {
  const onProfileCreate = props.onProfileCreate ?? vi.fn();
  const onProfileUpdate = props.onProfileUpdate ?? vi.fn();
  const onProfileDelete = props.onProfileDelete ?? vi.fn();
  const onSelectProfile = props.onSelectProfile ?? vi.fn();

  return {
    onProfileCreate,
    onProfileUpdate,
    onProfileDelete,
    onSelectProfile,
    ...render(
      <ECMIntegration
        profiles={props.profiles ?? []}
        onProfileCreate={onProfileCreate}
        onProfileUpdate={onProfileUpdate}
        onProfileDelete={onProfileDelete}
        onSelectProfile={onSelectProfile}
      />
    ),
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('ECMIntegration', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders profile selector', () => {
      renderECMIntegration();

      expect(screen.getByTestId('profile-select')).toBeInTheDocument();
    });

    it('renders apply-to selector', () => {
      renderECMIntegration();

      expect(screen.getByTestId('apply-to-select')).toBeInTheDocument();
    });

    it('renders channel/group selector', () => {
      renderECMIntegration();

      // Should render at least one of channel or group selector area
      const channelSelect = screen.queryByTestId('channel-select');
      const groupSelect = screen.queryByTestId('group-select');
      expect(channelSelect || groupSelect).toBeTruthy();
    });

    it('shows existing profiles', () => {
      renderECMIntegration({
        profiles: [
          defaultProfile,
          { id: 2, name: 'Another Profile', configId: 101, applyTo: 'group', enabled: false },
        ],
      });

      // The selected profile name appears in the dropdown trigger
      expect(screen.getAllByText('Test Profile').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('Another Profile')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Profile Management
  // -------------------------------------------------------------------------
  describe('profile management', () => {
    it('can create new profile', async () => {
      const user = userEvent.setup();
      seedConfigs();
      const { onProfileCreate } = renderECMIntegration();

      const createBtn = screen.getByRole('button', { name: /create|add|new/i });
      await user.click(createBtn);

      // Fill in profile name
      const nameInput = screen.getByLabelText(/profile name|name/i);
      await user.clear(nameInput);
      await user.type(nameInput, 'New Profile');

      // Submit
      const saveBtn = screen.getByRole('button', { name: /save|create|confirm/i });
      await user.click(saveBtn);

      await waitFor(() => {
        expect(onProfileCreate).toHaveBeenCalledWith(
          expect.objectContaining({ name: 'New Profile' })
        );
      });
    });

    it('can edit profile', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const editBtn = screen.getByRole('button', { name: /edit/i });
      await user.click(editBtn);

      const nameInput = screen.getByLabelText(/profile name|name/i);
      await user.clear(nameInput);
      await user.type(nameInput, 'Updated Profile');

      const saveBtn = screen.getByRole('button', { name: /save|update|confirm/i });
      await user.click(saveBtn);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ name: 'Updated Profile' })
        );
      });
    });

    it('can delete profile', async () => {
      const user = userEvent.setup();
      const { onProfileDelete } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const deleteBtn = screen.getByRole('button', { name: /delete|remove/i });
      await user.click(deleteBtn);

      // Confirm deletion if dialog appears
      const confirmBtn = screen.queryByRole('button', { name: /confirm|yes|delete/i });
      if (confirmBtn) {
        await user.click(confirmBtn);
      }

      await waitFor(() => {
        expect(onProfileDelete).toHaveBeenCalledWith(defaultProfile.id);
      });
    });

    it('profile links to saved config', async () => {
      seedConfigs();
      renderECMIntegration({
        profiles: [defaultProfile],
      });

      // The profile should reference the saved config name (async fetch)
      await waitFor(() => {
        expect(screen.getByText(/Base Config/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Apply-To Targeting
  // -------------------------------------------------------------------------
  describe('apply-to targeting', () => {
    it('can select all channels', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const applyToSelect = screen.getByTestId('apply-to-select');
      await user.click(applyToSelect);

      const allOption = screen.getByRole('option', { name: /all/i });
      await user.click(allOption);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ applyTo: 'all' })
        );
      });
    });

    it('can select specific group', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const applyToSelect = screen.getByTestId('apply-to-select');
      await user.click(applyToSelect);

      const groupOption = screen.getByText(/group/i);
      await user.click(groupOption);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ applyTo: 'group' })
        );
      });
    });

    it('can select specific channel', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const applyToSelect = screen.getByTestId('apply-to-select');
      await user.click(applyToSelect);

      const channelOption = screen.getByRole('option', { name: /channel/i });
      await user.click(channelOption);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ applyTo: 'channel' })
        );
      });
    });

    it('shows channel list when channel selected', async () => {
      renderECMIntegration({
        profiles: [{ ...defaultProfile, applyTo: 'channel' }],
      });

      await waitFor(() => {
        expect(screen.getByTestId('channel-select')).toBeInTheDocument();
      });
    });

    it('shows group list when group selected', async () => {
      renderECMIntegration({
        profiles: [{ ...defaultProfile, applyTo: 'group' }],
      });

      await waitFor(() => {
        expect(screen.getByTestId('group-select')).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Profile Activation
  // -------------------------------------------------------------------------
  describe('profile activation', () => {
    it('can enable profile', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [{ ...defaultProfile, enabled: false }],
      });

      const enableToggle = screen.getByRole('checkbox', { name: /enable|active/i });
      await user.click(enableToggle);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ enabled: true })
        );
      });
    });

    it('can disable profile', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const disableToggle = screen.getByRole('checkbox', { name: /enable|active/i });
      await user.click(disableToggle);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ enabled: false })
        );
      });
    });

    it('enabled profile shows active badge', () => {
      renderECMIntegration({
        profiles: [defaultProfile],
      });

      expect(screen.getByText(/active|enabled/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Integration with Builder
  // -------------------------------------------------------------------------
  describe('integration with builder', () => {
    it('selecting profile loads its config', async () => {
      const user = userEvent.setup();
      seedConfigs();
      const { onSelectProfile } = renderECMIntegration({
        profiles: [
          defaultProfile,
          { id: 2, name: 'Streaming Profile', configId: 101, applyTo: 'all', enabled: true },
        ],
      });

      // Select the second profile
      const profileSelect = screen.getByTestId('profile-select');
      await user.click(profileSelect);

      const streamingOption = screen.getByText('Streaming Profile');
      await user.click(streamingOption);

      await waitFor(() => {
        expect(onSelectProfile).toHaveBeenCalledWith(
          expect.objectContaining({ id: 2, configId: 101 })
        );
      });
    });

    it('modifying config updates profile', async () => {
      const user = userEvent.setup();
      seedConfigs();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      // Trigger a config change via the profile editor
      const editBtn = screen.getByRole('button', { name: /edit/i });
      await user.click(editBtn);

      // Change the name to trigger an update
      const nameInput = screen.getByLabelText(/profile name|name/i);
      await user.clear(nameInput);
      await user.type(nameInput, 'Modified Profile');

      const saveBtn = screen.getByRole('button', { name: /save|update|confirm/i });
      await user.click(saveBtn);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalled();
      });
    });

    it('shows unsaved changes indicator', async () => {
      const user = userEvent.setup();
      seedConfigs();
      renderECMIntegration({
        profiles: [defaultProfile],
      });

      const editBtn = screen.getByRole('button', { name: /edit/i });
      await user.click(editBtn);

      const nameInput = screen.getByLabelText(/profile name|name/i);
      await user.clear(nameInput);
      await user.type(nameInput, 'Changed');

      await waitFor(() => {
        expect(screen.getByText(/unsaved|modified|changed/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onProfileCreate', async () => {
      const user = userEvent.setup();
      seedConfigs();
      const { onProfileCreate } = renderECMIntegration();

      const createBtn = screen.getByRole('button', { name: /create|add|new/i });
      await user.click(createBtn);

      const nameInput = screen.getByLabelText(/profile name|name/i);
      await user.type(nameInput, 'Callback Test');

      const saveBtn = screen.getByRole('button', { name: /save|create|confirm/i });
      await user.click(saveBtn);

      await waitFor(() => {
        expect(onProfileCreate).toHaveBeenCalledTimes(1);
      });
    });

    it('calls onProfileUpdate', async () => {
      const user = userEvent.setup();
      const { onProfileUpdate } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const editBtn = screen.getByRole('button', { name: /edit/i });
      await user.click(editBtn);

      const nameInput = screen.getByLabelText(/profile name|name/i);
      await user.clear(nameInput);
      await user.type(nameInput, 'Updated');

      const saveBtn = screen.getByRole('button', { name: /save|update|confirm/i });
      await user.click(saveBtn);

      await waitFor(() => {
        expect(onProfileUpdate).toHaveBeenCalledTimes(1);
      });
    });

    it('calls onProfileDelete', async () => {
      const user = userEvent.setup();
      const { onProfileDelete } = renderECMIntegration({
        profiles: [defaultProfile],
      });

      const deleteBtn = screen.getByRole('button', { name: /delete|remove/i });
      await user.click(deleteBtn);

      // Confirm deletion
      const confirmBtn = screen.queryByRole('button', { name: /confirm|yes|delete/i });
      if (confirmBtn) {
        await user.click(confirmBtn);
      }

      await waitFor(() => {
        expect(onProfileDelete).toHaveBeenCalledTimes(1);
      });
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icons for profile settings', () => {
      renderECMIntegration();

      const infoIcons = screen.getAllByTestId('info-icon');
      expect(infoIcons.length).toBeGreaterThanOrEqual(1);
    });

    it('tooltip explains apply-to options', async () => {
      const user = userEvent.setup();
      renderECMIntegration();

      const infoIcons = screen.getAllByTestId('info-icon');
      // Hover over the first info icon near apply-to
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent!.length).toBeGreaterThan(5);
      });
    });
  });
});
