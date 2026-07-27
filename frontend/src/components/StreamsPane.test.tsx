/**
 * Tests for StreamsPane category headers (bead enhancedchannelmanager-09x38.5).
 *
 * The Streams pane used to render ~90+ provider stream groups as one flat
 * alphabetical accordion. These tests lock in the collapsible category
 * layer added on top: categories are derived from the group-name prefix
 * convention (see utils/streamGroupCategories.ts), default to collapsed,
 * persist their expand/collapse state per session in localStorage, and
 * auto-surface while a search is active.
 */
import { describe, it, expect, vi, beforeEach, beforeAll, afterEach, afterAll } from 'vitest';
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { StreamsPane } from './StreamsPane';
import { server } from '../test/mocks/server';
import { tabUntil } from '../test/utils/keyboardNav';
import type { Stream, StreamGroupInfo, M3UAccount, ChannelGroup } from '../types';

function makeStream(overrides: Partial<Stream> & { id: number; name: string; channel_group_name: string }): Stream {
  const defaults: Stream = {
    id: overrides.id,
    name: overrides.name,
    url: 'http://example.com/stream.m3u8',
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: overrides.channel_group_name,
    is_custom: false,
  };
  return { ...defaults, ...overrides };
}

// Live naming-convention sample (bead 09x38.5 field-value survey):
// "CA | ..." / "CA| ..." both fold into category "CA"; "US" and "USA" stay
// distinct; "Default Group" has no delimiter and falls into "Other".
const STREAMS: Stream[] = [
  makeStream({ id: 1, name: 'CA Documentary Stream 1', channel_group_name: 'CA | Documentary' }),
  makeStream({ id: 2, name: 'CA Kids Stream 1', channel_group_name: 'CA| KIDS EN' }),
  makeStream({ id: 3, name: 'UK Sports Stream 1', channel_group_name: 'UK | Sports' }),
  makeStream({ id: 4, name: 'US News Stream 1', channel_group_name: 'US | News' }),
  makeStream({ id: 5, name: 'Default Stream 1', channel_group_name: 'Default Group' }),
];

const STREAM_GROUPS: StreamGroupInfo[] = [
  { name: 'CA | Documentary', count: 1 },
  { name: 'CA| KIDS EN', count: 1 },
  { name: 'UK | Sports', count: 1 },
  { name: 'US | News', count: 1 },
  { name: 'Default Group', count: 1 },
];

const PROVIDERS: M3UAccount[] = [];

function renderPane(overrides: Partial<React.ComponentProps<typeof StreamsPane>> = {}) {
  return render(
    <StreamsPane
      streams={STREAMS}
      providers={PROVIDERS}
      streamGroups={STREAM_GROUPS}
      searchTerm=""
      onSearchChange={vi.fn()}
      providerFilter={null}
      onProviderFilterChange={vi.fn()}
      groupFilter={null}
      onGroupFilterChange={vi.fn()}
      loading={false}
      onGroupExpand={vi.fn()}
      {...overrides}
    />
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe('StreamsPane category headers', () => {
  it('groups stream groups under category headers derived from the name prefix', () => {
    renderPane();
    expect(screen.getByRole('button', { name: /^CA/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^UK/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^US/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Other/ })).toBeInTheDocument();
  });

  it('shows the group count on each category header', () => {
    renderPane();
    // "CA" category has 2 groups (CA | Documentary, CA| KIDS EN)
    const caHeader = screen.getByRole('button', { name: /^CA/ });
    expect(within(caHeader).getByText('2')).toBeInTheDocument();
  });

  it('defaults to collapsed: group headers are not rendered until the category is expanded', () => {
    renderPane();
    expect(screen.queryByText('CA | Documentary')).not.toBeInTheDocument();
    expect(screen.queryByText('UK | Sports')).not.toBeInTheDocument();
  });

  it('expands a category on click, revealing its groups, and sets aria-expanded', async () => {
    const user = userEvent.setup();
    renderPane();
    const caHeader = screen.getByRole('button', { name: /^CA/ });
    expect(caHeader).toHaveAttribute('aria-expanded', 'false');

    await user.click(caHeader);

    expect(caHeader).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('CA | Documentary')).toBeInTheDocument();
    expect(screen.getByText('CA| KIDS EN')).toBeInTheDocument();
    // A sibling category stays collapsed
    expect(screen.queryByText('UK | Sports')).not.toBeInTheDocument();
  });

  it('collapses an expanded category back on a second click', async () => {
    const user = userEvent.setup();
    renderPane();
    const caHeader = screen.getByRole('button', { name: /^CA/ });

    await user.click(caHeader);
    expect(screen.getByText('CA | Documentary')).toBeInTheDocument();

    await user.click(caHeader);
    expect(caHeader).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('CA | Documentary')).not.toBeInTheDocument();
  });

  it('persists category expand state to localStorage across remounts', async () => {
    const user = userEvent.setup();
    const { unmount } = renderPane();
    await user.click(screen.getByRole('button', { name: /^UK/ }));
    expect(screen.getByText('UK | Sports')).toBeInTheDocument();
    unmount();

    renderPane();
    // Re-rendered from scratch: UK should already be expanded from storage,
    // CA should still be collapsed.
    expect(screen.getByText('UK | Sports')).toBeInTheDocument();
    expect(screen.queryByText('CA | Documentary')).not.toBeInTheDocument();
  });

  it('auto-expands categories while a search is active, without persisting that override', () => {
    const { rerender } = renderPane({ searchTerm: 'Documentary' });
    // Search narrows groupedStreams to the matching group only; its
    // category should be auto-visible with no click required.
    expect(screen.getByText('CA | Documentary')).toBeInTheDocument();

    // Clearing the search restores the default collapsed state -- the
    // auto-expand during search must not have written to localStorage.
    rerender(
      <StreamsPane
        streams={STREAMS}
        providers={PROVIDERS}
        streamGroups={STREAM_GROUPS}
        searchTerm=""
        onSearchChange={vi.fn()}
        providerFilter={null}
        onProviderFilterChange={vi.fn()}
        groupFilter={null}
        onGroupFilterChange={vi.fn()}
        loading={false}
        onGroupExpand={vi.fn()}
      />
    );
    expect(screen.queryByText('CA | Documentary')).not.toBeInTheDocument();
  });

  it('applies categorization to the already-filtered (group-filtered) visible set', () => {
    renderPane({ selectedStreamGroups: ['UK | Sports'], onSelectedStreamGroupsChange: vi.fn() });
    // Only the UK category should exist -- CA/US/Other groups are filtered
    // out upstream before categorization ever sees them.
    expect(screen.getByRole('button', { name: /^UK/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^CA/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^US/ })).not.toBeInTheDocument();
  });
});

