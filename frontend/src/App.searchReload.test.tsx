/**
 * Bead enhancedchannelmanager-bv8n7: the "reload channels when search changes"
 * effect used to fire a second full loadChannels() ~500ms after mount even
 * though the search term never changed. loadChannels() flips the channels
 * loading flag, so ChannelsPane swapped its whole group tree for a placeholder
 * and destroyed any open row/group menu.
 */
import { StrictMode, type ReactNode } from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChannelManagerTabProps } from './components/tabs/ChannelManagerTab';
import * as api from './services/api';

const testState = vi.hoisted(() => ({
  channelManagerProps: null as ChannelManagerTabProps | null,
}));

vi.mock('./hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 7, auth_provider: 'local', username: 'operator', is_admin: true },
    logout: vi.fn(),
  }),
  useAdminNavVisible: () => true,
}));

vi.mock('./components/tabs/ChannelManagerTab', () => ({
  ChannelManagerTab: (props: ChannelManagerTabProps) => {
    testState.channelManagerProps = props;
    return <div data-testid="channel-manager" />;
  },
}));

vi.mock('./components/NotificationCenter', () => ({ NotificationCenter: () => null }));

vi.mock('./components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./components')>();
  return {
    ...actual,
    SettingsModal: () => null,
    EditModeRestoreDialog: () => null,
    EditModeRestoredBadge: () => null,
    TabNavigation: () => null,
    UserMenu: () => null,
    PageHeader: ({ actions }: { actions?: ReactNode }) => <div>{actions}</div>,
    EditModeExitDialog: () => null,
  };
});

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>();
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: 'healthy', service: 'ECM' }),
    getSettings: vi.fn().mockResolvedValue({
      configured: true,
      default_channel_profile_ids: [],
      stream_sort_priority: [],
      stream_sort_enabled: {},
      m3u_account_priorities: {},
    }),
    getChannelGroups: vi.fn().mockResolvedValue([]),
    getChannels: vi.fn().mockResolvedValue({ results: [], count: 0, next: null }),
    getProviderGroupSettings: vi.fn().mockResolvedValue([]),
    getM3UAccounts: vi.fn().mockResolvedValue([]),
    getStreamGroups: vi.fn().mockResolvedValue([]),
    getStreams: vi.fn().mockResolvedValue({ results: [], count: 0, next: null }),
    getAllLogos: vi.fn().mockResolvedValue([]),
    getStreamProfiles: vi.fn().mockResolvedValue([]),
    getChannelProfiles: vi.fn().mockResolvedValue([]),
    getProfileConflictReviews: vi.fn().mockResolvedValue({ reviews: [], total: 0 }),
    getEPGSources: vi.fn().mockResolvedValue([]),
    getEPGData: vi.fn().mockResolvedValue([]),
    getM3UStreamMetadata: vi.fn().mockResolvedValue({ count: 0, metadata: {} }),
  };
});

import App from './App';

const channelCalls = () => vi.mocked(api.getChannels).mock.calls;
const searchesRequested = () => channelCalls().map(([params]) => params?.search);

/** Let promise chains settle without advancing the fake clock past a debounce. */
async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mountApp(strict = false) {
  window.history.replaceState({}, '', '#channel-manager');
  render(strict ? <StrictMode><App /></StrictMode> : <App />);
  await waitFor(() => expect(testState.channelManagerProps).not.toBeNull());
  await settle();
}

describe('App channel search reload (bv8n7)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    testState.channelManagerProps = null;
    vi.mocked(api.getChannels).mockClear();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads channels exactly once on mount, even past the debounce interval', async () => {
    await mountApp();
    const afterMount = channelCalls().length;
    expect(afterMount).toBeGreaterThan(0);
    await advance(2000);
    expect(channelCalls().length).toBe(afterMount);
  });

  it('does not double the load under StrictMode double-mount', async () => {
    await mountApp(true);
    const afterMount = channelCalls().length;
    await advance(2000);
    // The search effect adds nothing beyond the mount loads.
    expect(channelCalls().length).toBe(afterMount);
    expect(afterMount).toBeGreaterThan(0);
  });

  it('reloads once, after the debounce, when the search actually changes', async () => {
    await mountApp();
    await advance(1000);
    const before = channelCalls().length;
    act(() => testState.channelManagerProps!.onChannelSearchChange('abc'));
    await advance(400);
    expect(channelCalls().length).toBe(before);
    await advance(200);
    expect(channelCalls().length).toBe(before + 1);
    expect(searchesRequested().slice(-1)[0]).toBe('abc');
    await advance(2000);
    expect(channelCalls().length).toBe(before + 1);
  });

  it('coalesces rapid typing into a single reload', async () => {
    await mountApp();
    await advance(1000);
    const before = channelCalls().length;
    for (const term of ['a', 'ab', 'abc']) {
      act(() => testState.channelManagerProps!.onChannelSearchChange(term));
      await advance(100);
    }
    await advance(1000);
    expect(channelCalls().length).toBe(before + 1);
    expect(searchesRequested().slice(-1)[0]).toBe('abc');
  });

  it('reloads when the search is cleared back to empty', async () => {
    await mountApp();
    await advance(1000);
    act(() => testState.channelManagerProps!.onChannelSearchChange('abc'));
    await advance(600);
    const before = channelCalls().length;
    act(() => testState.channelManagerProps!.onChannelSearchChange(''));
    await advance(600);
    expect(channelCalls().length).toBe(before + 1);
    expect(searchesRequested().slice(-1)[0]).toBeUndefined();
  });

  it('does not reload when typing is reverted to the loaded term inside the debounce', async () => {
    await mountApp();
    await advance(1000);
    const before = channelCalls().length;
    act(() => testState.channelManagerProps!.onChannelSearchChange('x'));
    await advance(100);
    act(() => testState.channelManagerProps!.onChannelSearchChange(''));
    await advance(2000);
    expect(channelCalls().length).toBe(before);
  });
});
