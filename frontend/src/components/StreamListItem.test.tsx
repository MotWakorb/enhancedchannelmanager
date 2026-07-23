/**
 * Unit tests for StreamListItem component.
 *
 * Focus: bead enhancedchannelmanager-po78p / GH #696 — stale-stream STALE
 * badge in the expanded channel stream list.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DndContext } from '@dnd-kit/core';
import { SortableContext } from '@dnd-kit/sortable';
import { StreamListItem } from './StreamListItem';
import type { Stream } from '../types';

function renderItem(overrides: Partial<React.ComponentProps<typeof StreamListItem>> = {}) {
  const stream: Stream = {
    id: 10,
    name: 'ESPN Feed',
    url: 'http://example.com/stream.m3u8',
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: null,
    is_custom: false,
  };

  const props: React.ComponentProps<typeof StreamListItem> = {
    stream,
    providerName: null,
    isEditMode: false,
    onRemove: vi.fn(),
    ...overrides,
  };

  return render(
    <DndContext>
      <SortableContext items={[10]}>
        <StreamListItem {...props} />
      </SortableContext>
    </DndContext>
  );
}

describe('StreamListItem — stale-stream badge (bead enhancedchannelmanager-po78p / GH #696)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render the STALE badge when isStale is false/omitted', () => {
    renderItem();
    expect(screen.queryByText('STALE')).not.toBeInTheDocument();
  });

  it('renders the STALE badge when isStale is true', () => {
    renderItem({ isStale: true });
    const badge = screen.getByText('STALE');
    expect(badge.closest('.meta-tag')).toHaveClass('stream-stale');
  });

  it('shows a generic tooltip when the stream has no last_seen value', () => {
    renderItem({ isStale: true });
    const badge = screen.getByText('STALE').closest('.meta-tag');
    expect(badge).toHaveAttribute('title', 'No longer listed by provider (stale)');
  });

  it('includes the last-seen timestamp in the tooltip when available on the stream', () => {
    renderItem({
      isStale: true,
      stream: {
        id: 10,
        name: 'ESPN Feed',
        url: 'http://example.com/stream.m3u8',
        m3u_account: 1,
        logo_url: null,
        tvg_id: null,
        channel_group: null,
        channel_group_name: null,
        is_custom: false,
        is_stale: true,
        last_seen: '2026-06-15T12:00:00Z',
      },
    });
    const badge = screen.getByText('STALE').closest('.meta-tag');
    expect(badge).toHaveAttribute('title', 'No longer listed by provider — last seen 2026-06-15T12:00:00Z');
  });

  it('renders the STALE badge independent of streamStats (probe-failed and stale are distinct signals)', () => {
    renderItem({
      isStale: true,
      streamStats: {
        stream_id: 10,
        stream_name: 'ESPN Feed',
        resolution: null,
        fps: null,
        video_codec: null,
        audio_codec: null,
        audio_channels: null,
        stream_type: null,
        bitrate: null,
        video_bitrate: null,
        probe_status: 'failed',
        error_message: 'connection refused',
        last_probed: null,
        created_at: '2026-06-01T00:00:00Z',
        consecutive_failures: 0,
        is_black_screen: false,
        is_low_fps: false,
      },
    });
    expect(screen.getByText('STALE')).toBeInTheDocument();
    expect(screen.getByTitle('connection refused')).toBeInTheDocument();
  });
});

describe('StreamListItem — catch-up badge (bead enhancedchannelmanager-sy1sz)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the catch-up badge when the stream is_catchup is true', () => {
    const { container } = renderItem({
      stream: {
        id: 10,
        name: 'ESPN Feed',
        url: 'http://example.com/stream.m3u8',
        m3u_account: 1,
        logo_url: null,
        tvg_id: null,
        channel_group: null,
        channel_group_name: null,
        is_custom: false,
        is_catchup: true,
        catchup_days: 7,
      },
    });
    const badge = container.querySelector('.catchup-badge');
    expect(badge).toBeInTheDocument();
    expect(badge?.getAttribute('title')).toBe('Catch-up: 7 days');
  });

  it('does not show the catch-up badge when the stream does not support catch-up', () => {
    const { container } = renderItem({
      stream: {
        id: 10,
        name: 'ESPN Feed',
        url: 'http://example.com/stream.m3u8',
        m3u_account: 1,
        logo_url: null,
        tvg_id: null,
        channel_group: null,
        channel_group_name: null,
        is_custom: false,
        is_catchup: false,
        catchup_days: 5,
      },
    });
    expect(container.querySelector('.catchup-badge')).not.toBeInTheDocument();
  });

  it('does not show the catch-up badge when the fields are absent', () => {
    const { container } = renderItem();
    expect(container.querySelector('.catchup-badge')).not.toBeInTheDocument();
  });
});
