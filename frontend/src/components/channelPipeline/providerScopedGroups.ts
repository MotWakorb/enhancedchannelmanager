/**
 * Helpers for the provider-scoped Event Sync group picker (bead 38dzi / P4).
 * Kept out of the component file so Fast Refresh stays happy (a component file
 * should only export components).
 */
import type { EventSyncGroupScope } from '../../types/eventSync';
import type { ProviderGroupScopeRow } from '../../services/api';

/** One (provider, group) junction, joined to its channel-group name. */
export interface GroupProviderRow {
  groupId: number;
  groupName: string;
  m3uAccountId: number;
  m3uAccountName: string;
  autoChannelSync: boolean;
  enabled: boolean;
  streamCount: number | null;
}

/** Join raw junction rows to channel-group names (the endpoint omits the name;
 *  the caller already has the group list). Rows for unknown groups are
 *  dropped. */
export function joinProviderRows(
  rows: ProviderGroupScopeRow[],
  groupName: (id: number) => string | undefined,
): GroupProviderRow[] {
  const out: GroupProviderRow[] = [];
  for (const r of rows) {
    const name = groupName(r.channel_group_id);
    if (!name) continue;
    out.push({
      groupId: r.channel_group_id,
      groupName: name,
      m3uAccountId: r.m3u_account_id,
      m3uAccountName: r.m3u_account_name,
      autoChannelSync: r.auto_channel_sync,
      enabled: r.enabled,
      streamCount: r.stream_count,
    });
  }
  return out;
}

/** Stable identity for a scope, for selection/dedup/comparison. */
export function scopeKey(s: EventSyncGroupScope): string {
  return `${s.group_id}:${s.m3u_account_id ?? 'null'}`;
}