describe('StreamsPane inventory count semantics', () => {
  it('labels the provider-wide total when no result search or group filter is active', () => {
    renderPane({
      streams: STREAMS.slice(0, 1),
      streamGroups: [{ name: 'CA | Documentary', count: 87 }],
    });
    expect(screen.getByLabelText('87 total streams')).toHaveTextContent('87');
  });

  it('uses the current server-search result set instead of the provider-wide total', () => {
    renderPane({
      searchTerm: 'documentary',
      streams: STREAMS.slice(0, 1),
      streamGroups: [{ name: 'CA | Documentary', count: 87 }],
    });
    expect(screen.getByLabelText('1 matching stream')).toHaveTextContent('1');
    expect(screen.queryByLabelText('87 total streams')).toBeNull();
  });

  it('sums only selected inventory groups and returns to total when cleared', () => {
    const { rerender } = renderPane({
      selectedStreamGroups: ['CA | Documentary', 'UK | Sports'],
      onSelectedStreamGroupsChange: vi.fn(),
      streamGroups: [
        { name: 'CA | Documentary', count: 12 },
        { name: 'UK | Sports', count: 8 },
        { name: 'US | News', count: 50 },
      ],
    });
    expect(screen.getByLabelText('20 filtered streams')).toHaveTextContent('20');

    rerender(
      <StreamsPane
        streams={STREAMS}
        providers={PROVIDERS}
        streamGroups={[
          { name: 'CA | Documentary', count: 12 },
          { name: 'UK | Sports', count: 8 },
          { name: 'US | News', count: 50 },
        ]}
        searchTerm=""
        onSearchChange={vi.fn()}
        providerFilter={null}
        onProviderFilterChange={vi.fn()}
        groupFilter={null}
        onGroupFilterChange={vi.fn()}
        loading={false}
        selectedStreamGroups={[]}
        onSelectedStreamGroupsChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('70 total streams')).toHaveTextContent('70');
  });
});

