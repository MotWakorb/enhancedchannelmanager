/**
 * TDD Tests for PresetTemplates component (Spec 1.8).
 *
 * These tests define the expected behavior of the PresetTemplates component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/utils/renderWithProviders';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { PresetTemplates } from './PresetTemplates';
import type {
  PresetTemplate,
  PresetCategory,
  FFMPEGBuilderState,
} from '../../types/ffmpegBuilder';

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

const defaultConfig: FFMPEGBuilderState = {
  input: { type: 'file', path: '/media/input.mp4' },
  output: { path: '/media/output.mp4', format: 'mp4' },
  videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23, preset: 'medium' },
  audioCodec: { codec: 'aac', bitrate: '192k' },
  videoFilters: [],
  audioFilters: [],
  streamMappings: [],
};

function renderPresetTemplates(
  props: {
    onPresetLoad?: ReturnType<typeof vi.fn>;
    onPresetSave?: ReturnType<typeof vi.fn>;
    currentConfig?: FFMPEGBuilderState;
  } = {}
) {
  const onPresetLoad = props.onPresetLoad ?? vi.fn();
  const onPresetSave = props.onPresetSave ?? vi.fn();
  const currentConfig = props.currentConfig ?? defaultConfig;

  return {
    onPresetLoad,
    onPresetSave,
    ...renderWithProviders(
      <PresetTemplates
        onPresetLoad={onPresetLoad}
        onPresetSave={onPresetSave}
        currentConfig={currentConfig}
      />
    ),
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('PresetTemplates', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders preset selector', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByLabelText(/preset/i)).toBeInTheDocument();
      });
    });

    it('renders save preset button', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /save preset/i })).toBeInTheDocument();
      });
    });

    it('renders preset categories', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        // At least one category label should be visible
        const categories = ['web', 'streaming', 'archive'];
        const found = categories.some(cat =>
          screen.queryByText(new RegExp(cat, 'i')) !== null
        );
        expect(found).toBe(true);
      });
    });

    it('shows built-in presets', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        // Preset list should be populated from the API
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Built-in Presets
  // -------------------------------------------------------------------------
  describe('built-in presets', () => {
    it('shows Web MP4 preset', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });
    });

    it('shows HLS Streaming preset', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/hls streaming/i)).toBeInTheDocument();
      });
    });

    it('shows Archive HEVC preset', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/archive.*hevc/i)).toBeInTheDocument();
      });
    });

    it('shows NVENC Fast preset', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/nvenc fast/i)).toBeInTheDocument();
      });
    });

    it('each preset has name and description', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        // Web MP4 preset should show both name and description
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
        expect(
          screen.getByText(/browser playback|fast start/i)
        ).toBeInTheDocument();
      });
    });

    it('preset descriptions explain use case', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        // HLS description should mention streaming
        expect(
          screen.getByText(/adaptive|streaming/i)
        ).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Loading Presets
  // -------------------------------------------------------------------------
  describe('loading presets', () => {
    it('clicking preset loads config', async () => {
      const user = userEvent.setup();
      const { onPresetLoad } = renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/web mp4/i));

      await waitFor(() => {
        expect(onPresetLoad).toHaveBeenCalled();
      });
    });

    it('preserves input/output paths', async () => {
      const user = userEvent.setup();
      const { onPresetLoad } = renderPresetTemplates({
        currentConfig: {
          ...defaultConfig,
          input: { type: 'file', path: '/my/custom/input.mp4' },
          output: { path: '/my/custom/output.mp4', format: 'mp4' },
        },
      });

      await waitFor(() => {
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/web mp4/i));

      await waitFor(() => {
        expect(onPresetLoad).toHaveBeenCalled();
        const loadedConfig = onPresetLoad.mock.calls[0][0];
        // Input and output paths should be preserved from current config
        expect(loadedConfig.input.path).toBe('/my/custom/input.mp4');
        expect(loadedConfig.output.path).toBe('/my/custom/output.mp4');
      });
    });

    it('updates video codec settings', async () => {
      const user = userEvent.setup();
      const { onPresetLoad } = renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/archive.*hevc/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/archive.*hevc/i));

      await waitFor(() => {
        expect(onPresetLoad).toHaveBeenCalled();
        const loadedConfig = onPresetLoad.mock.calls[0][0];
        expect(loadedConfig.videoCodec.codec).toBe('libx265');
      });
    });

    it('updates audio codec settings', async () => {
      const user = userEvent.setup();
      const { onPresetLoad } = renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/archive.*hevc/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/archive.*hevc/i));

      await waitFor(() => {
        expect(onPresetLoad).toHaveBeenCalled();
        const loadedConfig = onPresetLoad.mock.calls[0][0];
        expect(loadedConfig.audioCodec.codec).toBe('flac');
      });
    });

    it('shows confirmation toast', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/web mp4/i));

      await waitFor(() => {
        // Should show a toast notification confirming the preset was loaded
        expect(
          screen.getByRole('alert')
        ).toHaveTextContent(/preset.*applied/i);
      });
    });
  });

  // -------------------------------------------------------------------------
  // Saving Presets
  // -------------------------------------------------------------------------
  describe('saving presets', () => {
    it('save button opens dialog', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      const saveBtn = screen.getByRole('button', { name: /save preset/i });
      await user.click(saveBtn);

      await waitFor(() => {
        // A dialog/modal should appear for saving the preset
        expect(
          screen.getByRole('dialog', { name: /save preset/i })
        ).toBeInTheDocument();
      });
    });

    it('can enter preset name', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await user.click(screen.getByRole('button', { name: /save preset/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      });

      const nameInput = screen.getByLabelText(/name/i);
      await user.type(nameInput, 'My Custom Preset');
      expect(nameInput).toHaveValue('My Custom Preset');
    });

    it('can enter description', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await user.click(screen.getByRole('button', { name: /save preset/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
      });

      const descInput = screen.getByLabelText(/description/i);
      await user.type(descInput, 'A custom preset for testing');
      expect(descInput).toHaveValue('A custom preset for testing');
    });

    it('can select category', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await user.click(screen.getByRole('button', { name: /save preset/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/category/i)).toBeInTheDocument();
      });

      await user.click(screen.getByLabelText(/category/i));
      await user.click(screen.getByRole('option', { name: /custom/i }));

      // Category should be selected — check the combobox value
      expect(screen.getByLabelText(/category/i)).toHaveAttribute('aria-valuetext', 'Custom');
    });

    it('saves current config as preset', async () => {
      const user = userEvent.setup();
      const { onPresetSave } = renderPresetTemplates();

      await user.click(screen.getByRole('button', { name: /save preset/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/name/i), 'My Custom Preset');
      await user.type(
        screen.getByLabelText(/description/i),
        'Custom preset description'
      );

      // Submit the save form
      const confirmSaveBtn = screen.getByRole('button', { name: /^save$/i });
      await user.click(confirmSaveBtn);

      await waitFor(() => {
        expect(onPresetSave).toHaveBeenCalledWith(
          expect.objectContaining({
            name: 'My Custom Preset',
            description: 'Custom preset description',
            config: expect.objectContaining({
              videoCodec: expect.objectContaining({ codec: 'libx264' }),
            }),
          })
        );
      });
    });

    it('saved preset appears in list', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await user.click(screen.getByRole('button', { name: /save preset/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/name/i), 'Brand New Preset');
      await user.type(
        screen.getByLabelText(/description/i),
        'A new custom preset'
      );

      const confirmSaveBtn = screen.getByRole('button', { name: /^save$/i });
      await user.click(confirmSaveBtn);

      await waitFor(() => {
        // The newly saved preset should appear in the preset list
        expect(screen.getByText(/brand new preset/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Preset Categories
  // -------------------------------------------------------------------------
  describe('preset categories', () => {
    it('shows web category', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/web/i)).toBeInTheDocument();
      });
    });

    it('shows streaming category', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/streaming/i)).toBeInTheDocument();
      });
    });

    it('shows archive category', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/archive/i)).toBeInTheDocument();
      });
    });

    it('shows custom category', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/custom/i)).toBeInTheDocument();
      });
    });

    it('can filter by category', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: /web/i })).toBeInTheDocument();
      });

      // Click the web category filter tab
      await user.click(screen.getByRole('tab', { name: /web/i }));

      await waitFor(() => {
        // Web MP4 should be visible
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
        // Archive HEVC should be filtered out (not in web category)
        expect(screen.queryByText(/archive.*hevc/i)).not.toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onPresetLoad with config', async () => {
      const user = userEvent.setup();
      const { onPresetLoad } = renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/web mp4/i));

      await waitFor(() => {
        expect(onPresetLoad).toHaveBeenCalledTimes(1);
        const config = onPresetLoad.mock.calls[0][0];
        // Config should be a complete FFMPEGBuilderState
        expect(config).toHaveProperty('input');
        expect(config).toHaveProperty('output');
        expect(config).toHaveProperty('videoCodec');
        expect(config).toHaveProperty('audioCodec');
      });
    });

    it('calls onPresetSave with new preset', async () => {
      const user = userEvent.setup();
      const { onPresetSave } = renderPresetTemplates();

      await user.click(screen.getByRole('button', { name: /save preset/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/name/i), 'Callback Test Preset');
      await user.type(
        screen.getByLabelText(/description/i),
        'Testing callbacks'
      );

      const confirmSaveBtn = screen.getByRole('button', { name: /^save$/i });
      await user.click(confirmSaveBtn);

      await waitFor(() => {
        expect(onPresetSave).toHaveBeenCalledTimes(1);
        const savedPreset = onPresetSave.mock.calls[0][0];
        expect(savedPreset).toHaveProperty('name', 'Callback Test Preset');
        expect(savedPreset).toHaveProperty('description', 'Testing callbacks');
        expect(savedPreset).toHaveProperty('config');
        expect(savedPreset.config).toHaveProperty('videoCodec');
      });
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('presets have description tooltips', async () => {
      const user = userEvent.setup();
      renderPresetTemplates();

      await waitFor(() => {
        expect(screen.getByText(/web mp4/i)).toBeInTheDocument();
      });

      // Hover over a preset to see its tooltip/description
      await user.hover(screen.getByText(/web mp4/i));

      await waitFor(() => {
        // The preset description should be visible (either inline or as tooltip)
        expect(
          screen.getByText(/browser playback|fast start|optimized/i)
        ).toBeInTheDocument();
      });
    });

    it('preset description explains use case', async () => {
      renderPresetTemplates();

      await waitFor(() => {
        // Each built-in preset has a description explaining when to use it
        // HLS Streaming description should mention streaming/adaptive use case
        const descriptions = screen.getAllByText(/adaptive|streaming|http live/i);
        expect(descriptions.length).toBeGreaterThanOrEqual(1);
        // Archive HEVC should mention archival use case
        expect(
          screen.getByText(/archival|smaller files|high.quality/i)
        ).toBeInTheDocument();
      });
    });
  });
});
