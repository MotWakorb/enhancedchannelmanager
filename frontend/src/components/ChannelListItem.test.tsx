/**
 * Unit tests for ChannelListItem component.
 *
 * Focus: bd-eio04.13 — per-channel would-normalize indicator.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DndContext } from '@dnd-kit/core';
import { SortableContext } from '@dnd-kit/sortable';
import { ChannelListItem } from './ChannelListItem';
import type { Channel } from '../types';

function renderRow(overrides: Partial<React.ComponentProps<typeof ChannelListItem>> = {}) {
  const channel: Channel = {
    id: 42,
    channel_number: 7,
    name: 'ESPN HD',
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: 'u-42',
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };

  const props: React.ComponentProps<typeof ChannelListItem> = {
    channel,
    isSelected: false,
    isMultiSelected: false,
    isExpanded: false,
    isDragOver: false,
    isEditingNumber: false,
    isEditingName: false,
    isModified: false,
    isEditMode: false,
    editingNumber: '',
    editingName: '',
    logoUrl: null,
    multiSelectCount: 0,
    onEditingNumberChange: vi.fn(),
    onEditingNameChange: vi.fn(),
    onStartEditNumber: vi.fn(),
    onStartEditName: vi.fn(),
    onSaveNumber: vi.fn(),
    onSaveName: vi.fn(),
    onCancelEditNumber: vi.fn(),
    onCancelEditName: vi.fn(),
    onClick: vi.fn(),
    onToggleExpand: vi.fn(),
    onToggleSelect: vi.fn(),
    onStreamDragOver: vi.fn(),
    onStreamDragLeave: vi.fn(),
    onStreamDrop: vi.fn(),
    onDelete: vi.fn(),
    onEditChannel: vi.fn(),
    ...overrides,
  };

  return render(
    <DndContext>
      <SortableContext items={[42]}>
        <ChannelListItem {...props} />
      </SortableContext>
    </DndContext>
  );
}

describe('ChannelListItem — would-normalize indicator (bd-eio04.13)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render the indicator when no proposed name is provided', () => {
    renderRow();
    expect(screen.queryByTestId('channel-normalize-indicator-42')).not.toBeInTheDocument();
  });

  it('renders the indicator when proposedNormalizedName is supplied', () => {
    renderRow({ proposedNormalizedName: 'ESPN' });
    const btn = screen.getByTestId('channel-normalize-indicator-42');
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveAttribute(
      'aria-label',
      'Channel name would normalize to "ESPN". Click to preview.'
    );
    expect(btn).toHaveAttribute(
      'title',
      'This name would be normalized to "ESPN". Click to preview.'
    );
  });

  it('calls onShowNormalizePreview when the indicator is clicked', () => {
    const onShow = vi.fn();
    const onClick = vi.fn();
    renderRow({
      proposedNormalizedName: 'ESPN',
      onShowNormalizePreview: onShow,
      onClick,
    });
    fireEvent.click(screen.getByTestId('channel-normalize-indicator-42'));
    expect(onShow).toHaveBeenCalledTimes(1);
    // Row-level click must not fire when the indicator is clicked — the
    // button stops propagation so the row isn't selected/expanded.
    expect(onClick).not.toHaveBeenCalled();
  });

  it('hides the indicator while the name is being edited inline', () => {
    renderRow({
      proposedNormalizedName: 'ESPN',
      isEditingName: true,
      isEditMode: true,
      editingName: 'ESPN HD',
    });
    expect(screen.queryByTestId('channel-normalize-indicator-42')).not.toBeInTheDocument();
  });

  it('renders the Material icon name auto_fix_high', () => {
    renderRow({ proposedNormalizedName: 'ESPN' });
    const btn = screen.getByTestId('channel-normalize-indicator-42');
    expect(btn.querySelector('.material-icons')).toHaveTextContent('auto_fix_high');
  });
});

describe('ChannelListItem — applied TVG ID / name subtitle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render the subtitle when no TVG info is provided', () => {
    renderRow();
    expect(screen.queryByTestId('channel-tvg-info-42')).not.toBeInTheDocument();
  });

  it('renders TVG ID and TVG name joined by a separator', () => {
    renderRow({ tvgId: 'espn.us', tvgName: 'ESPN' });
    const subtitle = screen.getByTestId('channel-tvg-info-42');
    expect(subtitle).toHaveTextContent('espn.us · ESPN');
    expect(subtitle).toHaveAttribute('title', 'TVG ID: espn.us · TVG Name: ESPN');
  });

  it('renders TVG ID alone when no EPG name is linked', () => {
    renderRow({ tvgId: 'espn.us' });
    expect(screen.getByTestId('channel-tvg-info-42')).toHaveTextContent(/^espn\.us$/);
  });

  it('renders TVG name alone when only the EPG link provides it', () => {
    renderRow({ tvgName: 'ESPN' });
    expect(screen.getByTestId('channel-tvg-info-42')).toHaveTextContent(/^ESPN$/);
  });

  it('hides the subtitle while the name is being edited inline', () => {
    renderRow({
      tvgId: 'espn.us',
      tvgName: 'ESPN',
      isEditingName: true,
      isEditMode: true,
      editingName: 'ESPN HD',
    });
    expect(screen.queryByTestId('channel-tvg-info-42')).not.toBeInTheDocument();
  });

  it('renders the EPG source name first, before TVG ID and TVG name', () => {
    renderRow({ tvgId: 'espn.us', tvgName: 'ESPN', epgSourceName: 'Gracenote' });
    const subtitle = screen.getByTestId('channel-tvg-info-42');
    expect(subtitle).toHaveTextContent('Gracenote · espn.us · ESPN');
    expect(subtitle).toHaveAttribute(
      'title',
      'EPG: Gracenote · TVG ID: espn.us · TVG Name: ESPN'
    );
  });

  it('omits the EPG source name when it does not resolve, keeping the two-part rendering', () => {
    renderRow({ tvgId: 'espn.us', tvgName: 'ESPN', epgSourceName: null });
    const subtitle = screen.getByTestId('channel-tvg-info-42');
    expect(subtitle).toHaveTextContent('espn.us · ESPN');
    expect(subtitle).toHaveAttribute('title', 'TVG ID: espn.us · TVG Name: ESPN');
  });
});

describe('ChannelListItem — resolution capability pills', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render the pills container when no capabilities are provided', () => {
    renderRow();
    expect(screen.queryByTestId('channel-capabilities-42')).not.toBeInTheDocument();
  });

  it('does not render the pills container for an empty capabilities array (unprobed channel)', () => {
    renderRow({ capabilities: [] });
    expect(screen.queryByTestId('channel-capabilities-42')).not.toBeInTheDocument();
  });

  it('renders one pill per distinct tier, in the highest-first order supplied', () => {
    renderRow({ capabilities: ['4K', 'FHD', 'HD'] });
    const container = screen.getByTestId('channel-capabilities-42');
    const pills = container.querySelectorAll('.capability-pill');
    expect(pills).toHaveLength(3);
    expect(Array.from(pills).map((p) => p.textContent)).toEqual(['4K', 'FHD', 'HD']);
  });

  it('applies the tier-specific class to each pill', () => {
    renderRow({ capabilities: ['4K', 'FHD', 'HD', 'SD'] });
    const container = screen.getByTestId('channel-capabilities-42');
    expect(container.querySelector('.cap-4k')).toHaveTextContent('4K');
    expect(container.querySelector('.cap-fhd')).toHaveTextContent('FHD');
    expect(container.querySelector('.cap-hd')).toHaveTextContent('HD');
    expect(container.querySelector('.cap-sd')).toHaveTextContent('SD');
  });

  it('renders a lone SD pill for a sub-720 channel', () => {
    renderRow({ capabilities: ['SD'] });
    const pills = screen.getByTestId('channel-capabilities-42').querySelectorAll('.capability-pill');
    expect(pills).toHaveLength(1);
    expect(pills[0]).toHaveTextContent('SD');
    expect(pills[0]).toHaveClass('cap-sd');
  });

  it('renders pills even while the name is being edited inline (independent of the TVG subtitle)', () => {
    renderRow({
      capabilities: ['HD'],
      isEditingName: true,
      isEditMode: true,
      editingName: 'ESPN HD',
    });
    // Subtitle hides during editing, but capability pills are not name-gated.
    expect(screen.queryByTestId('channel-tvg-info-42')).not.toBeInTheDocument();
    expect(screen.getByTestId('channel-capabilities-42')).toBeInTheDocument();
  });
});

describe('ChannelListItem — stale-stream indicator (bead enhancedchannelmanager-po78p / GH #696)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderWithStreams(overrides: Partial<React.ComponentProps<typeof ChannelListItem>> = {}) {
    return renderRow({
      channel: {
        id: 42,
        channel_number: 7,
        name: 'ESPN HD',
        channel_group_id: null,
        tvg_id: null,
        tvc_guide_stationid: null,
        epg_data_id: null,
        streams: [1, 2],
        stream_profile_id: null,
        uuid: 'u-42',
        logo_id: null,
        auto_created: false,
        auto_created_by: null,
        auto_created_by_name: null,
      },
      ...overrides,
    });
  }

  it('does not render the stale row class or icon when hasStaleStreams is false', () => {
    renderWithStreams();
    expect(document.querySelector('.channel-item')).not.toHaveClass('has-stale-streams');
    expect(document.querySelector('.stale-stream-icon')).not.toBeInTheDocument();
  });

  it('adds has-stale-streams to the row and renders the stale icon when hasStaleStreams is true', () => {
    renderWithStreams({ hasStaleStreams: true, staleStreamCount: 2 });
    expect(document.querySelector('.channel-item')).toHaveClass('has-stale-streams');
    expect(document.querySelector('.stale-stream-icon')).toBeInTheDocument();
  });

  it('applies the has-stale class to the streams-count area, with a count-specific tooltip', () => {
    renderWithStreams({ hasStaleStreams: true, staleStreamCount: 2 });
    const countEl = document.querySelector('.channel-streams-count');
    expect(countEl).toHaveClass('has-stale');
    const icon = document.querySelector('.stale-stream-icon');
    expect(icon).toHaveAttribute('title', '2 streams no longer listed by provider (stale)');
  });

  it('takes precedence over black-screen/low-fps but defers to has-failed', () => {
    // Stale + black-screen: stale wins (comes first in precedence order).
    renderWithStreams({ hasStaleStreams: true, hasBlackScreenStreams: true, staleStreamCount: 1 });
    const countEl = document.querySelector('.channel-streams-count');
    expect(countEl).toHaveClass('has-stale');
    expect(countEl).not.toHaveClass('has-black-screen');
    expect(document.querySelector('.stale-stream-icon')).toBeInTheDocument();
    expect(document.querySelector('.black-screen-icon')).not.toBeInTheDocument();
  });

  it('yields to has-failed when both a failed and a stale stream are present', () => {
    renderWithStreams({ hasStaleStreams: true, hasFailedStreams: true, staleStreamCount: 1 });
    const countEl = document.querySelector('.channel-streams-count');
    expect(countEl).toHaveClass('has-failed');
    expect(countEl).not.toHaveClass('has-stale');
    expect(document.querySelector('.failed-stream-icon')).toBeInTheDocument();
    expect(document.querySelector('.stale-stream-icon')).not.toBeInTheDocument();
  });

  it('does not show the neutral streams icon when hasStaleStreams is true', () => {
    renderWithStreams({ hasStaleStreams: true, staleStreamCount: 1 });
    expect(document.querySelector('.streams-count-icon')).not.toBeInTheDocument();
  });
});

describe('ChannelListItem — keyboard-operable row selector (bead enhancedchannelmanager-s8xpd)', () => {
  // Follow-on from bead zwhw4's StreamsPane review: the per-channel
  // selection indicator was a bare clickable <span> with no native control
  // semantics -- not focusable, no aria-checked, no keyboard handler. Mirrors
  // the shipped StreamsPane pattern: a real <button role="checkbox"> whose
  // aria-checked reflects `isMultiSelected`.
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render the selector outside edit mode', () => {
    renderRow({ isEditMode: false });
    expect(screen.queryByRole('checkbox', { name: 'Select channel ESPN HD' })).not.toBeInTheDocument();
  });

  it('renders the selector as a semantic checkbox exposing aria-checked state', () => {
    renderRow({ isEditMode: true, isMultiSelected: false });
    const selector = screen.getByRole('checkbox', { name: 'Select channel ESPN HD' });
    expect(selector.tagName).toBe('BUTTON');
    expect(selector).not.toBeChecked();
  });

  it('reflects isMultiSelected=true as aria-checked="true" and the checked glyph', () => {
    renderRow({ isEditMode: true, isMultiSelected: true });
    const selector = screen.getByRole('checkbox', { name: 'Select channel ESPN HD' });
    expect(selector).toBeChecked();
    expect(selector.querySelector('.material-icons')).toHaveTextContent('check_box');
  });

  it('is keyboard-focusable and activates onToggleSelect on Space, with zero pointer events', async () => {
    const user = userEvent.setup();
    const onToggleSelect = vi.fn();
    const onClick = vi.fn();
    renderRow({ isEditMode: true, isMultiSelected: false, onToggleSelect, onClick });

    const selector = screen.getByRole('checkbox', { name: 'Select channel ESPN HD' });
    await user.tab();
    expect(selector).toHaveFocus();

    await user.keyboard(' ');
    expect(onToggleSelect).toHaveBeenCalledTimes(1);
    // Row-level click must not fire — the button stops propagation so the
    // row isn't also selected/expanded by the same keypress.
    expect(onClick).not.toHaveBeenCalled();
  });

  it('keeps the glyph icon aria-hidden so screen readers announce only the button role/state', () => {
    renderRow({ isEditMode: true, isMultiSelected: false });
    const selector = screen.getByRole('checkbox', { name: 'Select channel ESPN HD' });
    expect(selector.querySelector('.material-icons')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('ChannelListItem — catch-up badge (bead enhancedchannelmanager-sy1sz)', () => {
  const baseChannel: Channel = {
    id: 42,
    channel_number: 7,
    name: 'ESPN HD',
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: 'u-42',
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the catch-up badge when the channel is_catchup is true', () => {
    renderRow({ channel: { ...baseChannel, is_catchup: true, catchup_days: 7 } });
    const badge = document.querySelector('.catchup-badge');
    expect(badge).toBeInTheDocument();
    expect(badge?.getAttribute('title')).toBe('Catch-up: 7 days');
  });

  it('does not show the catch-up badge when the channel does not support catch-up', () => {
    renderRow({ channel: { ...baseChannel, is_catchup: false, catchup_days: 5 } });
    expect(document.querySelector('.catchup-badge')).not.toBeInTheDocument();
  });

  it('does not show the catch-up badge when the fields are absent', () => {
    renderRow({ channel: { ...baseChannel } });
    expect(document.querySelector('.catchup-badge')).not.toBeInTheDocument();
  });
});
