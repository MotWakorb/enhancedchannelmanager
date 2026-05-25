/**
 * Unit tests for PrintGuideModal — channel-number range filter and empty-slot
 * placeholders.
 *
 * bd-9q9z0: Operator-selectable channel-number range filter for the Print
 * Channel Guide modal. Defaults to the full range so existing all-channels
 * behaviour is preserved; operators can narrow to a sub-range (e.g. 100–199)
 * for booklet sections. Range is applied before the existing group filter.
 *
 * bd-w532y: Empty-slot placeholders — when "Show empty slots" is enabled,
 * generatePrintHtml fills in placeholder rows for channel numbers within a
 * group's range that have no real channel assigned. This lets operators print
 * a guide that reflects the INTENDED channel layout of a sparse auto-sync
 * group, not just what exists today.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PrintGuideModal, generatePrintHtml, MAX_EMPTY_SLOTS_PER_GROUP } from './PrintGuideModal';
import type { GroupPrintSettings } from './PrintGuideModal';
import type { Channel, ChannelGroup } from '../types';

// --- Test fixtures ---

const GROUP_A: ChannelGroup = { id: 1, name: 'Sports', channel_count: 3 };
const GROUP_B: ChannelGroup = { id: 2, name: 'News', channel_count: 3 };

function makeChannel(id: number, num: number | null, groupId: number | null): Channel {
  return {
    id,
    channel_number: num,
    name: `Channel ${num ?? 'no-num'}`,
    channel_group_id: groupId,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

// Channels 100–110 in Sports, 200–210 in News
const CHANNELS: Channel[] = [
  makeChannel(1, 100, 1),
  makeChannel(2, 105, 1),
  makeChannel(3, 110, 1),
  makeChannel(4, 200, 2),
  makeChannel(5, 205, 2),
  makeChannel(6, 210, 2),
];

const GROUPS: ChannelGroup[] = [GROUP_A, GROUP_B];

function renderModal(channels = CHANNELS, groups = GROUPS) {
  const onClose = vi.fn();
  render(
    <PrintGuideModal
      isOpen={true}
      onClose={onClose}
      channelGroups={groups}
      channels={channels}
      title="Test Guide"
    />
  );
  return { onClose };
}

function getFromInput() {
  return screen.getByLabelText(/from channel/i) as HTMLInputElement;
}

function getToInput() {
  return screen.getByLabelText(/to channel/i) as HTMLInputElement;
}

// --- Test suite ---

describe('PrintGuideModal — channel range filter (bd-9q9z0)', () => {
  describe('default range covers all channels (regression guard)', () => {
    it('From input defaults to the lowest channel number in the guide', () => {
      renderModal();
      expect(getFromInput().value).toBe('100');
    });

    it('To input defaults to the highest channel number in the guide', () => {
      renderModal();
      expect(getToInput().value).toBe('210');
    });

    it('all groups remain visible at default range', () => {
      renderModal();
      expect(screen.getByText('Sports')).toBeInTheDocument();
      expect(screen.getByText('News')).toBeInTheDocument();
    });

    it('Print button is enabled at default range', () => {
      renderModal();
      expect(
        screen.getByRole('button', { name: /print selected/i })
      ).not.toBeDisabled();
    });
  });

  describe('narrowed range filters channels in the group list', () => {
    it('setting To=150 hides the News group (200–210) from the group list', () => {
      renderModal();
      const toInput = getToInput();
      fireEvent.change(toInput, { target: { value: '150' } });
      // News group has no channels in 100–150, should be hidden from group list
      expect(screen.queryByText('News')).not.toBeInTheDocument();
      expect(screen.getByText('Sports')).toBeInTheDocument();
    });

    it('setting From=200 hides the Sports group (100–110) from the group list', () => {
      renderModal();
      const fromInput = getFromInput();
      fireEvent.change(fromInput, { target: { value: '200' } });
      expect(screen.queryByText('Sports')).not.toBeInTheDocument();
      expect(screen.getByText('News')).toBeInTheDocument();
    });

    it('setting From=105 To=205 shows both groups (each has at least one channel in range)', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '105' } });
      fireEvent.change(getToInput(), { target: { value: '205' } });
      expect(screen.getByText('Sports')).toBeInTheDocument();
      expect(screen.getByText('News')).toBeInTheDocument();
    });
  });

  describe('invalid range — From > To', () => {
    it('shows a validation error when From > To', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '210' } });
      fireEvent.change(getToInput(), { target: { value: '100' } });
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByRole('alert').textContent).toMatch(/from.*must.*to|range.*invalid/i);
    });

    it('disables the Print button when From > To', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '210' } });
      fireEvent.change(getToInput(), { target: { value: '100' } });
      expect(screen.getByRole('button', { name: /print selected/i })).toBeDisabled();
    });
  });

  describe('out-of-bounds clamping with operator hint', () => {
    it('clamps From below the minimum and shows an Adjusted hint', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '1' } });
      fireEvent.blur(getFromInput());
      // Should clamp to 100 (the minimum)
      expect(getFromInput().value).toBe('100');
      expect(screen.getByText(/adjusted to/i)).toBeInTheDocument();
    });

    it('clamps To above the maximum and shows an Adjusted hint', () => {
      renderModal();
      fireEvent.change(getToInput(), { target: { value: '9999' } });
      fireEvent.blur(getToInput());
      // Should clamp to 210 (the maximum)
      expect(getToInput().value).toBe('210');
      expect(screen.getByText(/adjusted to/i)).toBeInTheDocument();
    });
  });

  describe('no channels in range — graceful empty state', () => {
    it('shows an empty state message when no groups have channels in the range', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '300' } });
      fireEvent.change(getToInput(), { target: { value: '400' } });
      expect(screen.getByText(/no channels in range/i)).toBeInTheDocument();
    });

    it('disables the Print button when no channels are in range', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '300' } });
      fireEvent.change(getToInput(), { target: { value: '400' } });
      expect(screen.getByRole('button', { name: /print selected/i })).toBeDisabled();
    });
  });

  describe('Show empty slots toggle (bd-w532y)', () => {
    it('renders the empty-slots checkbox', () => {
      renderModal();
      expect(screen.getByLabelText(/show empty slots/i)).toBeInTheDocument();
    });

    it('empty-slots checkbox is checked by default', () => {
      renderModal();
      const cb = screen.getByLabelText(/show empty slots/i) as HTMLInputElement;
      expect(cb.checked).toBe(true);
    });

    it('unchecking the empty-slots checkbox does not disable the Print button', () => {
      renderModal();
      const cb = screen.getByLabelText(/show empty slots/i);
      fireEvent.click(cb);
      expect(screen.getByRole('button', { name: /print selected/i })).not.toBeDisabled();
    });
  });
});

// ---------------------------------------------------------------------------
// generatePrintHtml — empty-slot placeholder unit tests (bd-w532y)
// ---------------------------------------------------------------------------
//
// These tests exercise the HTML-generation helper directly so we can assert on
// the rendered output without spinning up a full React component tree.

/** Build a minimal Channel for generatePrintHtml tests. */
function makeChannelForHtml(
  id: number,
  num: number,
  groupId: number,
  name = `Ch ${num}`,
): Channel {
  return {
    id,
    channel_number: num,
    name,
    channel_group_id: groupId,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

function makeGroupSettings(
  groupId: number,
  selected = true,
  mode: 'detailed' | 'summary' = 'detailed',
): GroupPrintSettings {
  return { groupId, selected, mode };
}

describe('generatePrintHtml — empty-slot placeholders (bd-w532y)', () => {
  const GROUP: ChannelGroup = { id: 1, name: 'Sports', channel_count: 3 };

  // Sparse group: channels at 100, 105, 110. Range 100–115. Gaps: 101-104, 106-109, 111-115.
  const SPARSE_CHANNELS = [
    makeChannelForHtml(1, 100, 1),
    makeChannelForHtml(2, 105, 1),
    makeChannelForHtml(3, 110, 1),
  ];

  describe('showEmptySlots = false (default-off path)', () => {
    it('renders only real channel rows — no placeholder rows', () => {
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        100,
        115,
        false,
      );
      // Real channels present
      expect(html).toContain('>100<');
      expect(html).toContain('>105<');
      expect(html).toContain('>110<');
      // No placeholder element (class="ch-slot-label" in HTML — CSS rule presence is expected)
      expect(html).not.toContain('class="ch-slot-label"');
    });
  });

  describe('showEmptySlots = true', () => {
    it('includes a placeholder row for each gap channel number in the range', () => {
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        100,
        112,
        true,
      );
      // Real channels
      expect(html).toContain('>100<');
      expect(html).toContain('>105<');
      expect(html).toContain('>110<');
      // Placeholder slots — gap numbers 101, 102, 103, 104, 106, 107, 108, 109, 111, 112
      expect(html).toContain('>101<');
      expect(html).toContain('>104<');
      expect(html).toContain('>106<');
      expect(html).toContain('>109<');
      expect(html).toContain('>111<');
      expect(html).toContain('>112<');
      // Placeholder marker element present
      expect(html).toContain('class="ch-slot-label"');
    });

    it('does not add placeholders before the first real channel in the group', () => {
      // First real channel is 105, range starts at 100. Slots 100-104 should NOT appear.
      const channelsStartingAt105 = [
        makeChannelForHtml(2, 105, 1),
        makeChannelForHtml(3, 110, 1),
      ];
      const html = generatePrintHtml(
        channelsStartingAt105,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        100,
        112,
        true,
      );
      // 105 and 110 present as real channels
      expect(html).toContain('>105<');
      expect(html).toContain('>110<');
      // 100–104 are before the group's first channel — should NOT appear in channel rows
      // (they may appear in CSS, but not as ch-num span content in a channel-line)
      expect(html).not.toContain('>100<');
      expect(html).not.toContain('>101<');
      expect(html).not.toContain('>104<');
      // 106–109, 111–112 are gaps within group range — should appear as placeholder rows
      expect(html).toContain('>106<');
      expect(html).toContain('>111<');
    });

    it('extends placeholders to rangeTo when rangeTo > group last channel', () => {
      // Group has channels at 100 and 105 only; range extends to 108.
      const channels = [
        makeChannelForHtml(1, 100, 1),
        makeChannelForHtml(2, 105, 1),
      ];
      const html = generatePrintHtml(
        channels,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        100,
        108,
        true,
      );
      // Real channels
      expect(html).toContain('>100<');
      expect(html).toContain('>105<');
      // Placeholder slots 101-104, 106-108
      expect(html).toContain('>101<');
      expect(html).toContain('>106<');
      expect(html).toContain('>107<');
      expect(html).toContain('>108<');
    });

    it('inclusive bounds: rangeFrom and rangeTo endpoints are included', () => {
      const channels = [makeChannelForHtml(1, 100, 1)];
      const html = generatePrintHtml(
        channels,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        100,
        102,
        true,
      );
      expect(html).toContain('>101<');
      expect(html).toContain('>102<');
      // 103 is outside rangeTo — should NOT appear
      expect(html).not.toContain('>103<');
    });

    it('no placeholders when group channels are fully contiguous in the range', () => {
      // 100, 101, 102 — no gaps
      const contiguous = [
        makeChannelForHtml(1, 100, 1),
        makeChannelForHtml(2, 101, 1),
        makeChannelForHtml(3, 102, 1),
      ];
      const html = generatePrintHtml(
        contiguous,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        100,
        102,
        true,
      );
      // No placeholder elements (CSS class definition present in <style> is expected)
      expect(html).not.toContain('class="ch-slot-label"');
    });

    it('summary mode groups never emit empty-slot rows regardless of showEmptySlots', () => {
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1, true, 'summary')],
        'Test Guide',
        100,
        115,
        true,
      );
      // Summary mode just shows a range label — no per-number placeholder elements
      expect(html).not.toContain('class="ch-slot-label"');
    });

    it(`truncates at ${MAX_EMPTY_SLOTS_PER_GROUP} empty slots and appends a warning row`, () => {
      // Single channel at 1; range extends far enough to exceed the cap.
      const bigRange = [makeChannelForHtml(1, 1, 1)];
      const rangeEnd = MAX_EMPTY_SLOTS_PER_GROUP + 50; // well over cap
      const html = generatePrintHtml(
        bigRange,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        1,
        rangeEnd,
        true,
      );
      // Truncation warning row must appear
      expect(html).toContain('empty-slot limit reached');
      // Should NOT contain channel numbers beyond the cap
      expect(html).not.toContain(`>${rangeEnd}<`);
    });

    it('deselected group produces no output even with showEmptySlots=true', () => {
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1, false)], // not selected
        'Test Guide',
        100,
        115,
        true,
      );
      // Group completely absent — no real channels, no placeholder elements
      expect(html).not.toContain('>Sports<');
      expect(html).not.toContain('class="ch-slot-label"');
    });
  });
});
