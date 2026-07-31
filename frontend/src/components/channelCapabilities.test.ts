import { describe, expect, it } from 'vitest';
import { channelCapabilityTiers } from './channelCapabilities';

describe('channelCapabilityTiers', () => {
  it('uses successful probes only and ignores missing or malformed resolutions', () => {
    const stats = new Map([
      [1, { probe_status: 'success', resolution: '1920x1080' }],
      [2, { probe_status: 'failed', resolution: '3840x2160' }],
      [3, { probe_status: 'success', resolution: 'not-a-resolution' }],
      [4, { probe_status: 'pending', resolution: '1280x720' }],
    ]);
    expect(channelCapabilityTiers([1, 2, 3, 4, 99], stats)).toEqual(['FHD']);
  });

  it('classifies exact boundaries and returns distinct tiers highest-first regardless of input order', () => {
    const stats = new Map([
      [1, { probe_status: 'success', resolution: '640x480' }],
      [2, { probe_status: 'success', resolution: '1280x720' }],
      [3, { probe_status: 'success', resolution: '1920x1080' }],
      [4, { probe_status: 'success', resolution: '3840x2160' }],
      [5, { probe_status: 'success', resolution: '4096x2160' }],
    ]);
    expect(channelCapabilityTiers([1, 2, 3, 4, 5], stats)).toEqual(['4K', 'FHD', 'HD', 'SD']);
  });
});
