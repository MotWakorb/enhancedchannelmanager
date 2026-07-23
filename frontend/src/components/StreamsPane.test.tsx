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
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamsPane } from './StreamsPane';
import type { Stream, StreamGroupInfo, M3UAccount } from '../types';

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
