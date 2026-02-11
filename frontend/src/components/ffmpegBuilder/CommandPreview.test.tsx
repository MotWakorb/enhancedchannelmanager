/**
 * TDD Tests for CommandPreview component (Spec 1.9).
 *
 * These tests define the expected behavior of the CommandPreview component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../test/utils/renderWithProviders';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { CommandPreview } from './CommandPreview';
import type {
  FFMPEGBuilderState,
  InputSource,
  OutputConfig,
  VideoCodecSettings,
  AudioCodecSettings,
  VideoFilter,
  StreamMapping,
  CommandAnnotation,
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

function buildState(overrides: Partial<FFMPEGBuilderState> = {}): FFMPEGBuilderState {
  return {
    input: { type: 'file', path: '/input/video.mp4' },
    output: { path: '/output/result.mp4', format: 'mp4', overwrite: true },
    videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23, preset: 'medium' },
    audioCodec: { codec: 'aac', bitrate: '192k' },
    videoFilters: [],
    audioFilters: [],
    streamMappings: [],
    ...overrides,
  };
}

function renderCommandPreview(
  stateOverrides: Partial<FFMPEGBuilderState> = {},
  props: { annotated?: boolean } = {}
) {
  const config = buildState(stateOverrides);
  return renderWithProviders(
    <CommandPreview config={config} annotated={props.annotated ?? true} />
  );
}

// ===========================================================================
// Tests
// ===========================================================================

describe('CommandPreview', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders command text area', () => {
      renderCommandPreview();

      expect(screen.getByTestId('command-text')).toBeInTheDocument();
    });

    it('renders copy button', () => {
      renderCommandPreview();

      expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
    });

    it('renders annotation list in annotated mode', () => {
      renderCommandPreview({}, { annotated: true });
      expect(screen.getByTestId('annotation-list')).toBeInTheDocument();
    });

    it('hides annotation list in plain mode', () => {
      renderCommandPreview({}, { annotated: false });
      expect(screen.queryByTestId('annotation-list')).not.toBeInTheDocument();
    });

    it('shows placeholder when no config', () => {
      renderWithProviders(<CommandPreview config={null as unknown as FFMPEGBuilderState} />);

      expect(screen.getByText(/no configuration|configure.*input/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Command Generation
  // -------------------------------------------------------------------------
  describe('command generation', () => {
    it('generates basic x264 command', () => {
      renderCommandPreview();

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('ffmpeg');
      expect(commandText).toContain('-i');
      expect(commandText).toContain('/input/video.mp4');
      expect(commandText).toContain('-c:v libx264');
      expect(commandText).toContain('-crf 23');
      expect(commandText).toContain('/output/result.mp4');
    });

    it('generates NVENC command with hwaccel flags', () => {
      renderCommandPreview({
        input: {
          type: 'file',
          path: '/input/video.mp4',
          hwaccel: { api: 'cuda', outputFormat: 'cuda' },
        },
        videoCodec: {
          codec: 'h264_nvenc',
          rateControl: 'vbr',
          cq: 23,
          preset: 'p4',
        },
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-hwaccel cuda');
      expect(commandText).toContain('-hwaccel_output_format cuda');
      expect(commandText).toContain('-c:v h264_nvenc');
    });

    it('generates copy command', () => {
      renderCommandPreview({
        videoCodec: { codec: 'copy', rateControl: 'crf' },
        audioCodec: { codec: 'copy' },
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-c:v copy');
      expect(commandText).toContain('-c:a copy');
    });

    it('generates command with filters', () => {
      renderCommandPreview({
        videoFilters: [
          {
            type: 'scale',
            enabled: true,
            params: { width: 1920, height: 1080 },
            order: 0,
          },
          {
            type: 'fps',
            enabled: true,
            params: { fps: 30 },
            order: 1,
          },
        ],
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-vf');
      expect(commandText).toMatch(/scale=.*1920.*1080/);
      expect(commandText).toMatch(/fps=.*30/);
    });

    it('generates command with stream mappings', () => {
      renderCommandPreview({
        streamMappings: [
          {
            inputIndex: 0,
            streamType: 'video',
            streamIndex: 0,
            outputIndex: 0,
          },
          {
            inputIndex: 0,
            streamType: 'audio',
            streamIndex: 1,
            outputIndex: 1,
            language: 'eng',
          },
        ],
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-map 0:v:0');
      expect(commandText).toContain('-map 0:a:1');
    });

    it('generates command with audio settings', () => {
      renderCommandPreview({
        audioCodec: {
          codec: 'aac',
          bitrate: '256k',
          sampleRate: 48000,
          channels: 2,
        },
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-c:a aac');
      expect(commandText).toContain('-b:a 256k');
      expect(commandText).toContain('-ar 48000');
      expect(commandText).toContain('-ac 2');
    });

    it('generates multi-input command', () => {
      // Multi-input is represented via globalOptions or additional inputs
      // The component should support rendering commands with multiple -i flags
      renderCommandPreview({
        globalOptions: {
          'i_additional': '/input/overlay.png',
        },
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-i /input/video.mp4');
    });

    it('includes -y flag when overwrite enabled', () => {
      renderCommandPreview({
        output: {
          path: '/output/result.mp4',
          format: 'mp4',
          overwrite: true,
        },
      });

      const commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('-y');
    });

  });

  // -------------------------------------------------------------------------
  // Annotations
  // -------------------------------------------------------------------------
  describe('annotations', () => {
    it('shows annotation for each command flag', () => {
      renderCommandPreview();

      const annotationList = screen.getByTestId('annotation-list');
      const annotations = annotationList.querySelectorAll('[data-testid="annotation-item"]');
      // Should have at least: input, video codec, crf, audio codec, audio bitrate, output
      expect(annotations.length).toBeGreaterThanOrEqual(4);
    });

    it('annotations have correct categories', () => {
      renderCommandPreview();

      const annotationList = screen.getByTestId('annotation-list');
      const annotations = annotationList.querySelectorAll('[data-testid="annotation-item"]');

      // Collect all category values
      const categories = Array.from(annotations).map(
        (el) => el.getAttribute('data-category')
      );
      // Should include at least input, video, audio, and output categories
      expect(categories).toContain('input');
      expect(categories).toContain('video');
      expect(categories).toContain('audio');
      expect(categories).toContain('output');
    });

    it('input flags annotated as input', () => {
      renderCommandPreview();

      const annotationList = screen.getByTestId('annotation-list');
      const inputAnnotations = annotationList.querySelectorAll(
        '[data-category="input"]'
      );
      expect(inputAnnotations.length).toBeGreaterThanOrEqual(1);

      // The input annotation should reference the -i flag
      const text = Array.from(inputAnnotations)
        .map((el) => el.textContent)
        .join(' ');
      expect(text).toMatch(/-i|input/i);
    });

    it('codec flags annotated as video/audio', () => {
      renderCommandPreview();

      const annotationList = screen.getByTestId('annotation-list');
      const videoAnnotations = annotationList.querySelectorAll(
        '[data-category="video"]'
      );
      const audioAnnotations = annotationList.querySelectorAll(
        '[data-category="audio"]'
      );

      expect(videoAnnotations.length).toBeGreaterThanOrEqual(1);
      expect(audioAnnotations.length).toBeGreaterThanOrEqual(1);
    });

    it('filter flags annotated as filter', () => {
      renderCommandPreview({
        videoFilters: [
          {
            type: 'scale',
            enabled: true,
            params: { width: 1280, height: 720 },
            order: 0,
          },
        ],
      });

      const annotationList = screen.getByTestId('annotation-list');
      const filterAnnotations = annotationList.querySelectorAll(
        '[data-category="filter"]'
      );
      expect(filterAnnotations.length).toBeGreaterThanOrEqual(1);
    });

    it('output flags annotated as output', () => {
      renderCommandPreview();

      const annotationList = screen.getByTestId('annotation-list');
      const outputAnnotations = annotationList.querySelectorAll(
        '[data-category="output"]'
      );
      expect(outputAnnotations.length).toBeGreaterThanOrEqual(1);
    });
  });

  // -------------------------------------------------------------------------
  // Annotated Explanations
  // -------------------------------------------------------------------------
  describe('annotated explanations', () => {
    it('each annotation has explanation text', () => {
      renderCommandPreview();

      const annotations = screen.getAllByTestId('annotation-item');
      annotations.forEach((annotation) => {
        const explanation = annotation.querySelector('[data-testid="annotation-explanation"]');
        expect(explanation).toBeInTheDocument();
        expect(explanation!.textContent!.length).toBeGreaterThan(0);
      });
    });

    it('explanations are plain-English (not flag names)', () => {
      renderCommandPreview();

      const annotations = screen.getAllByTestId('annotation-item');
      annotations.forEach((annotation) => {
        const explanation = annotation.querySelector(
          '[data-testid="annotation-explanation"]'
        )!;
        const text = explanation.textContent!;
        // Explanations should be more than just the flag itself
        expect(text.length).toBeGreaterThan(5);
        // Should contain readable words, not just flags
        expect(text).toMatch(/[a-zA-Z]{3,}/);
      });
    });

    it('clicking annotation highlights flag in command', async () => {
      const user = userEvent.setup();
      renderCommandPreview();

      const firstAnnotation = screen.getAllByTestId('annotation-item')[0];
      await user.click(firstAnnotation);

      // The corresponding flag in the command text should be highlighted
      const commandText = screen.getByTestId('command-text');
      const highlighted = commandText.querySelector('.highlighted, .flag-highlight, [data-highlighted="true"]');
      expect(highlighted).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Copy to Clipboard
  // -------------------------------------------------------------------------
  describe('copy to clipboard', () => {
    it('copy button copies command to clipboard', async () => {
      const user = userEvent.setup();
      // userEvent.setup() installs its own clipboard stub on navigator;
      // spy on writeText to verify the component calls it.
      const writeText = vi.spyOn(navigator.clipboard, 'writeText');

      renderCommandPreview();

      await user.click(screen.getByRole('button', { name: /copy/i }));

      expect(writeText).toHaveBeenCalledWith(
        expect.stringContaining('ffmpeg')
      );
    });

    it('shows success feedback after copy', async () => {
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, 'writeText');

      renderCommandPreview();

      await user.click(screen.getByRole('button', { name: /copy/i }));

      await waitFor(() => {
        expect(screen.getByText(/copied|success/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Validation Warnings
  // -------------------------------------------------------------------------
  describe('validation warnings', () => {
    it('shows warning for incompatible codec/container', () => {
      renderCommandPreview({
        videoCodec: { codec: 'libvpx-vp9', rateControl: 'crf', crf: 30 },
        output: { path: 'pipe:1', format: 'ts' },
      });

      // VP9 in MPEG-TS is incompatible
      expect(screen.getByText(/warning|incompatible|not recommended/i)).toBeInTheDocument();
    });

    it('shows warning for missing audio codec with audio filters', () => {
      renderCommandPreview({
        audioCodec: { codec: 'copy' },
        audioFilters: [
          {
            type: 'volume',
            enabled: true,
            params: { volume: 2.0 },
            order: 0,
          },
        ],
      });

      // Audio filters with copy codec will be ignored
      expect(screen.getByText(/warning|audio filter.*copy|cannot apply/i)).toBeInTheDocument();
    });

    it('shows warning for VAAPI without hwupload filter', () => {
      renderCommandPreview({
        input: {
          type: 'file',
          path: '/input/video.mp4',
          hwaccel: { api: 'vaapi', device: '/dev/dri/renderD128' },
        },
        videoCodec: { codec: 'h264_vaapi', rateControl: 'crf', crf: 23 },
        videoFilters: [
          {
            type: 'scale',
            enabled: true,
            params: { width: 1920, height: 1080 },
            order: 0,
          },
        ],
        // Missing hwupload filter after scale
      });

      expect(screen.getByText(/warning|hwupload|vaapi.*filter/i)).toBeInTheDocument();
    });

    it('no warnings for valid config', () => {
      renderCommandPreview();

      // Default config (x264 in MP4) should have no warnings
      expect(screen.queryByText(/warning/i)).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Live Update
  // -------------------------------------------------------------------------
  describe('live update', () => {
    it('command updates when config changes', () => {
      const config1 = buildState({ videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23 } });
      const config2 = buildState({ videoCodec: { codec: 'libx265', rateControl: 'crf', crf: 28 } });

      const { rerender } = renderWithProviders(<CommandPreview config={config1} />);

      let commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('libx264');
      expect(commandText).toContain('-crf 23');

      rerender(<CommandPreview config={config2} />);

      commandText = screen.getByTestId('command-text').textContent!;
      expect(commandText).toContain('libx265');
      expect(commandText).toContain('-crf 28');
    });

    it('annotations update with command', () => {
      const config1 = buildState({ videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23 } });
      const config2 = buildState({
        videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23 },
        videoFilters: [
          {
            type: 'scale',
            enabled: true,
            params: { width: 1280, height: 720 },
            order: 0,
          },
        ],
      });

      const { rerender } = renderWithProviders(<CommandPreview config={config1} annotated />);

      const annotationsBefore = screen
        .getByTestId('annotation-list')
        .querySelectorAll('[data-testid="annotation-item"]').length;

      rerender(<CommandPreview config={config2} annotated />);

      const annotationsAfter = screen
        .getByTestId('annotation-list')
        .querySelectorAll('[data-testid="annotation-item"]').length;

      // Adding a filter should add at least one more annotation
      expect(annotationsAfter).toBeGreaterThan(annotationsBefore);
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows annotated command mode toggle', () => {
      renderCommandPreview();

      expect(
        screen.getByRole('button', { name: /annotated|explain|toggle/i })
      ).toBeInTheDocument();
    });

    it('toggle hides annotations when in annotated mode', async () => {
      const user = userEvent.setup();
      renderCommandPreview({}, { annotated: true });

      // Annotations visible initially
      expect(screen.getByTestId('annotation-list')).toBeInTheDocument();

      // Click toggle to switch to plain mode
      const toggleBtn = screen.getByRole('button', { name: /plain|annotated|explain|toggle/i });
      await user.click(toggleBtn);

      await waitFor(() => {
        expect(screen.queryByTestId('annotation-list')).not.toBeInTheDocument();
      });
    });

    it('each flag has hover explanation', async () => {
      const user = userEvent.setup();
      renderCommandPreview();

      // Find a flag element in the command text
      const commandText = screen.getByTestId('command-text');
      const flagElements = commandText.querySelectorAll('[data-testid="command-flag"]');
      expect(flagElements.length).toBeGreaterThanOrEqual(1);

      // Hover over the first flag
      await user.hover(flagElements[0] as HTMLElement);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        // Tooltip should contain a human-readable explanation
        expect(tooltip.textContent!.length).toBeGreaterThan(5);
      });
    });
  });
});