describe('StreamsPane stale streams (bead enhancedchannelmanager-po78p / GH #696)', () => {
  const STALE_STREAMS: Stream[] = [
    makeStream({ id: 101, name: 'Stale Stream', channel_group_name: 'UK | Sports', is_stale: true, last_seen: '2026-07-01T00:00:00Z' }),
    makeStream({ id: 102, name: 'Fresh Stream', channel_group_name: 'UK | Sports', is_stale: false }),
    makeStream({ id: 103, name: 'Healthy Group Stream', channel_group_name: 'US | News', is_stale: false }),
  ];
  const STALE_STREAM_GROUPS: StreamGroupInfo[] = [
    { name: 'UK | Sports', count: 2 },
    { name: 'US | News', count: 1 },
  ];

  function renderStalePane(overrides: Partial<React.ComponentProps<typeof StreamsPane>> = {}) {
    return renderPane({
      streams: STALE_STREAMS,
      streamGroups: STALE_STREAM_GROUPS,
      ...overrides,
    });
  }

  it('does not render a stale-count pill on a group header with no stale streams', async () => {
    const user = userEvent.setup();
    renderStalePane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const usHeader = screen.getByText('US | News').closest('.stream-group-header');
    expect(usHeader).not.toBeNull();
    expect((usHeader as HTMLElement).querySelector('.group-stale-count')).not.toBeInTheDocument();
  });

  it('renders a stale-count pill on a group header containing a stale stream', async () => {
    const user = userEvent.setup();
    renderStalePane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const ukHeader = screen.getByText('UK | Sports').closest('.stream-group-header');
    expect(ukHeader).not.toBeNull();
    const pill = within(ukHeader as HTMLElement).getByTitle(/1 stream.*no longer listed by provider \(stale\)/i);
    expect(pill).toHaveTextContent('1');
  });

  it('renders a STALE badge on a stale stream row but not on a fresh row', async () => {
    const user = userEvent.setup();
    renderStalePane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const staleRow = screen.getByText('Stale Stream').closest('.stream-item');
    const freshRow = screen.getByText('Fresh Stream').closest('.stream-item');
    expect(staleRow).not.toBeNull();
    expect(freshRow).not.toBeNull();
    expect(within(staleRow as HTMLElement).getByText('STALE')).toBeInTheDocument();
    expect(within(freshRow as HTMLElement).queryByText('STALE')).not.toBeInTheDocument();
  });

  it('applies the is-stale row class only to the stale stream row', async () => {
    const user = userEvent.setup();
    renderStalePane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const staleRow = screen.getByText('Stale Stream').closest('.stream-item');
    const freshRow = screen.getByText('Fresh Stream').closest('.stream-item');
    expect(staleRow).toHaveClass('is-stale');
    expect(freshRow).not.toHaveClass('is-stale');
  });

  it('includes the last-seen timestamp in the stale badge tooltip when available', async () => {
    const user = userEvent.setup();
    renderStalePane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const badge = screen.getByText('STALE');
    expect(badge.closest('.meta-tag')).toHaveAttribute('title', expect.stringContaining('2026-07-01T00:00:00Z'));
  });
});

