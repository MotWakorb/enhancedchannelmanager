import type { Channel, ChannelSnapshot } from '../types';

/**
 * Helper to create a snapshot from a channel.
 * Used by edit mode and change history to capture channel state.
 */
export function createSnapshot(channel: Channel): ChannelSnapshot {
  return {
    id: channel.id,
    channel_number: channel.channel_number,
    name: channel.name,
    channel_group_id: channel.channel_group_id,
    streams: [...channel.streams],
  };
}
