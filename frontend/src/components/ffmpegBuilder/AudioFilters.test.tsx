/**
 * TDD Tests for AudioFilters component (Spec 1.6).
 *
 * These tests define the expected behavior of the AudioFilters component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { AudioFilters } from './AudioFilters';
import type { AudioFilter } from '../../types/ffmpegBuilder';

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

function renderAudioFilters(
  filters: AudioFilter[] = [],
  props: { onChange?: ReturnType<typeof vi.fn> } = {}
) {
  const onChange = props.onChange ?? vi.fn();
  return {
    onChange,
    ...render(<AudioFilters value={filters} onChange={onChange} />),
  };
}

function createFilter(
  type: AudioFilter['type'],
  params: Record<string, string | number | boolean> = {},
  overrides: Partial<AudioFilter> = {}
): AudioFilter {
  return {
    type,
    enabled: true,
    params,
    order: 0,
    ...overrides,
  };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('AudioFilters', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders filter list', () => {
      renderAudioFilters([]);

      expect(screen.getByTestId('audio-filter-list')).toBeInTheDocument();
    });

    it('renders add filter button', () => {
      renderAudioFilters([]);

      expect(
        screen.getByRole('button', { name: /add filter/i })
      ).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Adding Filters
  // -------------------------------------------------------------------------
  describe('adding filters', () => {
    it('can add volume filter', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /volume/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'volume' }),
        ])
      );
    });

    it('can add loudnorm filter', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /loudnorm/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'loudnorm' }),
        ])
      );
    });

    it('can add aresample filter', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /aresample/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'aresample' }),
        ])
      );
    });

  });

  // -------------------------------------------------------------------------
  // Volume Filter
  // -------------------------------------------------------------------------
  describe('volume filter', () => {
    it('shows volume slider', () => {
      renderAudioFilters([createFilter('volume', { volume: 1.0 })]);

      const slider = screen.getByLabelText(/volume/i);
      expect(slider).toBeInTheDocument();
      expect(slider).toHaveAttribute('type', 'range');
    });

    it('range 0 to 3', () => {
      renderAudioFilters([createFilter('volume', { volume: 1.0 })]);

      const slider = screen.getByLabelText(/volume/i);
      expect(slider).toHaveAttribute('min', '0');
      expect(slider).toHaveAttribute('max', '3');
    });

    it('shows dB value label', () => {
      renderAudioFilters([createFilter('volume', { volume: 1.5 })]);

      // Should show the dB equivalent of the volume level
      expect(screen.getByText(/dB/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Loudnorm Filter
  // -------------------------------------------------------------------------
  describe('loudnorm filter', () => {
    it('shows target loudness (I)', () => {
      renderAudioFilters([
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }),
      ]);

      expect(screen.getByLabelText(/target loudness|integrated/i)).toBeInTheDocument();
    });

    it('shows LRA', () => {
      renderAudioFilters([
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }),
      ]);

      expect(screen.getByLabelText(/lra|loudness range/i)).toBeInTheDocument();
    });

    it('shows true peak (TP)', () => {
      renderAudioFilters([
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }),
      ]);

      expect(screen.getByLabelText(/true peak|tp/i)).toBeInTheDocument();
    });

    it('uses EBU R128 defaults', () => {
      renderAudioFilters([
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }),
      ]);

      // EBU R128 default: I=-24, LRA=7, TP=-2
      const iInput = screen.getByLabelText(/target loudness|integrated/i);
      expect(iInput).toHaveValue(-24);

      const lraInput = screen.getByLabelText(/lra|loudness range/i);
      expect(lraInput).toHaveValue(7);

      const tpInput = screen.getByLabelText(/true peak|tp/i);
      expect(tpInput).toHaveValue(-2);
    });
  });

  // -------------------------------------------------------------------------
  // Filter Chain
  // -------------------------------------------------------------------------
  describe('filter chain', () => {
    it('shows filters in order', () => {
      const filters = [
        createFilter('volume', { volume: 1.5 }, { order: 0 }),
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }, { order: 1 }),
      ];
      renderAudioFilters(filters);

      const filterItems = screen.getAllByTestId('filter-item');
      expect(filterItems).toHaveLength(2);

      expect(within(filterItems[0]).getByText(/volume/i)).toBeInTheDocument();
      expect(within(filterItems[1]).getByText(/loudnorm/i)).toBeInTheDocument();
    });

    it('can reorder', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('volume', { volume: 1.5 }, { order: 0 }),
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }, { order: 1 }),
      ];
      const { onChange } = renderAudioFilters(filters);

      const filterItems = screen.getAllByTestId('filter-item');
      const moveDownBtn = within(filterItems[0]).getByRole('button', {
        name: /move down|reorder/i,
      });
      await user.click(moveDownBtn);

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'loudnorm', order: 0 }),
          expect.objectContaining({ type: 'volume', order: 1 }),
        ])
      );
    });

    it('can enable/disable', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('volume', { volume: 1.5 }, { order: 0, enabled: true }),
      ];
      const { onChange } = renderAudioFilters(filters);

      const toggleBtn = screen.getByRole('checkbox', { name: /enable|toggle/i });
      await user.click(toggleBtn);

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'volume', enabled: false }),
        ])
      );
    });

    it('can remove', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('volume', { volume: 1.5 }, { order: 0 }),
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }, { order: 1 }),
      ];
      const { onChange } = renderAudioFilters(filters);

      const filterItems = screen.getAllByTestId('filter-item');
      const removeBtn = within(filterItems[0]).getByRole('button', {
        name: /remove|delete/i,
      });
      await user.click(removeBtn);

      const callArg = onChange.mock.calls[onChange.mock.calls.length - 1][0];
      expect(callArg).toHaveLength(1);
      expect(callArg[0]).toHaveProperty('type', 'loudnorm');
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onChange with filter array', async () => {
      const user = userEvent.setup();
      const { onChange } = renderAudioFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /volume/i }));

      expect(onChange).toHaveBeenCalledTimes(1);
      const callArg = onChange.mock.calls[0][0];
      expect(Array.isArray(callArg)).toBe(true);
      expect(callArg[0]).toHaveProperty('type');
      expect(callArg[0]).toHaveProperty('enabled');
      expect(callArg[0]).toHaveProperty('params');
      expect(callArg[0]).toHaveProperty('order');
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icon per filter', () => {
      const filters = [
        createFilter('volume', { volume: 1.5 }, { order: 0 }),
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }, { order: 1 }),
      ];
      renderAudioFilters(filters);

      const infoIcons = screen.getAllByTestId('info-icon');
      expect(infoIcons.length).toBeGreaterThanOrEqual(2);
    });

    it('loudnorm tooltip explains EBU R128', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('loudnorm', { I: -24, LRA: 7, TP: -2 }, { order: 0 }),
      ];
      renderAudioFilters(filters);

      const infoIcons = screen.getAllByTestId('info-icon');
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent).toMatch(/ebu r128|loudness normalization/i);
      });
    });

    it('volume tooltip explains dB scale', async () => {
      const user = userEvent.setup();
      const filters = [createFilter('volume', { volume: 1.5 }, { order: 0 })];
      renderAudioFilters(filters);

      const infoIcons = screen.getAllByTestId('info-icon');
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent).toMatch(/dB|decibel|volume/i);
      });
    });
  });
});
