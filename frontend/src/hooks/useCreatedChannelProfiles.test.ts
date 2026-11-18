import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { applyCreatedChannelProfiles } from '../services/createdChannelProfiles';
import { useCreatedChannelProfiles } from './useCreatedChannelProfiles';

vi.mock('../services/createdChannelProfiles', () => ({ applyCreatedChannelProfiles: vi.fn() }));
const intent = { channelId: 50000, profileIds: [6] };

beforeEach(() => {
  sessionStorage.clear();
  vi.resetAllMocks();
  vi.mocked(applyCreatedChannelProfiles).mockImplementation(async intents => ({
    remaining: intents, profiles: null, verified: false,
  }));
});

it('retains unknown intent through remount without automatically replaying it', async () => {
  const saved = vi.fn();
  const first = renderHook(() => useCreatedChannelProfiles('local:7', saved));
  act(() => first.result.current.enqueue([intent]));
  await waitFor(() => expect(first.result.current.busy).toBe(false));
  expect(first.result.current.pending).toEqual([intent]);
  first.unmount();
  const restored = renderHook(() => useCreatedChannelProfiles('local:7', saved));
  expect(restored.result.current.pending).toEqual([intent]);
  expect(applyCreatedChannelProfiles).toHaveBeenCalledTimes(1);
  vi.mocked(applyCreatedChannelProfiles).mockResolvedValue({ remaining: [], profiles: [], verified: true });
  await act(async () => restored.result.current.retry());
  expect(restored.result.current.pending).toEqual([]);
});

it('does not expose one operator’s retry plan to another operator', async () => {
  const first = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn()));
  act(() => first.result.current.enqueue([intent]));
  await waitFor(() => expect(first.result.current.busy).toBe(false));
  first.unmount();
  const other = renderHook(() => useCreatedChannelProfiles('local:8', vi.fn()));
  expect(other.result.current.pending).toEqual([]);
});

it('serializes retry clicks and drains only new intent queued during an in-flight attempt', async () => {
  let finish!: (value: Awaited<ReturnType<typeof applyCreatedChannelProfiles>>) => void;
  vi.mocked(applyCreatedChannelProfiles).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const hook = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn()));
  act(() => hook.result.current.enqueue([intent]));
  act(() => {
    void hook.result.current.retry();
    hook.result.current.enqueue([{ channelId: 50001, profileIds: [] }]);
  });
  expect(applyCreatedChannelProfiles).toHaveBeenCalledTimes(1);
  await act(async () => finish({ remaining: [intent], profiles: [], verified: true }));
  expect(vi.mocked(applyCreatedChannelProfiles).mock.calls.map(([intents]) => intents)).toEqual([
    [intent], [{ channelId: 50001, profileIds: [] }],
  ]);
  expect(hook.result.current.pending).toEqual([intent, { channelId: 50001, profileIds: [] }]);
});

it('does not replay restored intent when a new create is enqueued', async () => {
  const first = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn()));
  act(() => first.result.current.enqueue([intent]));
  await waitFor(() => expect(first.result.current.busy).toBe(false));
  first.unmount();
  const restored = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn()));
  const newIntent = { channelId: 50001, profileIds: [] };
  act(() => restored.result.current.enqueue([newIntent]));
  await waitFor(() => expect(restored.result.current.busy).toBe(false));
  expect(vi.mocked(applyCreatedChannelProfiles).mock.calls.map(([intents]) => intents)).toEqual([
    [intent], [newIntent],
  ]);
  expect(restored.result.current.pending).toEqual([intent, newIntent]);
});

it('stores a stale callback’s mapped intent in the original scope without starting requests', () => {
  const saved = vi.fn();
  const hook = renderHook(({ destination }) => useCreatedChannelProfiles('local:7', saved, destination), {
    initialProps: { destination: 'https://first.invalid' },
  });
  const lateEnqueue = hook.result.current.enqueue;
  hook.rerender({ destination: 'https://second.invalid' });
  act(() => lateEnqueue([intent]));
  expect(applyCreatedChannelProfiles).not.toHaveBeenCalled();
  expect(hook.result.current.pending).toEqual([]);
  hook.rerender({ destination: 'https://first.invalid' });
  expect(hook.result.current.pending).toEqual([intent]);
});

