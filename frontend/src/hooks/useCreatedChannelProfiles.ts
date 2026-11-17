import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChannelProfile } from '../types';
import { applyCreatedChannelProfiles, type CreatedChannelProfileIntent } from '../services/createdChannelProfiles';

const STORAGE_PREFIX = 'ecm.created-channel-profiles.v1:';
const validId = (id: unknown): id is number => Number.isSafeInteger(id) && (id as number) > 0;

function restore(key: string): CreatedChannelProfileIntent[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? '[]');
    if (!Array.isArray(value) || !value.every(item =>
      item && validId(item.channelId) && Array.isArray(item.profileIds) && item.profileIds.every(validId),
    )) return [];
    return value;
  } catch {
    return [];
  }
}

/** Own only post-commit membership intent; the staged ledger still owns creates. */
export function useCreatedChannelProfiles(
  operatorKey: string,
  onProfilesChange: (profiles: ChannelProfile[]) => void,
  serverUrl = '',
) {
  let destination = '';
  try {
    const url = new URL(serverUrl);
    destination = url.origin + url.pathname.replace(/\/+$/, '');
  } catch {
    // Unconfigured instances cannot create channels; never retain URL credentials.
  }
  const storageKey = STORAGE_PREFIX + encodeURIComponent(JSON.stringify([operatorKey, destination]));
  const [pending, setPending] = useState<CreatedChannelProfileIntent[]>(() => restore(storageKey));
  const [pendingKey, setPendingKey] = useState(storageKey);
  const queue = useRef(new Map(pending.map(intent => [intent.channelId, intent])));
  const generation = useRef(0);
  const locked = useRef(false);
  const [busy, setBusy] = useState(false);
  const [unknown, setUnknown] = useState(pending.length > 0);
  const [storageUnavailable, setStorageUnavailable] = useState(false);

  useEffect(() => {
    queue.current = new Map(restore(storageKey).map(intent => [intent.channelId, intent]));
    setPending([...queue.current.values()]);
    setPendingKey(storageKey);
    locked.current = false;
    setBusy(false);
    setUnknown(queue.current.size > 0);
    setStorageUnavailable(false);
    return () => { generation.current += 1; };
  }, [storageKey]);

  const publish = useCallback(() => {
    const intents = [...queue.current.values()];
    setPending(intents);
    try {
      if (intents.length > 0) sessionStorage.setItem(storageKey, JSON.stringify(intents));
      else sessionStorage.removeItem(storageKey);
      setStorageUnavailable(false);
    } catch {
      setStorageUnavailable(true);
    }
  }, [storageKey]);

  const retry = useCallback(async () => {
    if (locked.current || queue.current.size === 0) return;
    locked.current = true;
    setBusy(true);
    const currentGeneration = generation.current;
    const original = [...queue.current.values()];
    try {
      const outcome = await applyCreatedChannelProfiles(original, () => generation.current === currentGeneration);
      if (generation.current !== currentGeneration) return;
      const remaining = new Set(outcome.remaining.map(intent => intent.channelId));
      for (const intent of original) {
        if (!remaining.has(intent.channelId) && queue.current.get(intent.channelId) === intent) {
          queue.current.delete(intent.channelId);
        }
      }
      if (outcome.profiles) onProfilesChange(outcome.profiles);
      setUnknown(!outcome.verified);
      publish();
    } finally {
      if (generation.current === currentGeneration) {
        locked.current = false;
        setBusy(false);
      }
    }
  }, [onProfilesChange, publish]);

  const enqueue = useCallback((intents: CreatedChannelProfileIntent[]) => {
    for (const intent of intents) {
      queue.current.set(intent.channelId, { ...intent, profileIds: [...intent.profileIds] });
    }
    if (intents.length === 0) return;
    publish();
    void retry();
  }, [publish, retry]);

  const dismiss = useCallback(() => {
    if (locked.current) return;
    queue.current.clear();
    publish();
  }, [publish]);

  return { pending: pendingKey === storageKey ? pending : [], busy, unknown, storageUnavailable, enqueue, retry, dismiss };
}