describe('StreamsPane create-in menu replaces the right-click context menu (bead enhancedchannelmanager-zwhw4)', () => {
  const CHANNEL_GROUPS: ChannelGroup[] = [
    { id: 10, name: 'Entertainment', channel_count: 0 },
    { id: 11, name: 'Sports TV', channel_count: 0 },
    { id: 12, name: 'Disabled Group', channel_count: 0 },
  ];

  function renderEditPane(overrides: Partial<React.ComponentProps<typeof StreamsPane>> = {}) {
    return renderPane({
      isEditMode: true,
      onBulkCreateFromGroup: vi.fn(),
      channelGroups: CHANNEL_GROUPS,
      // Group 12 is deliberately NOT enabled — the menu must not offer it.
      selectedChannelGroups: [10, 11],
      ...overrides,
    });
  }

  async function expandAndSelect(user: ReturnType<typeof userEvent.setup>, streamNames: string[]) {
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));
    for (const name of streamNames) {
      await user.click(screen.getByRole('checkbox', { name: `Select stream ${name}` }));
    }
  }

  it('right-clicking a stream row spawns no custom context menu', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const row = screen.getByText('US News Stream 1').closest('.stream-item');
    expect(row).not.toBeNull();
    fireEvent.contextMenu(row!);

    expect(document.querySelector('.streams-context-menu')).toBeNull();
    expect(document.querySelector('.streams-context-submenu')).toBeNull();
  });

  it('right-clicking a group header spawns no custom context menu', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const header = screen.getByText('US | News').closest('.stream-group-header');
    expect(header).not.toBeNull();
    fireEvent.contextMenu(header!);

    expect(document.querySelector('.streams-context-menu')).toBeNull();
    expect(document.querySelector('.streams-context-submenu')).toBeNull();
  });

  it('renders each stream selector as a semantic checkbox exposing aria-checked state', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const selector = screen.getByRole('checkbox', { name: 'Select stream US News Stream 1' });
    expect(selector.tagName).toBe('BUTTON');
    expect(selector).not.toBeChecked();

    await user.click(selector);
    expect(selector).toBeChecked();

    await user.click(selector);
    expect(selector).not.toBeChecked();
  });

  it('supports the full keyboard-only single-stream flow: Tab to selector, Space selects, Create in… reachable and activatable', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    // Tab from the toolbar to the stream's selector — no pointer involved.
    const selector = screen.getByRole('checkbox', { name: 'Select stream US News Stream 1' });
    await tabUntil(user, () => document.activeElement === selector);
    expect(selector).not.toBeChecked();

    // Space toggles the selection and updates aria-checked.
    await user.keyboard(' ');
    expect(selector).toBeChecked();

    // The selection strip appears; Shift+Tab back up to the Create in…
    // trigger (it precedes the list in DOM order) and open it with Enter.
    const trigger = screen.getByRole('button', { name: 'Create in…' });
    await tabUntil(user, () => document.activeElement === trigger, { shift: true });
    await user.keyboard('{Enter}');

    // Panel auto-focuses the filter; ArrowDown walks Entertainment →
    // Sports TV → pinned "Create in new group…", Enter activates it.
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: /filter groups/i })).toHaveFocus()
    );
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}');
    expect(screen.getByRole('button', { name: 'Create in new group…' })).toHaveFocus();
    await user.keyboard('{Enter}');

    // The bulk-create modal opens preset to the new-group option — the
    // whole replacement flow completed without a single pointer event on
    // the selection surface.
    expect(
      screen.getByRole('heading', { name: /Create Channels from 1 Selected Stream/i })
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Create new group/i })).toBeChecked();
  });

  it('offers only the ENABLED channel groups in the Create in… chooser', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await expandAndSelect(user, ['US News Stream 1']);

    await user.click(screen.getByRole('button', { name: 'Create in…' }));

    expect(screen.getByRole('button', { name: 'Entertainment' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sports TV' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Disabled Group' })).not.toBeInTheDocument();
  });

  it('multi-stream Create in… <group> opens the bulk-create modal preset to that group', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await expandAndSelect(user, ['US News Stream 1', 'UK Sports Stream 1']);

    await user.click(screen.getByRole('button', { name: 'Create in…' }));
    await user.click(screen.getByRole('button', { name: 'Entertainment' }));

    expect(
      screen.getByRole('heading', { name: /Create Channels from 2 Selected Streams/i })
    ).toBeInTheDocument();
    // The Channel Group section's collapsed summary shows the preset target
    // group — proof the chosen group id was wired through (groupOption
    // 'existing' + selectedGroupId 10).
    expect(screen.getByText('"Entertainment"')).toBeInTheDocument();
  });

  it('Create in new group… opens the bulk-create modal with the new-group option expanded and selected', async () => {
    const user = userEvent.setup();
    renderEditPane();
    await expandAndSelect(user, ['US News Stream 1', 'UK Sports Stream 1']);

    await user.click(screen.getByRole('button', { name: 'Create in…' }));
    await user.click(screen.getByRole('button', { name: 'Create in new group…' }));

    expect(
      screen.getByRole('heading', { name: /Create Channels from 2 Selected Streams/i })
    ).toBeInTheDocument();
    // groupOption 'new' + channelGroupExpanded: the radio is pre-selected
    // and the new-group name input is immediately visible.
    expect(screen.getByRole('radio', { name: /Create new group/i })).toBeChecked();
    expect(screen.getByPlaceholderText('New group name')).toBeInTheDocument();
  });

  describe('single-stream dedup intercept (BD-I, ADR-008 §D1)', () => {
    beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
    afterEach(() => server.resetHandlers());
    afterAll(() => server.close());

    it('routes a single-stream Create in… <group> through the dedup candidates lookup before the modal', async () => {
      const candidatesSpy = vi.fn();
      server.use(
        http.get('/api/channel-merges/candidates', ({ request }) => {
          const url = new URL(request.url);
          candidatesSpy(url.searchParams.get('stream_name'), url.searchParams.get('group_id'));
          return HttpResponse.json({
            stream_name: 'US News Stream 1',
            candidates: [],
            total: 0,
            page: 1,
            page_size: 1,
            total_pages: 0,
          });
        })
      );
      const user = userEvent.setup();
      renderEditPane();
      await expandAndSelect(user, ['US News Stream 1']);

      await user.click(screen.getByRole('button', { name: 'Create in…' }));
      await user.click(screen.getByRole('button', { name: 'Sports TV' }));

      await waitFor(() => expect(candidatesSpy).toHaveBeenCalledTimes(1));
      expect(candidatesSpy).toHaveBeenCalledWith('US News Stream 1', '11');
      // Empty candidate list falls through to the bulk-create modal.
      expect(
        await screen.findByRole('heading', { name: /Create Channels from 1 Selected Stream/i })
      ).toBeInTheDocument();
    });
  });
});