it('does not restart recovery from a callback whose original scope was left and re-entered', () => {
  const saved = vi.fn();
  const hook = renderHook(({ operator }) => useCreatedChannelProfiles(operator, saved), {
    initialProps: { operator: 'local:7' },
  });
  const lateEnqueue = hook.result.current.enqueue;
  hook.rerender({ operator: 'local:8' });
  hook.rerender({ operator: 'local:7' });
  act(() => lateEnqueue([intent]));
  expect(applyCreatedChannelProfiles).not.toHaveBeenCalled();
  expect(hook.result.current.pending).toEqual([intent]);
});

it('defers the automatic attempt without deferring persistence or borrowing a later scope', () => {
  const saved = vi.fn();
  const hook = renderHook(({ operator }) => useCreatedChannelProfiles(operator, saved), {
    initialProps: { operator: 'local:7' },
  });
  let start: (() => void) | void;
  act(() => { start = hook.result.current.enqueue([intent], undefined, true); });
  expect(applyCreatedChannelProfiles).not.toHaveBeenCalled();
  hook.rerender({ operator: 'local:8' });
  act(() => start?.());
  expect(applyCreatedChannelProfiles).not.toHaveBeenCalled();
  hook.rerender({ operator: 'local:7' });
  expect(hook.result.current.pending).toEqual([intent]);
});

it('does not carry saved intent to another Dispatcharr destination', async () => {
  const saved = vi.fn();
  const hook = renderHook(({ destination }) => useCreatedChannelProfiles('local:7', saved, destination), {
    initialProps: { destination: 'https://first.invalid' },
  });
  act(() => hook.result.current.enqueue([intent]));
  await waitFor(() => expect(hook.result.current.busy).toBe(false));
  hook.rerender({ destination: 'https://second.invalid' });
  expect(hook.result.current.pending).toEqual([]);
  hook.rerender({ destination: 'https://first.invalid' });
  expect(hook.result.current.pending).toEqual([intent]);
  expect(applyCreatedChannelProfiles).toHaveBeenCalledTimes(1);
});

it('keeps intent in memory and reports unavailable storage rather than pretending it was saved', async () => {
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Synthetic storage unavailable'); });
  try {
    const hook = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn()));
    act(() => hook.result.current.enqueue([intent]));
    await waitFor(() => expect(hook.result.current.busy).toBe(false));
    expect(hook.result.current.pending).toEqual([intent]);
    expect(hook.result.current.storageUnavailable).toBe(true);
  } finally {
    write.mockRestore();
  }
});

it('discarding saved intent does not submit another request', async () => {
  const hook = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn()));
  act(() => hook.result.current.enqueue([intent]));
  await waitFor(() => expect(hook.result.current.busy).toBe(false));
  act(() => hook.result.current.dismiss());
  expect(hook.result.current.pending).toEqual([]);
  expect(applyCreatedChannelProfiles).toHaveBeenCalledTimes(1);
});

it('ignores a late completion after the operator changes', async () => {
  let finish!: (value: Awaited<ReturnType<typeof applyCreatedChannelProfiles>>) => void;
  vi.mocked(applyCreatedChannelProfiles).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const saved = vi.fn();
  const hook = renderHook(({ operator }) => useCreatedChannelProfiles(operator, saved), {
    initialProps: { operator: 'local:7' },
  });
  act(() => hook.result.current.enqueue([intent]));
  hook.rerender({ operator: 'local:8' });
  await act(async () => finish({ remaining: [], profiles: [], verified: true }));
  expect(saved).not.toHaveBeenCalled();
  expect(hook.result.current.pending).toEqual([]);
  hook.rerender({ operator: 'local:7' });
  expect(hook.result.current.pending).toEqual([intent]);
});

it('never includes URL credentials or query strings in the persisted destination key', async () => {
  const hook = renderHook(() => useCreatedChannelProfiles('local:7', vi.fn(), 'https://user:secret@first.invalid/base/?token=hidden'));
  act(() => hook.result.current.enqueue([intent]));
  await waitFor(() => expect(hook.result.current.busy).toBe(false));
  const keys = Array.from({ length: sessionStorage.length }, (_, index) => decodeURIComponent(sessionStorage.key(index)!));
  expect(keys).toEqual(['ecm.created-channel-profiles.v1:["local:7","https://first.invalid/base"]']);
});
