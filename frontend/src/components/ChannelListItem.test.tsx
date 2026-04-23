/**
 * Unit tests for ChannelListItem component.
 *
 * Focus: bd-eio04.13 — per-channel would-normalize indicator.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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
