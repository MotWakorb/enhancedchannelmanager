/**
 * Unit tests for PrintGuideModal — per-group channel-number range and empty-slot
 * placeholders.
 *
 * bd-9eyqp: Replace the single global From/To range (bd-9q9z0) with per-group
 * From/To inputs that default to each group's min/max used channel number and
 * are independently editable. The empty-slot fill (bd-w532y) uses each group's
 * own [From,To].
 *
 * bd-w532y: Empty-slot placeholders — when "Show empty slots" is enabled,
 * generatePrintHtml fills in placeholder rows for channel numbers within a
 * group's per-group range that have no real channel assigned.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { PrintGuideModal, generatePrintHtml, MAX_EMPTY_SLOTS_PER_GROUP } from './PrintGuideModal';
import type { GroupPrintSettings, GroupRangeMap } from './PrintGuideModal';
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

// Sports: 100, 105, 110 — News: 200, 205, 210
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

// --- Test suite ---

describe('PrintGuideModal — per-group range defaults (bd-9eyqp)', () => {
  it('Sports group From input defaults to its minimum channel (100)', () => {
    renderModal();
    // Each group-item renders its own From/To inputs labeled with the group name context
    const sportsSection = screen.getByText('Sports').closest('.group-item') as HTMLElement;
    const fromInput = within(sportsSection).getByLabelText(/from/i) as HTMLInputElement;
    expect(fromInput.value).toBe('100');
  });

  it('Sports group To input defaults to its maximum channel (110)', () => {
    renderModal();
    const sportsSection = screen.getByText('Sports').closest('.group-item') as HTMLElement;
    const toInput = within(sportsSection).getByLabelText(/to/i) as HTMLInputElement;
    expect(toInput.value).toBe('110');
  });

  it('News group From input defaults to its minimum channel (200)', () => {
    renderModal();
    const newsSection = screen.getByText('News').closest('.group-item') as HTMLElement;
    const fromInput = within(newsSection).getByLabelText(/from/i) as HTMLInputElement;
    expect(fromInput.value).toBe('200');
  });

  it('News group To input defaults to its maximum channel (210)', () => {
    renderModal();
    const newsSection = screen.getByText('News').closest('.group-item') as HTMLElement;
    const toInput = within(newsSection).getByLabelText(/to/i) as HTMLInputElement;
    expect(toInput.value).toBe('210');
  });

  it('both groups are visible by default (no global range filter)', () => {
    renderModal();
    expect(screen.getByText('Sports')).toBeInTheDocument();
    expect(screen.getByText('News')).toBeInTheDocument();
  });

  it('Print button is enabled at default ranges', () => {
    renderModal();
    expect(
      screen.getByRole('button', { name: /print selected/i })
    ).not.toBeDisabled();
  });
});

describe('PrintGuideModal — per-group range editing is independent (bd-9eyqp)', () => {
  it('editing News group To does not affect Sports group To', () => {
    renderModal();
    const newsSection = screen.getByText('News').closest('.group-item') as HTMLElement;
    const newsToInput = within(newsSection).getByLabelText(/to/i) as HTMLInputElement;
    fireEvent.change(newsToInput, { target: { value: '251' } });

    const sportsSection = screen.getByText('Sports').closest('.group-item') as HTMLElement;
    const sportsToInput = within(sportsSection).getByLabelText(/to/i) as HTMLInputElement;
    expect(sportsToInput.value).toBe('110'); // unchanged
  });

  it('editing Sports From does not affect News From', () => {
    renderModal();
    const sportsSection = screen.getByText('Sports').closest('.group-item') as HTMLElement;
    const sportsFromInput = within(sportsSection).getByLabelText(/from/i) as HTMLInputElement;
    fireEvent.change(sportsFromInput, { target: { value: '105' } });

    const newsSection = screen.getByText('News').closest('.group-item') as HTMLElement;
    const newsFromInput = within(newsSection).getByLabelText(/from/i) as HTMLInputElement;
    expect(newsFromInput.value).toBe('200'); // unchanged
  });

  it('Print button remains enabled when only one group has From > To (other group ok)', () => {
    // Per-group validation: only blocks that group's print, not the whole button
    // (unless ALL selected groups have invalid ranges — but here News is still valid)
    renderModal();
    const sportsSection = screen.getByText('Sports').closest('.group-item') as HTMLElement;
    const sportsFromInput = within(sportsSection).getByLabelText(/from/i) as HTMLInputElement;
    const sportsToInput = within(sportsSection).getByLabelText(/to/i) as HTMLInputElement;
    fireEvent.change(sportsFromInput, { target: { value: '110' } });
    fireEvent.change(sportsToInput, { target: { value: '100' } });
    // News group is still valid; Print should remain enabled
    expect(screen.getByRole('button', { name: /print selected/i })).not.toBeDisabled();
  });
});

describe('PrintGuideModal — no global From/To controls (bd-9eyqp)', () => {
  it('does not render a standalone global "From channel #" input outside group rows', () => {
    renderModal();
    // There should be no single global From/To — only per-group ones inside .group-item
    const allFromLabels = screen.getAllByLabelText(/from/i) as HTMLInputElement[];
    // Each From label should be inside a .group-item
    allFromLabels.forEach(input => {
      expect(input.closest('.group-item')).not.toBeNull();
    });
  });
});

describe('PrintGuideModal — Show empty slots toggle (bd-w532y, preserved)', () => {
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

// ---------------------------------------------------------------------------
// generatePrintHtml — per-group range + empty-slot tests (bd-9eyqp + bd-w532y)
// ---------------------------------------------------------------------------

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
  fromRaw = '1',
  toRaw = '9999',
): GroupPrintSettings {
  return { groupId, selected, mode, fromRaw, toRaw };
}

describe('generatePrintHtml — per-group range filter (bd-9eyqp)', () => {
  const GROUP_PPV: ChannelGroup = { id: 10, name: 'PPV', channel_count: 5 };
  const GROUP_SPORTS: ChannelGroup = { id: 20, name: 'Sports', channel_count: 3 };

  // PPV: 1101..1201, Sports: 100..110
  const PPV_CHANNELS = Array.from({ length: 101 }, (_, i) =>
    makeChannelForHtml(1000 + i, 1101 + i, 10)
  );
  const SPORTS_CHANNELS = [
    makeChannelForHtml(1, 100, 20),
    makeChannelForHtml(2, 105, 20),
    makeChannelForHtml(3, 110, 20),
  ];
  const ALL_CHANNELS = [...PPV_CHANNELS, ...SPORTS_CHANNELS];

  it('PO example: PPV defaults From=1101 To=1201 — no empty slots', () => {
    // With per-group range defaulting to the group's own min/max, no empty slots appear
    const rangeMap: GroupRangeMap = { 10: { from: 1101, to: 1201 }, 20: { from: 100, to: 110 } };
    const html = generatePrintHtml(
      ALL_CHANNELS,
      [GROUP_PPV, GROUP_SPORTS],
      [makeGroupSettings(10), makeGroupSettings(20)],
      'Test Guide',
      rangeMap,
      false, // showEmptySlots off — no gaps expected anyway
    );
    expect(html).toContain('>1101<');
    expect(html).toContain('>1201<');
    expect(html).not.toContain('class="ch-slot-label"');
  });

  it('PO example: operator edits PPV To to 1251 → empty slots 1202-1251 for PPV only', () => {
    // Sports remains at 100-110 unchanged
    const rangeMap: GroupRangeMap = { 10: { from: 1101, to: 1251 }, 20: { from: 100, to: 110 } };
    const html = generatePrintHtml(
      ALL_CHANNELS,
      [GROUP_PPV, GROUP_SPORTS],
      [makeGroupSettings(10), makeGroupSettings(20)],
      'Test Guide',
      rangeMap,
      true,
    );
    // PPV real channels present
    expect(html).toContain('>1101<');
    expect(html).toContain('>1201<');
    // PPV empty slots 1202-1251 present
    expect(html).toContain('>1202<');
    expect(html).toContain('>1251<');
    // Sports real channels present
    expect(html).toContain('>100<');
    expect(html).toContain('>110<');
    // Sports does NOT have channels at 111-..., empty slots only for PPV beyond 1201
    // (Sports group ends at 110, its To is 110 — no extension)
    expect(html).not.toContain('>111<');
  });

  it('narrowing a group From/To filters that group\'s real channels', () => {
    // Sports From=105 To=110 — channel 100 excluded
    const rangeMap: GroupRangeMap = { 20: { from: 105, to: 110 } };
    const html = generatePrintHtml(
      SPORTS_CHANNELS,
      [GROUP_SPORTS],
      [makeGroupSettings(20)],
      'Test Guide',
      rangeMap,
      false,
    );
    expect(html).not.toContain('>100<');
    expect(html).toContain('>105<');
    expect(html).toContain('>110<');
  });

  it('each group uses only its own range — other groups unaffected', () => {
    const rangeMap: GroupRangeMap = {
      10: { from: 1101, to: 1110 }, // narrow PPV to first 10
      20: { from: 100, to: 110 },
    };
    const html = generatePrintHtml(
      ALL_CHANNELS,
      [GROUP_PPV, GROUP_SPORTS],
      [makeGroupSettings(10), makeGroupSettings(20)],
      'Test Guide',
      rangeMap,
      false,
    );
    // Only PPV channels 1101-1110 present
    expect(html).toContain('>1101<');
    expect(html).toContain('>1110<');
    expect(html).not.toContain('>1111<');
    // Sports unaffected — all 3 channels present
    expect(html).toContain('>100<');
    expect(html).toContain('>105<');
    expect(html).toContain('>110<');
  });
});

describe('generatePrintHtml — empty-slot placeholders with per-group range (bd-w532y)', () => {
  const GROUP: ChannelGroup = { id: 1, name: 'Sports', channel_count: 3 };

  // Sparse group: channels at 100, 105, 110
  const SPARSE_CHANNELS = [
    makeChannelForHtml(1, 100, 1),
    makeChannelForHtml(2, 105, 1),
    makeChannelForHtml(3, 110, 1),
  ];

  describe('showEmptySlots = false', () => {
    it('renders only real channel rows — no placeholder rows', () => {
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 115 } };
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        false,
      );
      expect(html).toContain('>100<');
      expect(html).toContain('>105<');
      expect(html).toContain('>110<');
      expect(html).not.toContain('class="ch-slot-label"');
    });
  });

  describe('showEmptySlots = true', () => {
    it('includes a placeholder row for each gap in the group range', () => {
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 112 } };
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).toContain('>100<');
      expect(html).toContain('>105<');
      expect(html).toContain('>110<');
      // Gap numbers
      expect(html).toContain('>101<');
      expect(html).toContain('>104<');
      expect(html).toContain('>106<');
      expect(html).toContain('>109<');
      expect(html).toContain('>111<');
      expect(html).toContain('>112<');
      expect(html).toContain('class="ch-slot-label"');
    });

    it('does not add placeholders before the group From value', () => {
      // From=105: channel 100 excluded; gaps start at 106
      const rangeMap: GroupRangeMap = { 1: { from: 105, to: 112 } };
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).toContain('>105<');
      expect(html).toContain('>110<');
      expect(html).not.toContain('>100<');
      expect(html).not.toContain('>104<');
      expect(html).toContain('>106<');
      expect(html).toContain('>111<');
      expect(html).toContain('>112<');
    });

    it('extends placeholders to group To when To > group last channel', () => {
      const channels = [
        makeChannelForHtml(1, 100, 1),
        makeChannelForHtml(2, 105, 1),
      ];
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 108 } };
      const html = generatePrintHtml(
        channels,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).toContain('>100<');
      expect(html).toContain('>105<');
      expect(html).toContain('>101<');
      expect(html).toContain('>106<');
      expect(html).toContain('>107<');
      expect(html).toContain('>108<');
    });

    it('inclusive bounds: group From and To endpoints are included', () => {
      const channels = [makeChannelForHtml(1, 100, 1)];
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 102 } };
      const html = generatePrintHtml(
        channels,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).toContain('>101<');
      expect(html).toContain('>102<');
      expect(html).not.toContain('>103<');
    });

    it('no placeholders when group channels are fully contiguous in the range', () => {
      const contiguous = [
        makeChannelForHtml(1, 100, 1),
        makeChannelForHtml(2, 101, 1),
        makeChannelForHtml(3, 102, 1),
      ];
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 102 } };
      const html = generatePrintHtml(
        contiguous,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).not.toContain('class="ch-slot-label"');
    });

    it('summary mode groups never emit empty-slot rows regardless of showEmptySlots', () => {
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 115 } };
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1, true, 'summary')],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).not.toContain('class="ch-slot-label"');
    });

    it(`truncates at ${MAX_EMPTY_SLOTS_PER_GROUP} empty slots and appends a warning row`, () => {
      const bigRange = [makeChannelForHtml(1, 1, 1)];
      const rangeEnd = MAX_EMPTY_SLOTS_PER_GROUP + 50;
      const rangeMap: GroupRangeMap = { 1: { from: 1, to: rangeEnd } };
      const html = generatePrintHtml(
        bigRange,
        [GROUP],
        [makeGroupSettings(1)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).toContain('empty-slot limit reached');
      expect(html).not.toContain(`>${rangeEnd}<`);
    });

    // bd-szsb2: float/decimal channel numbers (e.g. 2.1, 5.1) must render with
    // empty slots on. The old integer-walk lookup skipped every float channel and
    // emitted only greyed-out integer placeholders.
    describe('float channel numbers (bd-szsb2)', () => {
      const FLOAT_GROUP: ChannelGroup = { id: 7, name: 'OTA', channel_count: 4 };
      // 2.1, 5.1, 38.5 — sparse decimals
      const FLOAT_CHANNELS = [
        makeChannelForHtml(1, 2.1, 7, 'ABC'),
        makeChannelForHtml(2, 5.1, 7, 'NBC'),
        makeChannelForHtml(3, 38.5, 7, 'PBS'),
      ];

      it('renders real float channels (not skipped) with empty slots on', () => {
        const rangeMap: GroupRangeMap = { 7: { from: 2.1, to: 38.5 } };
        const html = generatePrintHtml(
          FLOAT_CHANNELS,
          [FLOAT_GROUP],
          [makeGroupSettings(7)],
          'Test Guide',
          rangeMap,
          true,
        );
        expect(html).toContain('>2.1<');
        expect(html).toContain('>5.1<');
        expect(html).toContain('>38.5<');
        expect(html).toContain('ABC');
        expect(html).toContain('NBC');
        expect(html).toContain('PBS');
      });

      it('skips integer gap-fill entirely for a float group (no placeholder rows)', () => {
        // PO decision: a group with any float channel renders only its real
        // channels — the integer gaps (3, 4 between 2.1 and 5.1) are unused
        // major numbers, not "missing channels", so no placeholders appear.
        const rangeMap: GroupRangeMap = { 7: { from: 2.1, to: 5.1 } };
        const html = generatePrintHtml(
          FLOAT_CHANNELS.slice(0, 2),
          [FLOAT_GROUP],
          [makeGroupSettings(7)],
          'Test Guide',
          rangeMap,
          true,
        );
        // Real float channels present
        expect(html).toContain('>2.1<');
        expect(html).toContain('>5.1<');
        // No empty-slot placeholders at all
        expect(html).not.toContain('class="ch-slot-label"');
        expect(html).not.toContain('>3<');
        expect(html).not.toContain('>4<');
      });

      it('includes the highest float channel (no parseInt truncation of To bound)', () => {
        // Regression for the secondary bug: To=38.5 must include channel 38.5
        const rangeMap: GroupRangeMap = { 7: { from: 2.1, to: 38.5 } };
        const html = generatePrintHtml(
          FLOAT_CHANNELS,
          [FLOAT_GROUP],
          [makeGroupSettings(7, true, 'detailed', '2.1', '38.5')],
          'Test Guide',
          rangeMap,
          false,
        );
        expect(html).toContain('>38.5<');
      });
    });

    it('deselected group produces no output even with showEmptySlots=true', () => {
      const rangeMap: GroupRangeMap = { 1: { from: 100, to: 115 } };
      const html = generatePrintHtml(
        SPARSE_CHANNELS,
        [GROUP],
        [makeGroupSettings(1, false)],
        'Test Guide',
        rangeMap,
        true,
      );
      expect(html).not.toContain('>Sports<');
      expect(html).not.toContain('class="ch-slot-label"');
    });
  });
});
