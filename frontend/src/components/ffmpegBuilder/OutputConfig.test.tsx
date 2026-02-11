/**
 * TDD Tests for OutputConfig component (Spec 1.2).
 *
 * These tests define the expected behavior of the OutputConfig component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { OutputConfig } from './OutputConfig';
import type {
  OutputConfig as OutputConfigType,
  ContainerFormat,
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

const defaultOutput: OutputConfigType = {
  path: '',
  format: 'ts',
  overwrite: true,
};

function renderOutputConfig(
  overrides: Partial<OutputConfigType> = {},
  props: { onChange?: ReturnType<typeof vi.fn> } = {}
) {
  const onChange = props.onChange ?? vi.fn();
  const outputConfig: OutputConfigType = { ...defaultOutput, ...overrides };
  return {
    onChange,
    ...render(<OutputConfig value={outputConfig} onChange={onChange} />),
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('OutputConfig', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders output path input', () => {
      renderOutputConfig({ path: 'pipe:1' });

      expect(screen.getByLabelText(/output path/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/output path/i)).toHaveValue('pipe:1');
    });

    it('renders format selector', () => {
      renderOutputConfig();

      expect(screen.getByLabelText(/format/i)).toBeInTheDocument();
    });

    it('shows default MPEG-TS format', () => {
      renderOutputConfig();

      expect(screen.getByLabelText(/format/i)).toHaveTextContent(/mpeg-ts/i);
    });
  });

  // -------------------------------------------------------------------------
  // Container Formats
  // -------------------------------------------------------------------------
  describe('container formats', () => {
    it('can select MPEG-TS', async () => {
      const user = userEvent.setup();
      const { onChange } = renderOutputConfig();

      await user.click(screen.getByLabelText(/format/i));
      await user.click(screen.getByRole('option', { name: /mpeg-?ts/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'ts' })
      );
    });

    it('can select HLS', async () => {
      const user = userEvent.setup();
      const { onChange } = renderOutputConfig();

      await user.click(screen.getByLabelText(/format/i));
      await user.click(screen.getByRole('option', { name: /hls/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'hls' })
      );
    });

    it('can select DASH', async () => {
      const user = userEvent.setup();
      const { onChange } = renderOutputConfig();

      await user.click(screen.getByLabelText(/format/i));
      await user.click(screen.getByRole('option', { name: /dash/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'dash' })
      );
    });

    it('shows format description', () => {
      renderOutputConfig({ format: 'ts' });

      const desc = screen.getByText(/Transport stream for broadcasting/i);
      expect(desc).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // MP4 Options
  // -------------------------------------------------------------------------
  // -------------------------------------------------------------------------
  // HLS Options
  // -------------------------------------------------------------------------
  describe('HLS options', () => {
    it('shows segment duration when HLS', () => {
      renderOutputConfig({ format: 'hls' });

      expect(screen.getByLabelText(/segment duration/i)).toBeInTheDocument();
    });

    it('shows playlist type when HLS', () => {
      renderOutputConfig({ format: 'hls' });

      expect(screen.getByLabelText(/playlist type/i)).toBeInTheDocument();
    });

    it('shows segment filename pattern', () => {
      renderOutputConfig({ format: 'hls' });

      expect(screen.getByLabelText(/segment.*filename|segment.*pattern/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Validation
  // -------------------------------------------------------------------------
  describe('validation', () => {
    it('requires output path', () => {
      renderOutputConfig({ path: '' });

      expect(screen.getByText(/output path is required/i)).toBeInTheDocument();
    });

    it('warns on extension/format mismatch', () => {
      renderOutputConfig({ path: '/output/stream.m3u8', format: 'ts' });

      expect(screen.getByText(/extension.*mismatch|does not match/i)).toBeInTheDocument();
    });

    it('validates HLS output creates directory', () => {
      renderOutputConfig({ format: 'hls', path: '/output/stream.m3u8' });

      // HLS outputs a directory of segments, so the component should note this
      expect(screen.getAllByText(/directory|segment/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onChange for path changes', async () => {
      const user = userEvent.setup();
      const { onChange } = renderOutputConfig({ path: '' });

      const pathInput = screen.getByLabelText(/output path/i);
      await user.type(pathInput, '/output/result.mp4');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.objectContaining({ path: '/output/result.mp4' })
        );
      });
    });

    it('calls onChange for format changes', async () => {
      const user = userEvent.setup();
      const { onChange } = renderOutputConfig();

      await user.click(screen.getByLabelText(/format/i));
      await user.click(screen.getByRole('option', { name: /hls/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'hls' })
      );
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icon for format selector', () => {
      renderOutputConfig();

      const formatLabel = screen.getByText(/^Format$/i);
      const formatField = formatLabel.closest('.output-config-field, .form-field, .form-group')!;
      const infoIcon = formatField.querySelector('[data-testid="info-icon"]');
      expect(infoIcon).toBeInTheDocument();
    });

    it('tooltip explains each format', async () => {
      const user = userEvent.setup();
      renderOutputConfig();

      const formatLabel = screen.getByText(/^Format$/i);
      const formatField = formatLabel.closest('.output-config-field, .form-field, .form-group')!;
      const infoIcon = formatField.querySelector('[data-testid="info-icon"]')!;
      await user.hover(infoIcon);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        // Should contain a human-readable description
        expect(tooltip.textContent!.length).toBeGreaterThan(10);
      });
    });
  });
});
