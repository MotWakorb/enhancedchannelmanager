import type { ChannelProfile } from '../types';
import * as api from './api';
import { HttpError } from './httpClient';
import { applyProfileMembership } from './profileMembership';

export interface CreatedChannelProfileIntent {
  channelId: number;
  profileIds: number[];
}

export interface CreatedChannelProfileOutcome {
  remaining: CreatedChannelProfileIntent[];
  profiles: ChannelProfile[] | null;
  verified: boolean;
  missingProfiles?: Record<number, number[]>;
  scopeUnavailable?: boolean;
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
  binding: string | undefined,
  isCurrent: () => boolean = () => true,
): Promise<CreatedChannelProfileOutcome> {
  const unknown = { remaining: intents, profiles: null, verified: false };
  if (!binding) return { ...unknown, scopeUnavailable: true };
  try {
    if (!isCurrent()) return unknown;
    const before = await api.getChannelProfiles(binding);
    if (!isCurrent()) return unknown;
    if (!validProfiles(before)) return unknown;
    const known = new Set(before.map(profile => profile.id));
    const eligible = intents.filter(intent => intent.profileIds.every(id => known.has(id)));

    // Reuse the serial 100-item write/readback and bounded legacy fallback.
    for (const profile of eligible.length > 0 ? before : []) {
      if (!isCurrent()) return unknown;
      await applyProfileMembership(profile.id, new Map(
        eligible.map(intent => [intent.channelId, intent.profileIds.includes(profile.id)]),
      ), isCurrent, binding);
    }

    if (!isCurrent()) return unknown;
    const after = eligible.length > 0 ? await api.getChannelProfiles(binding) : before;
    if (!isCurrent()) return unknown;
    if (!validProfiles(after)) return unknown;
    const enabled = new Map(after.map(profile => [profile.id, new Set(profile.channels)]));
    const missingProfiles: Record<number, number[]> = {};
    for (const intent of intents) {
      const missing = intent.profileIds.filter(id => !known.has(id) || !enabled.has(id));
      if (missing.length) missingProfiles[intent.channelId] = missing;
    }
    const remaining = intents.filter(intent =>
      missingProfiles[intent.channelId] ||
      after.some(profile => enabled.get(profile.id)!.has(intent.channelId) !== intent.profileIds.includes(profile.id)),
    );
    return { remaining, profiles: after, verified: true, missingProfiles };
  } catch (error) {
    // Read failure is unknown visibility, not proof of either success or loss.
    if (error instanceof HttpError && error.status === 409) return { ...unknown, scopeUnavailable: true };
    return unknown;
  }
}
