/**
 * TDD Tests for VideoFilters component (Spec 1.5).
 *
 * These tests define the expected behavior of the VideoFilters component
 * BEFORE implementation. They will FAIL until the component is built.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { VideoFilters } from './VideoFilters';
import type { VideoFilter } from '../../types/ffmpegBuilder';

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

function renderVideoFilters(
  filters: VideoFilter[] = [],
  props: {
    onChange?: ReturnType<typeof vi.fn>;
    hwAccel?: string;
  } = {}
) {
  const onChange = props.onChange ?? vi.fn();
  return {
    onChange,
    ...render(
      <VideoFilters
        value={filters}
        onChange={onChange}
        hwAccel={props.hwAccel}
      />
    ),
  };
}

function createFilter(
  type: VideoFilter['type'],
  params: Record<string, string | number | boolean> = {},
  overrides: Partial<VideoFilter> = {}
): VideoFilter {
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

describe('VideoFilters', () => {
  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  describe('rendering', () => {
    it('renders filter list (empty initially)', () => {
      renderVideoFilters([]);

      // Should show an empty state or list area
      expect(screen.getByTestId('video-filter-list')).toBeInTheDocument();
    });

    it('renders add filter button', () => {
      renderVideoFilters([]);

      expect(
        screen.getByRole('button', { name: /add filter/i })
      ).toBeInTheDocument();
    });

    it('renders filter chain preview', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
        createFilter('fps', { fps: 30 }, { order: 1 }),
      ];
      renderVideoFilters(filters);

      // Should display the filter chain as a preview string
      expect(screen.getByTestId('filter-chain-preview')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Adding Filters
  // -------------------------------------------------------------------------
  describe('adding filters', () => {
    it('can add scale filter', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /scale/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'scale' }),
        ])
      );
    });

    it('can add fps filter', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /fps/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'fps' }),
        ])
      );
    });

    it('can add deinterlace filter', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /deinterlace/i }));

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'deinterlace' }),
        ])
      );
    });

    it('shows filter parameters after adding', () => {
      renderVideoFilters([
        createFilter('scale', { width: 1920, height: 1080 }),
      ]);

      // Parameters should be visible for the added filter
      expect(screen.getByLabelText(/width/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/height/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Scale Filter
  // -------------------------------------------------------------------------
  describe('scale filter', () => {
    it('shows width and height inputs', () => {
      renderVideoFilters([
        createFilter('scale', { width: 1920, height: 1080 }),
      ]);

      expect(screen.getByLabelText(/width/i)).toHaveValue(1920);
      expect(screen.getByLabelText(/height/i)).toHaveValue(1080);
    });

    it('supports common presets (1080p, 720p, 480p)', () => {
      renderVideoFilters([
        createFilter('scale', { width: 1920, height: 1080 }),
      ]);

      expect(
        screen.getByRole('button', { name: /1080p/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /720p/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /480p/i })
      ).toBeInTheDocument();
    });

    it('maintains aspect ratio option', () => {
      renderVideoFilters([
        createFilter('scale', { width: 1920, height: 1080 }),
      ]);

      expect(
        screen.getByLabelText(/maintain aspect ratio|keep aspect/i)
      ).toBeInTheDocument();
    });

    it('shows output resolution preview', () => {
      renderVideoFilters([
        createFilter('scale', { width: 1920, height: 1080 }),
      ]);

      expect(screen.getByText(/1920\s*[x\u00d7]\s*1080/)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // FPS Filter
  // -------------------------------------------------------------------------
  describe('fps filter', () => {
    it('shows fps input', () => {
      renderVideoFilters([createFilter('fps', { fps: 30 })]);

      expect(screen.getByLabelText(/fps|frame.?rate/i)).toBeInTheDocument();
    });

    it('common fps presets (24, 25, 30, 60)', () => {
      renderVideoFilters([createFilter('fps', { fps: 30 })]);

      expect(
        screen.getByRole('button', { name: /^24$/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /^25$/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /^30$/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /^60$/i })
      ).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Filter Chain
  // -------------------------------------------------------------------------
  describe('filter chain', () => {
    it('shows filters in order', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
        createFilter('fps', { fps: 30 }, { order: 1 }),
        createFilter('deinterlace', {}, { order: 2 }),
      ];
      renderVideoFilters(filters);

      const filterItems = screen.getAllByTestId('filter-item');
      expect(filterItems).toHaveLength(3);

      expect(within(filterItems[0]).getByText(/scale/i)).toBeInTheDocument();
      expect(within(filterItems[1]).getByText(/fps/i)).toBeInTheDocument();
      expect(within(filterItems[2]).getByText(/deinterlace/i)).toBeInTheDocument();
    });

    it('can reorder filters via drag or arrows', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
        createFilter('fps', { fps: 30 }, { order: 1 }),
      ];
      const { onChange } = renderVideoFilters(filters);

      // Click move-down on the first filter
      const filterItems = screen.getAllByTestId('filter-item');
      const moveDownBtn = within(filterItems[0]).getByRole('button', {
        name: /move down|reorder/i,
      });
      await user.click(moveDownBtn);

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'fps', order: 0 }),
          expect.objectContaining({ type: 'scale', order: 1 }),
        ])
      );
    });

    it('can enable/disable individual filters', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0, enabled: true }),
      ];
      const { onChange } = renderVideoFilters(filters);

      const toggleBtn = screen.getByRole('checkbox', { name: /enable|toggle/i });
      await user.click(toggleBtn);

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'scale', enabled: false }),
        ])
      );
    });

    it('can remove filters', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
        createFilter('fps', { fps: 30 }, { order: 1 }),
      ];
      const { onChange } = renderVideoFilters(filters);

      const filterItems = screen.getAllByTestId('filter-item');
      const removeBtn = within(filterItems[0]).getByRole('button', {
        name: /remove|delete/i,
      });
      await user.click(removeBtn);

      expect(onChange).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ type: 'fps' }),
        ])
      );
      // The removed filter should not be in the callback
      const callArg = onChange.mock.calls[onChange.mock.calls.length - 1][0];
      expect(callArg).toHaveLength(1);
    });

    it('disabled filters excluded from command', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0, enabled: true }),
        createFilter('fps', { fps: 30 }, { order: 1, enabled: false }),
      ];
      renderVideoFilters(filters);

      const preview = screen.getByTestId('filter-chain-preview');
      // The preview should contain scale but not fps
      expect(preview.textContent).toMatch(/scale/i);
      expect(preview.textContent).not.toMatch(/fps/i);
    });
  });

  // -------------------------------------------------------------------------
  // Hardware Filters
  // -------------------------------------------------------------------------
  describe('hardware filters', () => {
    it('shows hwupload when VAAPI selected', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
      ];
      renderVideoFilters(filters, { hwAccel: 'vaapi' });

      // Should auto-show hwupload in the filter chain
      expect(screen.getByText(/hwupload/i)).toBeInTheDocument();
    });

    it('shows format=nv12 before hwupload', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
      ];
      renderVideoFilters(filters, { hwAccel: 'vaapi' });

      const preview = screen.getByTestId('filter-chain-preview');
      // format=nv12 should appear before hwupload_vaapi
      const text = preview.textContent!;
      const formatIdx = text.indexOf('format=nv12');
      const hwuploadIdx = text.indexOf('hwupload');
      expect(formatIdx).toBeGreaterThan(-1);
      expect(hwuploadIdx).toBeGreaterThan(-1);
      expect(formatIdx).toBeLessThan(hwuploadIdx);
    });

    it('auto-inserts hw filters for VAAPI pipeline', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
      ];
      renderVideoFilters(filters, { hwAccel: 'vaapi' });

      const preview = screen.getByTestId('filter-chain-preview');
      // Should contain VAAPI-specific filters
      expect(preview.textContent).toMatch(/format=nv12/);
      expect(preview.textContent).toMatch(/hwupload/);
    });
  });

  // -------------------------------------------------------------------------
  // Custom Filter
  // -------------------------------------------------------------------------
  describe('custom filter', () => {
    it('can add custom filter string', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /custom/i }));

      // Should show a text input for custom filter string
      const customInput = screen.getByLabelText(/custom filter|filter string/i);
      await user.type(customInput, 'eq=brightness=0.06:saturation=1.5');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.arrayContaining([
            expect.objectContaining({
              type: 'custom',
              params: expect.objectContaining({
                filterString: 'eq=brightness=0.06:saturation=1.5',
              }),
            }),
          ])
        );
      });
    });

    it('validates filter syntax', async () => {
      const user = userEvent.setup();
      renderVideoFilters([
        createFilter('custom', { filterString: '' }),
      ]);

      const customInput = screen.getByLabelText(/custom filter|filter string/i);
      await user.clear(customInput);

      // Empty custom filter should show validation
      await waitFor(() => {
        expect(screen.getByText(/required|empty|invalid/i)).toBeInTheDocument();
      });
    });
  });

  // -------------------------------------------------------------------------
  // Callbacks
  // -------------------------------------------------------------------------
  describe('callbacks', () => {
    it('calls onChange with filter array', async () => {
      const user = userEvent.setup();
      const { onChange } = renderVideoFilters([]);

      await user.click(screen.getByRole('button', { name: /add filter/i }));
      await user.click(screen.getByRole('option', { name: /scale/i }));

      expect(onChange).toHaveBeenCalledTimes(1);
      const callArg = onChange.mock.calls[0][0];
      expect(Array.isArray(callArg)).toBe(true);
      expect(callArg[0]).toHaveProperty('type');
      expect(callArg[0]).toHaveProperty('enabled');
      expect(callArg[0]).toHaveProperty('params');
      expect(callArg[0]).toHaveProperty('order');
    });

    it('onChange reflects order changes', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
        createFilter('fps', { fps: 30 }, { order: 1 }),
      ];
      const { onChange } = renderVideoFilters(filters);

      const filterItems = screen.getAllByTestId('filter-item');
      const moveDownBtn = within(filterItems[0]).getByRole('button', {
        name: /move down|reorder/i,
      });
      await user.click(moveDownBtn);

      const callArg = onChange.mock.calls[onChange.mock.calls.length - 1][0];
      expect(callArg[0].order).toBeLessThan(callArg[1].order);
    });
  });

  // -------------------------------------------------------------------------
  // Explanations
  // -------------------------------------------------------------------------
  describe('explanations', () => {
    it('shows info icon per filter type', () => {
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
        createFilter('fps', { fps: 30 }, { order: 1 }),
      ];
      renderVideoFilters(filters);

      const infoIcons = screen.getAllByTestId('info-icon');
      expect(infoIcons.length).toBeGreaterThanOrEqual(2);
    });

    it('scale tooltip explains resolution', async () => {
      const user = userEvent.setup();
      const filters = [
        createFilter('scale', { width: 1920, height: 1080 }, { order: 0 }),
      ];
      renderVideoFilters(filters);

      const infoIcons = screen.getAllByTestId('info-icon');
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent).toMatch(/resolution|scale|resize/i);
      });
    });

    it('deinterlace tooltip explains interlacing', async () => {
      const user = userEvent.setup();
      const filters = [createFilter('deinterlace', {}, { order: 0 })];
      renderVideoFilters(filters);

      const infoIcons = screen.getAllByTestId('info-icon');
      await user.hover(infoIcons[0]);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        expect(tooltip).toBeInTheDocument();
        expect(tooltip.textContent).toMatch(/interlac/i);
      });
    });
  });
});
