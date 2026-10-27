import type { ChannelProfile } from '../types';
import * as api from './api';

// Serial 100-item slices follow useNormalizePreview's bounded-batch pattern.
const PROFILE_MEMBERSHIP_BATCH_SIZE = 100;

/** Best-effort explicit intents, not a whole-profile replacement or global CAS.
 * Readback confirms visibility only: an absent enabled ID does not identify
 * whether a disabled membership row exists. Covered by the modal HTTP tests.
 */
export async function applyProfileMembership(profileId: number, intents: ReadonlyMap<number, boolean>) {
  const requested = new Map(intents);
  const remaining = new Map(requested);
  let profile: ChannelProfile | null = null;
  let verified = false;
  let failedRequests = 0;

  const readback = async () => {
    verified = false;
    // Reconcile the complete click's intent on EVERY read, including IDs an
    // earlier read satisfied. Another actor can change those during fallback.
    // If this read fails, none of the original intents is terminally verified.
    remaining.clear();
    for (const [id, enabled] of requested) remaining.set(id, enabled);
    try {
      const updated = await api.getChannelProfile(profileId, { fresh: true });
      if (updated?.id !== profileId || !Array.isArray(updated.channels) ||
          !updated.channels.every(id => Number.isSafeInteger(id) && id > 0)) {
        return false;
      }
      profile = updated;
      verified = true;
      const enabledIds = new Set(updated.channels);
      for (const [id, enabled] of remaining) {
        if (enabledIds.has(id) === enabled) remaining.delete(id);
      }
      return true;
    } catch {
      return false;
    }
  };
  const result = () => ({ profile, remaining, verified, failedRequests });

  // A retry first reads, to avoid repeating a write whose reply was lost.
  if (!await readback() || remaining.size === 0) return result();

  const acceptedIds = new Set<number>();
  for (const enabled of [true, false]) {
    const ids = [...remaining].filter(([, on]) => on === enabled).map(([id]) => id);
    for (let i = 0; i < ids.length; i += PROFILE_MEMBERSHIP_BATCH_SIZE) {
      const batch = ids.slice(i, i + PROFILE_MEMBERSHIP_BATCH_SIZE);
      try {
        await api.bulkUpdateProfileChannels(profileId, batch, enabled);
        batch.forEach(id => acceptedIds.add(id));
      } catch {
        failedRequests++;
      }
    }
  }

  // All requests have settled before reading, including lost/rejected replies.
  if (!await readback()) return result();

  // Older upstreams may accept bulk but only update existing rows. Fall back
  // once, serially, solely for accepted-but-unapplied explicit intents.
  // Failed bulk requests await operator retry instead of an automatic storm.
  const fallback = [...remaining].filter(([id]) => acceptedIds.has(id));
  for (const [id, enabled] of fallback) {
    try {
      await api.updateProfileChannel(profileId, id, { enabled });
    } catch {
      failedRequests++;
    }
  }
  if (fallback.length > 0) await readback();
  return result();
}