describe('StreamsPane group select-all tri-state (round-2 review of bead enhancedchannelmanager-s8xpd)', () => {
  // The zwhw4 pass gave the row-level selector role="checkbox" +
  // aria-checked but left the group-header select-all on a boolean
  // aria-pressed, which announces "none selected" and "some selected"
  // identically even though the glyph already shows an indeterminate
  // state. This block proves the tri-state fix: role="checkbox" +
  // aria-checked={true|false|'mixed'}, plus the same nesting-conflict fix
  // ChannelsPane got -- the select-all button is a sibling of the
  // expand/collapse toggle, not nested inside it, so it's reachable in the
  // real tab order on its own.
  const GROUP_SELECT_STREAMS: Stream[] = [
    makeStream({ id: 501, name: 'US News Stream A', channel_group_name: 'US | News' }),
    makeStream({ id: 502, name: 'US News Stream B', channel_group_name: 'US | News' }),
  ];
  const GROUP_SELECT_STREAM_GROUPS: StreamGroupInfo[] = [{ name: 'US | News', count: 2 }];

  function renderGroupSelectPane(overrides: Partial<React.ComponentProps<typeof StreamsPane>> = {}) {
    return renderPane({
      streams: GROUP_SELECT_STREAMS,
      streamGroups: GROUP_SELECT_STREAM_GROUPS,
      isEditMode: true,
      onBulkCreateFromGroup: vi.fn(),
      ...overrides,
    });
  }

  it('renders the group select-all as aria-checked="mixed" when only some streams in the group are selected', async () => {
    const user = userEvent.setup();
    renderGroupSelectPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const groupSelector = screen.getByRole('checkbox', { name: 'Select all streams in group' });
    expect(groupSelector).toHaveAttribute('aria-checked', 'false');

    await user.click(screen.getByRole('checkbox', { name: 'Select stream US News Stream A' }));

    expect(groupSelector).toHaveAttribute('aria-checked', 'mixed');
    expect(groupSelector).toHaveClass('group-selection-checkbox');
  });

  it('reaches and activates the group select-all via Tab + Space, zero pointer events', async () => {
    const user = userEvent.setup();
    renderGroupSelectPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const groupSelector = screen.getByRole('checkbox', { name: 'Select all streams in group' });
    expect(groupSelector).toHaveAttribute('aria-checked', 'false');

    // Tab from the toolbar to the group selector -- no pointer involved.
    // Proves the button is in the real tab order as a sibling of the
    // expand/collapse toggle, not nested inside it.
    await tabUntil(user, () => document.activeElement === groupSelector);

    await user.keyboard(' ');
    expect(screen.getByRole('checkbox', { name: 'Deselect all streams in group' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });
});

describe('StreamsPane catch-up badge (bead enhancedchannelmanager-sy1sz)', () => {
  const CATCHUP_STREAMS: Stream[] = [
    makeStream({ id: 201, name: 'Catchup Stream', channel_group_name: 'UK | Sports', is_catchup: true, catchup_days: 7 }),
    makeStream({ id: 202, name: 'Plain Stream', channel_group_name: 'UK | Sports', is_catchup: false, catchup_days: 5 }),
  ];
  const CATCHUP_STREAM_GROUPS: StreamGroupInfo[] = [
    { name: 'UK | Sports', count: 2 },
  ];

  function renderCatchupPane(overrides: Partial<React.ComponentProps<typeof StreamsPane>> = {}) {
    return renderPane({
      streams: CATCHUP_STREAMS,
      streamGroups: CATCHUP_STREAM_GROUPS,
      ...overrides,
    });
  }

  it('renders the catch-up badge on a supported stream row but not on an unsupported one', async () => {
    const user = userEvent.setup();
    renderCatchupPane();
    await user.click(screen.getByRole('button', { name: /Expand all groups/i }));

    const catchupRow = screen.getByText('Catchup Stream').closest('.stream-item');
    const plainRow = screen.getByText('Plain Stream').closest('.stream-item');
    expect(catchupRow).not.toBeNull();
    expect(plainRow).not.toBeNull();

    const badge = (catchupRow as HTMLElement).querySelector('.catchup-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute('title', 'Catch-up: 7 days');
    // Flag is authoritative — is_catchup:false wins even with catchup_days:5.
    expect((plainRow as HTMLElement).querySelector('.catchup-badge')).not.toBeInTheDocument();
  });
});
