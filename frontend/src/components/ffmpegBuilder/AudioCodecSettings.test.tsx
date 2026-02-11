/**
 * TDD Tests for AudioCodecSettings component (Spec 1.4).
 *
 * These tests define the expected behavior of the AudioCodecSettings component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { AudioCodecSettings } from './AudioCodecSettings';
import type { AudioCodecSettings as AudioCodecSettingsType } from '../../types/ffmpegBuilder';

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

const defaultSettings: AudioCodecSettingsType = {
  codec: 'aac',
  bitrate: '192k',
  sampleRate: 48000,
  channels: 2,
  channelLayout: 'stereo',
};

function renderAudioCodecSettings(
  overrides: Partial<AudioCodecSettingsType> = {},
  props: { onChange?: ReturnType<typeof vi.fn> } = {}
) {
  const onChange = props.onChange ?? vi.fn();
  const settings: AudioCodecSettingsType = { ...defaultSettings, ...overrides };
  return {
    onChange,
    ...render(<AudioCodecSettings value={settings} onChange={onChange} />),
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('AudioCodecSettings', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders codec selector', () => {
      renderAudioCodecSettings();

      expect(screen.getByLabelText(/audio codec/i)).toBeInTheDocument();
    });

    it('renders bitrate input', () => {
      renderAudioCodecSettings();

      expect(screen.getByLabelText(/bitrate/i)).toBeInTheDocument();
    });

    it('renders sample rate selector', () => {
      renderAudioCodecSettings();

      expect(screen.getByLabelText(/sample rate/i)).toBeInTheDocument();
    });

    it('renders channels selector', () => {
      renderAudioCodecSettings();

      expect(screen.getByLabelText(/channels/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Codec Selection
  // -------------------------------------------------------------------------
  describe('codec selection', () => {
    it('can select AAC', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/audio codec/i));
      await user.click(screen.getByRole('option', { name: /aac/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'aac' })
      );
    });

    it('can select AC3', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/audio codec/i));
      await user.click(screen.getByRole('option', { name: /^AC3$/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'ac3' })
      );
    });

    it('can select EAC3', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/audio codec/i));
      await user.click(screen.getByRole('option', { name: /eac3/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'eac3' })
      );
    });
  });

  // -------------------------------------------------------------------------
  // Bitrate
  // -------------------------------------------------------------------------
  describe('bitrate', () => {
    it('shows bitrate for lossy codecs', () => {
      renderAudioCodecSettings({ codec: 'aac', bitrate: '192k' });

      expect(screen.getByLabelText(/bitrate/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/bitrate/i)).toHaveValue('192k');
    });

    it('validates bitrate format (e.g., 192k)', async () => {
      const user = userEvent.setup();
      renderAudioCodecSettings({ codec: 'aac' });

      const bitrateInput = screen.getByLabelText(/bitrate/i);
      await user.clear(bitrateInput);
      await user.type(bitrateInput, 'invalid');

      await waitFor(() => {
        expect(screen.getByText(/valid bitrate|format/i)).toBeInTheDocument();
      });
    });

    it('common presets (128k, 192k, 256k, 320k)', () => {
      renderAudioCodecSettings({ codec: 'aac' });

      // Should have quick-select bitrate presets
      expect(screen.getByRole('button', { name: /128k/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /192k/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /256k/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /320k/i })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Sample Rate
  // -------------------------------------------------------------------------
  describe('sample rate', () => {
    it('can select 44100', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/sample rate/i));
      await user.click(screen.getByRole('option', { name: /44100/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ sampleRate: 44100 })
      );
    });

    it('can select 48000', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/sample rate/i));
      await user.click(screen.getByRole('option', { name: /48000/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ sampleRate: 48000 })
      );
    });

    it('shows current sample rate', () => {
      renderAudioCodecSettings({ sampleRate: 48000 });

      expect(screen.getByLabelText(/sample rate/i)).toHaveTextContent(/48000/);
    });
  });

  // -------------------------------------------------------------------------
  // Channels
  // -------------------------------------------------------------------------
  describe('channels', () => {
    it('can select mono', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/channels/i));
      await user.click(screen.getByRole('option', { name: /mono/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ channels: 1 })
      );
    });

    it('can select stereo', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/channels/i));
      await user.click(screen.getByRole('option', { name: /stereo/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ channels: 2 })
      );
    });

    it('can select 5.1', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/channels/i));
      await user.click(screen.getByRole('option', { name: /5\.1/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ channels: 6 })
      );
    });

    it('shows channel layout label', () => {
      renderAudioCodecSettings({ channels: 2, channelLayout: 'stereo' });

      expect(screen.getByLabelText(/channels/i)).toHaveTextContent(/stereo/i);
    });
  });

  // -------------------------------------------------------------------------
  // Stream Copy
  // -------------------------------------------------------------------------
  describe('stream copy', () => {
    it('copy disables all audio settings', () => {
      renderAudioCodecSettings({ codec: 'copy' });

      // All audio settings should be disabled when copy is selected
      const bitrateInput = screen.queryByLabelText(/bitrate/i);
      const sampleRateInput = screen.queryByLabelText(/sample rate/i);
      const channelsInput = screen.queryByLabelText(/channels/i);

      // Either disabled or not rendered at all
      if (bitrateInput) expect(bitrateInput).toBeDisabled();
      if (sampleRateInput) expect(sampleRateInput).toBeDisabled();
      if (channelsInput) expect(channelsInput).toBeDisabled();

      // Should show copy indication
      expect(screen.getByText(/stream copy|no re-?encoding/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onChange with AudioCodecSettings', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioCodecSettings();

      await user.click(screen.getByLabelText(/audio codec/i));
      await user.click(screen.getByRole('option', { name: /^AC3$/i }));

      expect(onChange).toHaveBeenCalledTimes(1);
      const callArg = onChange.mock.calls[0][0];
      expect(callArg).toHaveProperty('codec', 'ac3');
      expect(callArg).toHaveProperty('sampleRate');
      expect(callArg).toHaveProperty('channels');
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icons', () => {
      renderAudioCodecSettings();

      const infoIcons = screen.getAllByTestId('info-icon');
      // At minimum: codec, bitrate, sample rate, channels
      expect(infoIcons.length).toBeGreaterThanOrEqual(4);
    });

    it('codec tooltip explains format', async () => {
      const user = userEvent.setup();
      renderAudioCodecSettings();

      const codecLabel = screen.getByText(/audio codec/i);
      const codecInfo = codecLabel
        .closest('.form-field, .form-group, .setting-row')!
        .querySelector('[data-testid="info-icon"]')!;
      await user.hover(codecInfo);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent!.length).toBeGreaterThan(10);
      });
    });

    it('bitrate tooltip explains quality impact', async () => {
      const user = userEvent.setup();
      renderAudioCodecSettings();

      const bitrateLabel = screen.getByText(/^Bitrate$/i);
      const bitrateInfo = bitrateLabel
        .closest('.form-field, .form-group, .setting-row')!
        .querySelector('[data-testid="info-icon"]')!;
      await user.hover(bitrateInfo);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent).toMatch(/quality|higher|lower|size/i);
      });
    });
  });
});
