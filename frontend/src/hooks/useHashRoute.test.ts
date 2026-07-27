import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useHashRoute, _parseHash, _buildHash } from './useHashRoute';

describe('parseHash', () => {
  it('preserves supported section deep links on audited long pages', () => {
    expect(_parseHash('#stats?section=stats-section-watch-history')).toEqual({
      tab: 'stats', settingsPage: null, section: 'stats-section-watch-history',
    });
    expect(_buildHash('settings', 'integrations', null, 'settings-integrations-section-plex'))
      .toBe('#settings/integrations?section=settings-integrations-section-plex');
  });
  it('preserves the supported M3U changes time window', () => {
    expect(_parseHash('#m3u-changes?hours=24')).toEqual({
      tab: 'm3u-changes', settingsPage: null, m3uChangesHours: 24,
    });
    expect(_buildHash('m3u-changes', null, 24)).toBe('#m3u-changes?hours=24');
  });
  it('returns default tab for empty hash', () => {
    expect(_parseHash('')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('returns default tab for just #', () => {
    expect(_parseHash('#')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('parses valid tab hashes', () => {
    expect(_parseHash('#dashboard')).toEqual({ tab: 'dashboard', settingsPage: null });
    expect(_parseHash('#m3u-manager')).toEqual({ tab: 'm3u-manager', settingsPage: null });
    expect(_parseHash('#epg-manager')).toEqual({ tab: 'epg-manager', settingsPage: null });
    expect(_parseHash('#channel-manager')).toEqual({ tab: 'channel-manager', settingsPage: null });
    expect(_parseHash('#guide')).toEqual({ tab: 'guide', settingsPage: null });
    expect(_parseHash('#logo-manager')).toEqual({ tab: 'logo-manager', settingsPage: null });
    expect(_parseHash('#channel-pipeline')).toEqual({ tab: 'channel-pipeline', settingsPage: null });
    expect(_parseHash('#journal')).toEqual({ tab: 'journal', settingsPage: null });
    expect(_parseHash('#stats')).toEqual({ tab: 'stats', settingsPage: null });
    expect(_parseHash('#settings')).toEqual({ tab: 'settings', settingsPage: null });
  });

  it('parses settings sub-pages', () => {
    expect(_parseHash('#settings/normalization')).toEqual({ tab: 'settings', settingsPage: 'normalization' });
    expect(_parseHash('#settings/channel-defaults')).toEqual({ tab: 'settings', settingsPage: 'channel-defaults' });
    expect(_parseHash('#settings/email')).toEqual({ tab: 'settings', settingsPage: 'email' });
    expect(_parseHash('#settings/scheduled-tasks')).toEqual({ tab: 'settings', settingsPage: 'scheduled-tasks' });
    expect(_parseHash('#settings/auth-settings')).toEqual({ tab: 'settings', settingsPage: 'auth-settings' });
    expect(_parseHash('#settings/tls-settings')).toEqual({ tab: 'settings', settingsPage: 'tls-settings' });
  });

  it('returns default for invalid hash', () => {
    expect(_parseHash('#bogus')).toEqual({ tab: 'channel-manager', settingsPage: null });
    expect(_parseHash('#not-a-tab')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('returns settings with null page for invalid settings sub-page', () => {
    expect(_parseHash('#settings/invalid-page')).toEqual({ tab: 'settings', settingsPage: null });
  });
});

describe('legacy hash aliases (Auto-Creation -> Channel Pipeline rename)', () => {
  it('resolves the old top-level auto-creation hash to channel-pipeline', () => {
    expect(_parseHash('#auto-creation')).toEqual({ tab: 'channel-pipeline', settingsPage: null });
  });

  it('resolves the old settings/auto-creation sub-page hash to settings/channel-pipeline', () => {
    expect(_parseHash('#settings/auto-creation')).toEqual({ tab: 'settings', settingsPage: 'channel-pipeline' });
  });
});

describe('legacy hash alias (Security page removal, bead 09x38.12)', () => {
  it('resolves the old settings/security sub-page hash to settings/backup-restore', () => {
    expect(_parseHash('#settings/security')).toEqual({ tab: 'settings', settingsPage: 'backup-restore' });
  });
});

describe('buildHash', () => {
  it('builds simple tab hashes', () => {
    expect(_buildHash('dashboard')).toBe('#dashboard');
    expect(_buildHash('channel-manager')).toBe('#channel-manager');
    expect(_buildHash('m3u-manager')).toBe('#m3u-manager');
    expect(_buildHash('settings')).toBe('#settings');
  });

  it('builds settings sub-page hashes', () => {
    expect(_buildHash('settings', 'normalization')).toBe('#settings/normalization');
    expect(_buildHash('settings', 'email')).toBe('#settings/email');
  });

  it('omits general sub-page (default)', () => {
    expect(_buildHash('settings', 'general')).toBe('#settings');
    expect(_buildHash('settings', null)).toBe('#settings');
  });
});

describe('useHashRoute', () => {
  let pushStateSpy: ReturnType<typeof vi.spyOn>;
  let replaceStateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    window.history.replaceState(null, '', '#');
    pushStateSpy = vi.spyOn(window.history, 'pushState');
    replaceStateSpy = vi.spyOn(window.history, 'replaceState');
  });

  afterEach(() => {
    pushStateSpy.mockRestore();
    replaceStateSpy.mockRestore();
  });

  it('defaults to channel-manager with no hash', () => {
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('channel-manager');
    expect(result.current.settingsPage).toBeNull();
  });

  it('reads initial hash on mount', () => {
    window.location.hash = '#m3u-manager';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('m3u-manager');
  });

  it('reads settings sub-page from initial hash', () => {
    window.location.hash = '#settings/normalization';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('normalization');
  });

  it('setHash updates tab and calls pushState', () => {
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setHash('epg-manager');
    });

    expect(result.current.activeTab).toBe('epg-manager');
    expect(pushStateSpy).toHaveBeenCalledWith(expect.objectContaining({ ecmRouteIndex: 1 }), '', '#epg-manager');
  });

  it('keeps setHash stable while observing the latest route', () => {
    const { result } = renderHook(() => useHashRoute());
    const initialSetHash = result.current.setHash;
    act(() => result.current.setHash('guide'));
    expect(result.current.setHash).toBe(initialSetHash);
    act(() => result.current.setHash('guide'));
    expect(pushStateSpy).toHaveBeenCalledTimes(1);
  });

  it('setHash with settings page', () => {
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setHash('settings', 'normalization');
    });

    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('normalization');
    expect(pushStateSpy).toHaveBeenCalledWith(expect.objectContaining({ ecmRouteIndex: 1 }), '', '#settings/normalization');
  });

  it('setSettingsPage updates settings sub-page', () => {
    window.location.hash = '#settings';
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setSettingsPage('email');
    });

    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('email');
    expect(pushStateSpy).toHaveBeenCalledWith(expect.objectContaining({ ecmRouteIndex: 1 }), '', '#settings/email');
  });

  it('responds to popstate events', () => {
    const { result } = renderHook(() => useHashRoute());

    // Simulate browser back to a different hash
    act(() => {
      window.location.hash = '#stats';
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(result.current.activeTab).toBe('stats');
  });

  it('keeps route state and URL aligned when a history transition is rejected', () => {
    window.location.hash = '#settings';
    const reject = (event: Event) => event.preventDefault();
    window.addEventListener('ecm:before-route-change', reject);
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      window.location.hash = '#stats';
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(result.current.activeTab).toBe('settings');
    expect(window.location.hash).toBe('#settings');
    window.removeEventListener('ecm:before-route-change', reject);
  });

  it('sets initial hash via replaceState if none present', () => {
    window.location.hash = '';
    renderHook(() => useHashRoute());
    expect(replaceStateSpy).toHaveBeenCalledWith(expect.anything(), '', '#channel-manager');
  });

  it('adds route history metadata without changing an existing valid hash', () => {
    window.location.hash = '#guide';
    renderHook(() => useHashRoute());
    expect(window.location.hash).toBe('#guide');
    expect(window.history.state).toEqual(expect.objectContaining({ ecmRouteIndex: 0 }));
  });

  it('canonicalizes the legacy top-level alias with replaceState', () => {
    window.location.hash = '#auto-creation';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('channel-pipeline');
    expect(replaceStateSpy).toHaveBeenCalledWith(expect.anything(), '', '#channel-pipeline');
    expect(pushStateSpy).not.toHaveBeenCalled();
  });

  it('canonicalizes settings aliases and invalid routes without adding history', () => {
    window.location.hash = '#settings/auto-creation';
    renderHook(() => useHashRoute());
    expect(replaceStateSpy).toHaveBeenCalledWith(expect.anything(), '', '#settings/channel-pipeline');
    expect(pushStateSpy).not.toHaveBeenCalled();
  });
});
