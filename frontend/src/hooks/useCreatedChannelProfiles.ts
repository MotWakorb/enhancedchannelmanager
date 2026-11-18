import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ChannelProfile } from '../types';
import { applyCreatedChannelProfiles, type CreatedChannelProfileIntent } from '../services/createdChannelProfiles';

const STORAGE_PREFIX = 'ecm.created-channel-profiles.v1:';
const validId = (id: unknown): id is number => Number.isSafeInteger(id) && (id as number) > 0;
const validBinding = (binding: unknown): binding is string => typeof binding === 'string' && /^[0-9a-f]{64}$/.test(binding);

interface SavedIntent extends CreatedChannelProfileIntent {
  binding?: string;
}

interface PendingIntent extends SavedIntent {
  missingProfiles?: number[];
  scopeUnavailable?: boolean;
}

interface Scope {
  key: string;
  queue: Map<string, PendingIntent>;
  newIds: Set<string>;
  locked: boolean;
  unknown: boolean;
}

const intentKey = ({ channelId, binding }: SavedIntent) => `${binding ?? ''}:${channelId}`;

function restore(key: string): Map<string, PendingIntent> {
  const queue = new Map<string, PendingIntent>();
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? '[]');
    if (!Array.isArray(value)) return queue;
    for (const item of value) {
      if (!item || !validId(item.channelId) || !Array.isArray(item.profileIds) || !item.profileIds.every(validId)) continue;
      const intent = {
        channelId: item.channelId, profileIds: item.profileIds,
        ...(validBinding(item.binding) ? { binding: item.binding } : {}),
      };
      queue.set(intentKey(intent), intent);
    }
  } catch {
    // Storage can be unavailable; never infer a binding for legacy intent.
  }
  return queue;
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
  const scope = useMemo<Scope>(() => ({
    key: storageKey, queue: new Map(), newIds: new Set(), locked: false, unknown: false,
  }), [storageKey]);
  const active = useRef<Scope | null>(null);
  const [pending, setPending] = useState<PendingIntent[]>([]);
  const [pendingScope, setPendingScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [storageUnavailable, setStorageUnavailable] = useState(false);

  useLayoutEffect(() => {
    scope.queue = restore(scope.key);
    scope.unknown = scope.queue.size > 0;
    active.current = scope;
    setPending([...scope.queue.values()]);
    setPendingScope(scope);
    setBusy(false);
    setUnknown(scope.unknown);
    setStorageUnavailable(false);
    return () => { active.current = null; };
  }, [scope]);

  const publish = useCallback((key: string, queue: Map<string, PendingIntent>, current: boolean) => {
    const intents = [...queue.values()];
    if (current) setPending(intents);
    try {
      if (intents.length > 0) sessionStorage.setItem(key, JSON.stringify(
        intents.map(({ channelId, profileIds, binding }) => ({ channelId, profileIds, binding })),
      ));
      else sessionStorage.removeItem(key);
      if (current) setStorageUnavailable(false);
    } catch {
      if (current) setStorageUnavailable(true);
    }
  }, []);

  const run = useCallback(async (explicit: boolean) => {
    const isCurrent = () => active.current === scope;
    if (!isCurrent() || scope.locked || scope.queue.size === 0) return;
    scope.locked = true;
    setBusy(true);
    const take = (all: boolean) => {
      const ids = all ? [...scope.queue.keys()] : [...scope.newIds];
      scope.newIds.clear();
      return ids;
    };
    try {
      let ids = take(explicit);
      if (explicit) scope.unknown = false;
      while (isCurrent() && ids.length > 0) {
        const batches = new Map<string | undefined, PendingIntent[]>();
        for (const id of ids) {
          const intent = scope.queue.get(id);
          if (!intent) continue;
          const batch = batches.get(intent.binding) ?? [];
          batch.push(intent);
          batches.set(intent.binding, batch);
        }
        for (const [binding, batch] of batches) {
          if (!isCurrent()) return;
          const original = batch.map(({ channelId, profileIds }) => ({ channelId, profileIds }));
          const outcome = await applyCreatedChannelProfiles(original, binding, isCurrent);
          if (!isCurrent()) return;
          const remaining = new Set(outcome.remaining.map(intent => intent.channelId));
          for (const intent of batch) {
            const id = intentKey(intent);
            if (scope.queue.get(id) !== intent) continue;
            if (!remaining.has(intent.channelId)) scope.queue.delete(id);
            else scope.queue.set(id, {
              ...intent, missingProfiles: outcome.missingProfiles?.[intent.channelId],
              scopeUnavailable: outcome.scopeUnavailable,
            });
          }
          if (outcome.profiles) onProfilesChange(outcome.profiles);
          scope.unknown ||= !outcome.verified;
          setUnknown(scope.unknown);
          publish(scope.key, scope.queue, true);
        }
        ids = take(false);
      }
    } finally {
      scope.locked = false;
      if (isCurrent()) setBusy(false);
    }
  }, [onProfilesChange, publish, scope]);

  const enqueue = useCallback((intents: CreatedChannelProfileIntent[], binding?: string, defer = false) => {
    if (intents.length === 0) return;
    const current = active.current;
    const target = current?.key === scope.key ? current : null;
    const queue = target?.queue ?? restore(scope.key);
    const entries = new Map<string, SavedIntent>();
    for (const { channelId, profileIds } of intents) {
      const intent = { channelId, profileIds: [...profileIds], ...(validBinding(binding) ? { binding } : {}) };
      const id = intentKey(intent);
      queue.set(id, intent);
      entries.set(id, intent);
    }
    publish(scope.key, queue, target !== null);
    // A stale callback can save only; it cannot start the active scope's drain,
    // even if the local operator and URL have since switched away and back.
    if (current !== scope) return;
    const start = () => {
      if (active.current !== scope) return;
      for (const [id, intent] of entries) {
        if (scope.queue.get(id) === intent) scope.newIds.add(id);
      }
      entries.clear();
      if (scope.newIds.size > 0) void run(false);
    };
    if (defer) return start;
    start();
  }, [publish, run, scope]);

  const dismiss = useCallback(() => {
    if (active.current !== scope || scope.locked) return;
    scope.queue.clear();
    scope.newIds.clear();
    scope.unknown = false;
    setUnknown(false);
    publish(scope.key, scope.queue, true);
  }, [publish, scope]);

  return {
    pending: pendingScope === scope ? pending : [], busy, unknown, storageUnavailable, enqueue,
    retry: () => run(true), dismiss,
  };
}
