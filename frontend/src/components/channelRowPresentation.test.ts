import { describe, expect, it } from 'vitest';
import type { Channel, Logo } from '../types';
import { resolveChannelArtwork } from './channelRowPresentation';

const channel = {
  id: 1,
  logo_id: 9,
  _stagedLogoUrl: '/staged.png',
} as Channel;

const logo = {
  id: 9,
  cache_url: '/cached.png',
  url: '/origin.png',
} as Logo;

describe('resolveChannelArtwork', () => {
  it('uses staged artwork first only in Edit Mode', () => {
    const logos = new Map([[9, logo]]);
    expect(resolveChannelArtwork(channel, logos, true)).toBe('/staged.png');
    expect(resolveChannelArtwork(channel, logos, false)).toBe('/cached.png');
  });

  it('falls back from cache URL to origin URL and returns null for missing logo', () => {
    expect(resolveChannelArtwork(channel, new Map([[9, { ...logo, cache_url: '' }]]), false)).toBe('/origin.png');
    expect(resolveChannelArtwork({ ...channel, logo_id: null }, new Map(), true)).toBe('/staged.png');
    expect(resolveChannelArtwork({ ...channel, logo_id: null, _stagedLogoUrl: undefined }, new Map(), true)).toBeNull();
  });
});
