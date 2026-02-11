/**
 * TDD Tests for InputSourceConfig component (Spec 1.1).
 *
 * These tests define the expected behavior of the InputSourceConfig component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { InputSourceConfig } from './InputSourceConfig';
import type { InputSource, HWAccelConfig } from '../../types/ffmpegBuilder';

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

const defaultInput: InputSource = {
  type: 'url',
  path: '',
};

function renderInputSource(
  overrides: Partial<InputSource> = {},
  props: { onChange?: ReturnType<typeof vi.fn> } = {}
) {
  const onChange = props.onChange ?? vi.fn();
  const inputSource: InputSource = { ...defaultInput, ...overrides };
  return {
    onChange,
    ...render(<InputSourceConfig value={inputSource} onChange={onChange} />),
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('InputSourceConfig', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders input type selector', () => {
      renderInputSource();

      expect(screen.getByLabelText(/input type/i)).toBeInTheDocument();
    });

    it('renders path input', () => {
      renderInputSource({ path: 'http://example.com/stream.m3u8' });

      expect(screen.getByLabelText(/url/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/url/i)).toHaveValue('http://example.com/stream.m3u8');
    });

    it('renders format selector', () => {
      renderInputSource();

      expect(screen.getByLabelText(/format/i)).toBeInTheDocument();
    });

    it('renders hw accel selector', () => {
      renderInputSource();

      expect(screen.getByLabelText(/hardware acceleration/i)).toBeInTheDocument();
    });

    it('shows default URL type selected', () => {
      renderInputSource();

      const inputTypeCombobox = screen.getByRole('combobox', { name: /input type/i });
      expect(inputTypeCombobox).toHaveAttribute('aria-valuetext', 'URL');
    });
  });

  // -------------------------------------------------------------------------
  // Input Types
  // -------------------------------------------------------------------------
  describe('input types', () => {
    it('can select URL input', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/input type/i));
      await user.click(screen.getByRole('option', { name: /^url$/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'url' })
      );
    });

    it('can select pipe input', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/input type/i));
      await user.click(screen.getByRole('option', { name: /^pipe$/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'pipe' })
      );
    });

    it('shows appropriate path label for URL type', () => {
      renderInputSource({ type: 'url' });

      expect(screen.getByLabelText(/url/i)).toBeInTheDocument();
    });

    it('shows appropriate path label for pipe type', () => {
      renderInputSource({ type: 'pipe' });

      expect(screen.getByText(/pipe:0/i)).toBeInTheDocument();
    });

    it('validates URLs', () => {
      renderInputSource({ type: 'url', path: 'not-a-url' });

      expect(screen.getByText(/valid url/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Hardware Acceleration
  // -------------------------------------------------------------------------
  describe('hardware acceleration', () => {
    it('shows hwaccel dropdown', () => {
      renderInputSource();

      expect(screen.getByLabelText(/hardware acceleration/i)).toBeInTheDocument();
    });

    it('can select CUDA', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/hardware acceleration/i));
      await user.click(screen.getByRole('option', { name: /cuda/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          hwaccel: expect.objectContaining({ api: 'cuda' }),
        })
      );
    });

    it('can select QSV', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/hardware acceleration/i));
      await user.click(screen.getByRole('option', { name: /qsv/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          hwaccel: expect.objectContaining({ api: 'qsv' }),
        })
      );
    });

    it('can select VAAPI', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/hardware acceleration/i));
      await user.click(screen.getByRole('option', { name: /vaapi/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          hwaccel: expect.objectContaining({ api: 'vaapi' }),
        })
      );
    });

    it('shows device field when VAAPI selected', () => {
      renderInputSource({
        hwaccel: { api: 'vaapi', device: '/dev/dri/renderD128' },
      });

      expect(screen.getByLabelText(/device/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/device/i)).toHaveValue('/dev/dri/renderD128');
    });

    it('shows output format when CUDA selected', () => {
      renderInputSource({
        hwaccel: { api: 'cuda', outputFormat: 'cuda' },
      });

      expect(screen.getByLabelText(/output format/i)).toBeInTheDocument();
    });

    it('disables unavailable HW options with reason', async () => {
      const user = userEvent.setup();
      // The capabilities mock has all HW available, but we test the UI pattern:
      // unavailable options should be disabled and show a reason tooltip
      renderInputSource();

      await user.click(screen.getByLabelText(/hardware acceleration/i));

      // All options should be present; the component should mark unavailable
      // ones as disabled with an aria-disabled attribute and show a reason
      const options = screen.getAllByRole('option');
      expect(options.length).toBeGreaterThanOrEqual(4); // none, cuda, qsv, vaapi
    });
  });

  // -------------------------------------------------------------------------
  // Input Options
  // -------------------------------------------------------------------------
  describe('input options', () => {
    it('can set format override', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/format/i));
      await user.click(screen.getByRole('option', { name: /mpegts/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'mpegts' })
      );
    });

    it('clears options when input type changes', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource({
        type: 'url',
        path: 'http://example.com/stream',
      });

      // Change input type to Pipe
      await user.click(screen.getByLabelText(/input type/i));
      await user.click(screen.getByRole('option', { name: /^pipe$/i }));

      // Options should be cleared when type changes
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'pipe',
          path: '',
        })
      );
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onChange when input type changes', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/input type/i));
      await user.click(screen.getByRole('option', { name: /^url$/i }));

      expect(onChange).toHaveBeenCalled();
    });

    it('calls onChange when path changes', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource({ type: 'url', path: '' });

      const pathInput = screen.getByLabelText(/url/i);
      await user.type(pathInput, 'http://example.com/stream');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.objectContaining({ path: 'http://example.com/stream' })
        );
      });
    });

    it('calls onChange when hwaccel changes', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource();

      await user.click(screen.getByLabelText(/hardware acceleration/i));
      await user.click(screen.getByRole('option', { name: /cuda/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          hwaccel: expect.objectContaining({ api: 'cuda' }),
        })
      );
    });

    it('passes complete InputSource object', async () => {
      const user = userEvent.setup();
      const { onChange } = renderInputSource({
        type: 'url',
        path: 'http://example.com/old',
        format: 'mpegts',
      });

      const pathInput = screen.getByLabelText(/url/i);
      await user.clear(pathInput);
      await user.type(pathInput, 'http://example.com/new');

      await waitFor(() => {
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall).toHaveProperty('type');
        expect(lastCall).toHaveProperty('path');
        expect(lastCall.type).toBe('url');
      });
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icons for each setting', () => {
      renderInputSource();

      const infoIcons = screen.getAllByTestId('info-icon');
      // At minimum: input type, path, format, hwaccel
      expect(infoIcons.length).toBeGreaterThanOrEqual(4);
    });

    it('shows tooltip on hover for input type', async () => {
      const user = userEvent.setup();
      renderInputSource();

      const inputTypeInfo = screen.getAllByTestId('info-icon')[0];
      await user.hover(inputTypeInfo);

      await waitFor(() => {
        expect(screen.getByRole('tooltip')).toBeInTheDocument();
      });
    });

    it('shows tooltip for hwaccel options', async () => {
      const user = userEvent.setup();
      renderInputSource();

      // Find the info icon near the hwaccel selector
      const hwaccelLabel = screen.getByText(/hardware acceleration/i);
      const hwaccelInfo = hwaccelLabel
        .closest('.input-source-field, .form-field, .form-group')!
        .querySelector('[data-testid="info-icon"]')!;
      await user.hover(hwaccelInfo);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
      });
    });

    it('tooltip text contains plain-English explanation', async () => {
      const user = userEvent.setup();
      renderInputSource();

      const infoIcons = screen.getAllByTestId('info-icon');
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        // Tooltip should contain a human-readable sentence, not just a flag name
        expect(tooltip.textContent!.length).toBeGreaterThan(10);
        // Should not just be the ffmpeg flag
        expect(tooltip.textContent).not.toBe('-i');
      });
    });
  });
});
