/**
 * TDD Tests for VideoCodecSettings component (Spec 1.3).
 *
 * These tests define the expected behavior of the VideoCodecSettings component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { VideoCodecSettings } from './VideoCodecSettings';
import type { VideoCodecSettings as VideoCodecSettingsType } from '../../types/ffmpegBuilder';

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

const defaultSettings: VideoCodecSettingsType = {
  codec: 'libx264',
  rateControl: 'crf',
  crf: 23,
  preset: 'medium',
};

function renderVideoCodecSettings(
  overrides: Partial<VideoCodecSettingsType> = {},
  props: { onChange?: ReturnType<typeof vi.fn>; hwCapabilities?: string[] } = {}
) {
  const onChange = props.onChange ?? vi.fn();
  const settings: VideoCodecSettingsType = { ...defaultSettings, ...overrides };
  return {
    onChange,
    ...render(
      <VideoCodecSettings
        value={settings}
        onChange={onChange}
        hwCapabilities={props.hwCapabilities}
      />
    ),
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('VideoCodecSettings', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders codec selector', () => {
      renderVideoCodecSettings();

      expect(screen.getByLabelText(/video codec/i)).toBeInTheDocument();
    });

    it('renders preset selector', () => {
      renderVideoCodecSettings();

      expect(screen.getByLabelText(/preset/i)).toBeInTheDocument();
    });

    it('renders rate control selector', () => {
      renderVideoCodecSettings();

      expect(screen.getByLabelText(/rate control/i)).toBeInTheDocument();
    });

    it('renders CRF slider', () => {
      renderVideoCodecSettings({ rateControl: 'crf', crf: 23 });

      expect(screen.getByLabelText(/crf/i)).toBeInTheDocument();
    });

    it('shows default libx264', () => {
      renderVideoCodecSettings();

      expect(screen.getByText(/libx264/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Software Codecs
  // -------------------------------------------------------------------------
  describe('software codecs', () => {
    it('can select libx264', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /libx264/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'libx264' })
      );
    });

    it('can select libx265', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /libx265/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'libx265' })
      );
    });

    it('can select VP9', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /vp9/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'libvpx-vp9' })
      );
    });

    it('can select AV1 (libaom)', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /libaom/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'libaom-av1' })
      );
    });

    it('can select SVT-AV1', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /svt-av1/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'libsvtav1' })
      );
    });

    it('shows codec description on selection', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /libx265/i }));

      await waitFor(() => {
        // Should show a description of the selected codec
        expect(screen.getByText(/h\.?265|hevc/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Hardware Codecs - NVIDIA
  // -------------------------------------------------------------------------
  describe('hardware codecs - NVIDIA', () => {
    it('can select h264_nvenc', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_nvenc', 'hevc_nvenc'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /h264_nvenc/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'h264_nvenc' })
      );
    });

    it('can select hevc_nvenc', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_nvenc', 'hevc_nvenc'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /hevc_nvenc/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'hevc_nvenc' })
      );
    });

    it('shows NVENC-specific options (rc, spatial-aq, temporal-aq)', () => {
      renderVideoCodecSettings({
        codec: 'h264_nvenc',
        rateControl: 'cq',
      });

      expect(screen.getByLabelText(/spatial.?aq/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/temporal.?aq/i)).toBeInTheDocument();
    });

    it('shows NVENC presets (p1-p7)', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings({ codec: 'h264_nvenc' });

      await user.click(screen.getByLabelText(/preset/i));

      // NVENC uses p1 through p7 presets
      expect(screen.getByRole('option', { name: /p1/i })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /p4/i })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /p7/i })).toBeInTheDocument();
    });

    it('hides CRF for NVENC shows CQ instead', () => {
      renderVideoCodecSettings({
        codec: 'h264_nvenc',
        rateControl: 'cq',
        cq: 20,
      });

      // CRF should not be present for NVENC
      expect(screen.queryByLabelText(/^crf$/i)).not.toBeInTheDocument();
      // CQ should be shown instead
      expect(screen.getByLabelText(/cq/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Hardware Codecs - Intel QSV
  // -------------------------------------------------------------------------
  describe('hardware codecs - Intel QSV', () => {
    it('can select h264_qsv', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_qsv', 'hevc_qsv'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /h264_qsv/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'h264_qsv' })
      );
    });

    it('can select hevc_qsv', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_qsv', 'hevc_qsv'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /hevc_qsv/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'hevc_qsv' })
      );
    });

    it('shows QSV global_quality option', () => {
      renderVideoCodecSettings({
        codec: 'h264_qsv',
        rateControl: 'global_quality',
        globalQuality: 25,
      });

      expect(screen.getByLabelText(/global.?quality/i)).toBeInTheDocument();
    });

    it('shows QSV look_ahead option', () => {
      renderVideoCodecSettings({
        codec: 'h264_qsv',
        rateControl: 'global_quality',
        lookAhead: 20,
      });

      expect(screen.getByLabelText(/look.?ahead/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Hardware Codecs - VAAPI
  // -------------------------------------------------------------------------
  describe('hardware codecs - VAAPI', () => {
    it('can select h264_vaapi', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_vaapi', 'hevc_vaapi'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /h264_vaapi/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'h264_vaapi' })
      );
    });

    it('can select hevc_vaapi', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_vaapi', 'hevc_vaapi'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /hevc_vaapi/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'hevc_vaapi' })
      );
    });

    it('shows VAAPI quality option', () => {
      renderVideoCodecSettings({
        codec: 'h264_vaapi',
        rateControl: 'qp',
        qp: 25,
      });

      expect(screen.getByLabelText(/quality|qp/i)).toBeInTheDocument();
    });

    it('shows compression_level option', () => {
      renderVideoCodecSettings({
        codec: 'h264_vaapi',
        compressionLevel: 5,
      });

      expect(screen.getByLabelText(/compression.?level/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Codec Categories
  // -------------------------------------------------------------------------
  describe('codec categories', () => {
    it('groups codecs by category (Software, NVIDIA, Intel QSV, VAAPI)', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings(
        {},
        { hwCapabilities: ['h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'hevc_qsv', 'h264_vaapi', 'hevc_vaapi'] }
      );

      await user.click(screen.getByLabelText(/video codec/i));

      expect(screen.getAllByText(/software/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/nvidia/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/intel qsv/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/vaapi/i).length).toBeGreaterThanOrEqual(1);
    });

    it('disables unavailable HW codecs', async () => {
      const user = userEvent.setup();
      // No HW capabilities provided -- HW codecs should be disabled
      renderVideoCodecSettings({}, { hwCapabilities: [] });

      await user.click(screen.getByLabelText(/video codec/i));

      const nvencOption = screen.getByRole('option', { name: /h264_nvenc/i });
      expect(nvencOption).toHaveAttribute('aria-disabled', 'true');
    });

    it('shows unavailability reason', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings({}, { hwCapabilities: [] });

      await user.click(screen.getByLabelText(/video codec/i));

      // Should show reason why HW codec is unavailable
      expect(screen.getByText(/not available|no .* hardware detected/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Rate Control
  // -------------------------------------------------------------------------
  describe('rate control', () => {
    it('can select CRF mode', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/rate control/i));
      await user.click(screen.getByRole('option', { name: /crf/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ rateControl: 'crf' })
      );
    });

    it('can select CBR mode', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/rate control/i));
      await user.click(screen.getByRole('option', { name: /cbr/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ rateControl: 'cbr' })
      );
    });

    it('can select VBR mode', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/rate control/i));
      await user.click(screen.getByRole('option', { name: /vbr/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ rateControl: 'vbr' })
      );
    });

    it('shows bitrate fields for CBR/VBR', () => {
      renderVideoCodecSettings({ rateControl: 'cbr', bitrate: '5M' });

      expect(screen.getByLabelText(/bitrate/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/bitrate/i)).toHaveValue('5M');
    });

    it('shows CRF slider for CRF mode', () => {
      renderVideoCodecSettings({ rateControl: 'crf', crf: 23 });

      const crfSlider = screen.getByLabelText(/crf/i);
      expect(crfSlider).toBeInTheDocument();
      expect(crfSlider).toHaveAttribute('type', 'range');
    });

    it('CRF range 0-51', () => {
      renderVideoCodecSettings({ rateControl: 'crf', crf: 23 });

      const crfSlider = screen.getByLabelText(/crf/i);
      expect(crfSlider).toHaveAttribute('min', '0');
      expect(crfSlider).toHaveAttribute('max', '51');
    });

    it('shows maxrate/bufsize for VBR', () => {
      renderVideoCodecSettings({
        rateControl: 'vbr',
        bitrate: '5M',
        maxBitrate: '8M',
        bufsize: '10M',
      });

      expect(screen.getByLabelText(/max.?bitrate|maxrate/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/buffer.?size|bufsize/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Advanced Options
  // -------------------------------------------------------------------------
  describe('advanced options', () => {
    it('can set pixel format', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/pixel format/i));
      await user.click(screen.getByRole('option', { name: /yuv420p/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ pixelFormat: 'yuv420p' })
      );
    });

    it('can set keyframe interval', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      const keyframeInput = screen.getByLabelText('Keyframe Interval');
      await user.clear(keyframeInput);
      await user.type(keyframeInput, '250');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.objectContaining({ keyframeInterval: 250 })
        );
      });
    });

    it('can set B-frames', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      const bframesInput = screen.getByLabelText(/b.?frames/i);
      await user.clear(bframesInput);
      await user.type(bframesInput, '3');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.objectContaining({ bFrames: 3 })
        );
      });
    });

    it('can set profile', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/profile/i));
      await user.click(screen.getByRole('option', { name: /high/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ profile: 'high' })
      );
    });

    it('can set level', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/level/i));
      await user.click(screen.getByRole('option', { name: /4\.1/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ level: '4.1' })
      );
    });

    it('can set tune', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/tune/i));
      await user.click(screen.getByRole('option', { name: /film/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ tune: 'film' })
      );
    });
  });

  // -------------------------------------------------------------------------
  // Stream Copy
  // -------------------------------------------------------------------------
  describe('stream copy', () => {
    it('selecting copy hides encoding-only settings', () => {
      renderVideoCodecSettings({ codec: 'copy' });

      // When copy is selected, encoding-only settings should be hidden
      expect(screen.queryByLabelText(/preset/i)).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/rate control/i)).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Pixel Format')).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/b-frames/i)).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Profile')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Level')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Tune')).not.toBeInTheDocument();

      // Keyframe controls should still be visible (work with copy mode)
      expect(screen.getByLabelText('Keyframe Interval')).toBeInTheDocument();
      expect(screen.getByLabelText(/min keyframe/i)).toBeInTheDocument();
    });

    it('shows copy explanation', () => {
      renderVideoCodecSettings({ codec: 'copy' });

      expect(
        screen.getByText(/stream copy|remux|no re-?encoding/i)
      ).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onChange with updated settings', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));
      await user.click(screen.getByRole('option', { name: /libx265/i }));

      expect(onChange).toHaveBeenCalledTimes(1);
      const callArg = onChange.mock.calls[0][0];
      expect(callArg).toHaveProperty('codec', 'libx265');
      expect(callArg).toHaveProperty('rateControl');
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icon per codec', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings();

      await user.click(screen.getByLabelText(/video codec/i));

      // Each codec option should have an info icon
      const infoIcons = screen.getAllByTestId('info-icon');
      expect(infoIcons.length).toBeGreaterThanOrEqual(1);
    });

    it('shows CRF explanation tooltip', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings({ rateControl: 'crf', crf: 23 });

      // Find info icon near CRF label (not field hint)
      const crfLabel = screen.getByText(/^CRF$/i);
      const crfInfo = crfLabel
        .closest('.form-field, .form-group, .setting-row')!
        .querySelector('[data-testid="info-icon"]')!;
      await user.hover(crfInfo);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent!.length).toBeGreaterThan(10);
      });
    });

    it('shows preset explanation tooltip', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings();

      const presetLabel = screen.getByText(/^Preset$/i);
      const presetInfo = presetLabel
        .closest('.form-field, .form-group, .setting-row')!
        .querySelector('[data-testid="info-icon"]')!;
      await user.hover(presetInfo);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        // Should describe speed vs quality tradeoff
        expect(tooltip.textContent).toMatch(/speed|quality|encoding/i);
      });
    });

    it('HW codec tooltips explain requirements', async () => {
      const user = userEvent.setup();
      renderVideoCodecSettings(
        { codec: 'h264_nvenc' },
        { hwCapabilities: ['h264_nvenc', 'hevc_nvenc'] }
      );

      const infoIcons = screen.getAllByTestId('info-icon');
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        // Should mention hardware requirements
        expect(tooltip.textContent).toMatch(/nvidia|gpu|hardware/i);
      });
    });
  });
});
