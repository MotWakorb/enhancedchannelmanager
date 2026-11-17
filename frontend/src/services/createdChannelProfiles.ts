import type { ChannelProfile } from '../types';
import * as api from './api';
import { applyProfileMembership } from './profileMembership';

export interface CreatedChannelProfileIntent {
  channelId: number;
  profileIds: number[];
}

function validProfiles(profiles: ChannelProfile[]): boolean {
  return Array.isArray(profiles) &&
    new Set(profiles.map(profile => profile.id)).size === profiles.length &&
    profiles.every(profile => Number.isSafeInteger(profile.id) && profile.id > 0 &&
      Array.isArray(profile.channels) &&
      profile.channels.every(id => Number.isSafeInteger(id) && id > 0));
}

/** Reconcile explicit visibility for committed IDs, never recreate a channel.
 * A final complete read checks earlier successes too, not just failed requests.
 */
export async function applyCreatedChannelProfiles(
  intents: CreatedChannelProfileIntent[],
  isCurrent: () => boolean = () => true,
) {
  const unknown = { remaining: intents, profiles: null as ChannelProfile[] | null, verified: false };
  try {
    if (!isCurrent()) return unknown;
    const before = await api.getChannelProfiles();
    if (!isCurrent()) return unknown;
    if (!validProfiles(before)) return unknown;
    const known = new Set(before.map(profile => profile.id));
    if (intents.some(intent => intent.profileIds.some(id => !known.has(id)))) return unknown;

    // Reuse the serial 100-item write/readback and bounded legacy fallback.
    for (const profile of before) {
      if (!isCurrent()) return unknown;
      await applyProfileMembership(profile.id, new Map(
        intents.map(intent => [intent.channelId, intent.profileIds.includes(profile.id)]),
      ), isCurrent);
    }

    if (!isCurrent()) return unknown;
    const after = await api.getChannelProfiles();
    if (!isCurrent()) return unknown;
    if (!validProfiles(after)) return unknown;
    const enabled = new Map(after.map(profile => [profile.id, new Set(profile.channels)]));
    const remaining = intents.filter(intent =>
      intent.profileIds.some(id => !enabled.has(id)) ||
      after.some(profile => enabled.get(profile.id)!.has(intent.channelId) !== intent.profileIds.includes(profile.id)),
    );
    return { remaining, profiles: after, verified: true };
  } catch {
    // Read failure is unknown visibility, not proof of either success or loss.
    return unknown;
  }
}
