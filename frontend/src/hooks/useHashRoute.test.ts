import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useHashRoute, _parseHash, _buildHash } from './useHashRoute';

describe('parseHash', () => {
  it('returns default tab for empty hash', () => {
    expect(_parseHash('')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('returns default tab for just #', () => {
    expect(_parseHash('#')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('parses valid tab hashes', () => {
    expect(_parseHash('#m3u-manager')).toEqual({ tab: 'm3u-manager', settingsPage: null });
    expect(_parseHash('#epg-manager')).toEqual({ tab: 'epg-manager', settingsPage: null });
    expect(_parseHash('#channel-manager')).toEqual({ tab: 'channel-manager', settingsPage: null });
    expect(_parseHash('#guide')).toEqual({ tab: 'guide', settingsPage: null });
    expect(_parseHash('#logo-manager')).toEqual({ tab: 'logo-manager', settingsPage: null });
    expect(_parseHash('#auto-creation')).toEqual({ tab: 'auto-creation', settingsPage: null });
    expect(_parseHash('#journal')).toEqual({ tab: 'journal', settingsPage: null });
    expect(_parseHash('#stats')).toEqual({ tab: 'stats', settingsPage: null });
    expect(_parseHash('#ffmpeg-builder')).toEqual({ tab: 'ffmpeg-builder', settingsPage: null });
    expect(_parseHash('#settings')).toEqual({ tab: 'settings', settingsPage: null });
  });

  it('parses settings sub-pages', () => {
    expect(_parseHash('#settings/normalization')).toEqual({ tab: 'settings', settingsPage: 'normalization' });
    expect(_parseHash('#settings/channel-defaults')).toEqual({ tab: 'settings', settingsPage: 'channel-defaults' });
    expect(_parseHash('#settings/email')).toEqual({ tab: 'settings', settingsPage: 'email' });
    expect(_parseHash('#settings/scheduled-tasks')).toEqual({ tab: 'settings', settingsPage: 'scheduled-tasks' });
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

describe('buildHash', () => {
  it('builds simple tab hashes', () => {
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
    window.location.hash = '';
    pushStateSpy = vi.spyOn(window.history, 'pushState').mockImplementation(() => {});
    replaceStateSpy = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
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
    expect(pushStateSpy).toHaveBeenCalledWith(null, '', '#epg-manager');
  });

  it('setHash with settings page', () => {
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setHash('settings', 'normalization');
    });

    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('normalization');
    expect(pushStateSpy).toHaveBeenCalledWith(null, '', '#settings/normalization');
  });

  it('setSettingsPage updates settings sub-page', () => {
    window.location.hash = '#settings';
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setSettingsPage('email');
    });

    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('email');
    expect(pushStateSpy).toHaveBeenCalledWith(null, '', '#settings/email');
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

  it('sets initial hash via replaceState if none present', () => {
    window.location.hash = '';
    renderHook(() => useHashRoute());
    expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '#channel-manager');
  });

  it('does not set initial hash if one is already present', () => {
    window.location.hash = '#guide';
    renderHook(() => useHashRoute());
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });
});
