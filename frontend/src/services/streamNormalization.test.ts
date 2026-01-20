/**
 * Unit tests for streamNormalization service.
 */
import { describe, it, expect } from 'vitest';
import {
  getStreamQualityPriority,
  sortStreamsByQuality,
  stripNetworkPrefix,
  hasNetworkPrefix,
  stripNetworkSuffix,
  hasNetworkSuffix,
  getCountryPrefix,
  stripCountryPrefix,
  getRegionalSuffix,
  detectRegionalVariants,
  normalizeStreamName,
  filterStreamsByTimezone,
} from './streamNormalization';

describe('getStreamQualityPriority', () => {
  it('assigns lowest priority (best) to 4K/UHD', () => {
    expect(getStreamQualityPriority('ESPN 4K')).toBeLessThan(20);
    expect(getStreamQualityPriority('HBO UHD')).toBeLessThan(20);
    expect(getStreamQualityPriority('CNN 2160p')).toBeLessThan(20);
  });

  it('assigns higher priority to FHD than HD', () => {
    const fhdPriority = getStreamQualityPriority('ESPN FHD');
    const hdPriority = getStreamQualityPriority('ESPN HD');
    expect(fhdPriority).toBeLessThan(hdPriority);
  });

  it('assigns higher priority to HD than SD', () => {
    const hdPriority = getStreamQualityPriority('ESPN HD');
    const sdPriority = getStreamQualityPriority('ESPN SD');
    expect(hdPriority).toBeLessThan(sdPriority);
  });

  it('handles numeric resolutions', () => {
    const p1080 = getStreamQualityPriority('ESPN 1080p');
    const p720 = getStreamQualityPriority('ESPN 720p');
    const p480 = getStreamQualityPriority('ESPN 480p');

    expect(p1080).toBeLessThan(p720);
    expect(p720).toBeLessThan(p480);
  });

  it('handles arbitrary resolutions', () => {
    const p540 = getStreamQualityPriority('Channel 540p');
    const p476 = getStreamQualityPriority('Channel 476p');

    expect(p540).toBeLessThan(p476);
  });

  it('returns default priority for streams without quality indicator', () => {
    const priority = getStreamQualityPriority('ESPN');
    expect(priority).toBe(30); // DEFAULT_QUALITY_PRIORITY (HD level, same as 720p)
  });
});

describe('sortStreamsByQuality', () => {
  it('sorts streams by quality (highest first)', () => {
    const streams = [
      { name: 'ESPN SD', m3u_account: 1 },
      { name: 'ESPN FHD', m3u_account: 1 },
      { name: 'ESPN HD', m3u_account: 1 },
      { name: 'ESPN 4K', m3u_account: 1 },
    ];

    const sorted = sortStreamsByQuality(streams);

    expect(sorted[0].name).toBe('ESPN 4K');
    expect(sorted[1].name).toBe('ESPN FHD');
    expect(sorted[2].name).toBe('ESPN HD');
    expect(sorted[3].name).toBe('ESPN SD');
  });

  it('interleaves providers within quality tiers', () => {
    const streams = [
      { name: 'ESPN FHD', m3u_account: 1 },
      { name: 'ESPN FHD', m3u_account: 2 },
      { name: 'ESPN HD', m3u_account: 1 },
      { name: 'ESPN HD', m3u_account: 2 },
    ];

    const sorted = sortStreamsByQuality(streams);

    // FHD tier should come first, interleaved by provider
    expect(sorted[0].m3u_account).toBe(1);
    expect(sorted[1].m3u_account).toBe(2);
    // HD tier should come next
    expect(sorted[2].m3u_account).toBe(1);
    expect(sorted[3].m3u_account).toBe(2);
  });
});

describe('stripNetworkPrefix', () => {
  it('strips known network prefixes', () => {
    expect(stripNetworkPrefix('CHAMP | Queens Park Rangers')).toBe('Queens Park Rangers');
    expect(stripNetworkPrefix('PPV | UFC 300')).toBe('UFC 300');
  });

  it('does not strip if no separator', () => {
    expect(stripNetworkPrefix('ESPN')).toBe('ESPN');
    expect(stripNetworkPrefix('ESPN2')).toBe('ESPN2');
  });

  it('requires content after separator', () => {
    expect(stripNetworkPrefix('PPV | AB')).toBe('PPV | AB'); // Too short
    expect(stripNetworkPrefix('PPV | ABC')).toBe('ABC'); // Long enough
  });

  it('handles custom prefixes', () => {
    expect(stripNetworkPrefix('CUSTOM | Content', ['CUSTOM'])).toBe('Content');
  });
});

describe('hasNetworkPrefix', () => {
  it('returns true when prefix exists', () => {
    expect(hasNetworkPrefix('CHAMP | Content')).toBe(true);
    expect(hasNetworkPrefix('PPV | Event')).toBe(true);
  });

  it('returns false when no prefix', () => {
    expect(hasNetworkPrefix('ESPN')).toBe(false);
    expect(hasNetworkPrefix('Regular Channel')).toBe(false);
  });
});

describe('stripNetworkSuffix', () => {
  it('strips parenthesized suffixes', () => {
    expect(stripNetworkSuffix('ESPN (ENGLISH)')).toBe('ESPN');
    expect(stripNetworkSuffix('CNN (LIVE)')).toBe('CNN');
  });

  it('strips bracketed suffixes', () => {
    expect(stripNetworkSuffix('Sky Sports [LIVE]')).toBe('Sky Sports');
  });

  it('strips bare suffixes', () => {
    expect(stripNetworkSuffix('HBO BACKUP')).toBe('HBO');
    expect(stripNetworkSuffix('CNN Feed')).toBe('CNN');
  });

  it('handles custom suffixes', () => {
    expect(stripNetworkSuffix('ESPN (CUSTOM)', ['CUSTOM'])).toBe('ESPN');
  });
});

describe('hasNetworkSuffix', () => {
  it('returns true when suffix exists', () => {
    expect(hasNetworkSuffix('ESPN (ENGLISH)')).toBe(true);
    expect(hasNetworkSuffix('CNN BACKUP')).toBe(true);
  });

  it('returns false when no suffix', () => {
    expect(hasNetworkSuffix('ESPN')).toBe(false);
  });
});

describe('getCountryPrefix', () => {
  it('detects US prefix', () => {
    expect(getCountryPrefix('US: ESPN')).toBe('US');
    expect(getCountryPrefix('US | CNN')).toBe('US');
  });

  it('detects UK prefix', () => {
    expect(getCountryPrefix('UK: Sky Sports')).toBe('UK');
  });

  it('returns null for no prefix', () => {
    expect(getCountryPrefix('ESPN')).toBeNull();
    expect(getCountryPrefix('Regular Channel')).toBeNull();
  });
});

describe('stripCountryPrefix', () => {
  it('strips country prefix', () => {
    expect(stripCountryPrefix('US: ESPN')).toBe('ESPN');
    expect(stripCountryPrefix('UK | BBC')).toBe('BBC');
  });

  it('preserves name without prefix', () => {
    expect(stripCountryPrefix('ESPN')).toBe('ESPN');
  });
});

describe('getRegionalSuffix', () => {
  it('detects East suffix', () => {
    expect(getRegionalSuffix('HBO East')).toBe('east');
    expect(getRegionalSuffix('ESPN - EAST')).toBe('east');
  });

  it('detects West suffix', () => {
    expect(getRegionalSuffix('HBO West')).toBe('west');
    expect(getRegionalSuffix('ESPN | WEST')).toBe('west');
  });

  it('returns null for no regional suffix', () => {
    expect(getRegionalSuffix('ESPN')).toBeNull();
    expect(getRegionalSuffix('HBO')).toBeNull();
  });
});

describe('detectRegionalVariants', () => {
  it('detects East/West variants', () => {
    const streams = [
      { name: 'HBO East' },
      { name: 'HBO West' },
    ];
    expect(detectRegionalVariants(streams)).toBe(true);
  });

  it('detects base + West as variant', () => {
    const streams = [
      { name: 'HBO' },
      { name: 'HBO West' },
    ];
    expect(detectRegionalVariants(streams)).toBe(true);
  });

  it('returns false when no variants', () => {
    const streams = [
      { name: 'ESPN' },
      { name: 'CNN' },
    ];
    expect(detectRegionalVariants(streams)).toBe(false);
  });
});

describe('normalizeStreamName', () => {
  it('strips quality suffixes', () => {
    expect(normalizeStreamName('ESPN FHD')).toBe('ESPN');
    expect(normalizeStreamName('CNN HD')).toBe('CNN');
    expect(normalizeStreamName('HBO 1080p')).toBe('HBO');
  });

  it('normalizes whitespace', () => {
    expect(normalizeStreamName('ESPN   HD')).toBe('ESPN');
    expect(normalizeStreamName('  CNN  ')).toBe('CNN');
  });

  it('can strip country prefix', () => {
    expect(normalizeStreamName('US: ESPN', { stripCountryPrefix: true })).toBe('ESPN');
  });

  it('can keep and normalize country prefix', () => {
    const result = normalizeStreamName('US: ESPN', {
      keepCountryPrefix: true,
      countrySeparator: '|',
    });
    expect(result).toBe('US | ESPN');
  });

  it('can strip network prefix', () => {
    expect(normalizeStreamName('CHAMP | Event', { stripNetworkPrefix: true })).toBe('Event');
  });

  it('can strip network suffix', () => {
    expect(normalizeStreamName('ESPN (ENGLISH)', { stripNetworkSuffix: true })).toBe('ESPN');
  });

  it('handles timezone preference for regional variants', () => {
    expect(normalizeStreamName('HBO East', 'east')).toBe('HBO');
    expect(normalizeStreamName('HBO West', 'west')).toBe('HBO');
    expect(normalizeStreamName('HBO East', 'both')).toBe('HBO East');
  });

  it('applies custom tags before quality suffixes', () => {
    // Bug fix: custom suffixes should be removed before quality suffix detection
    // Example: "PL| CANAL+ 1 HD (NA)" with custom suffix "NA" should result in "CANAL+ 1"
    const normalizationSettings = {
      disabledBuiltinTags: [],
      customTags: [
        { value: 'NA', mode: 'suffix' as const },
      ],
    };

    const result = normalizeStreamName('PL| CANAL+ 1 HD (NA)', {
      stripCountryPrefix: true,
      normalizationSettings,
    });

    // Should strip: PL| (country), (NA) (custom suffix), HD (quality suffix)
    expect(result).toBe('CANAL+ 1');
  });

  it('applies custom tags with various formats before quality suffixes', () => {
    const normalizationSettings = {
      disabledBuiltinTags: [],
      customTags: [
        { value: 'CUSTOM', mode: 'suffix' as const },
      ],
    };

    // Parenthesized custom suffix
    expect(normalizeStreamName('ESPN HD (CUSTOM)', { normalizationSettings })).toBe('ESPN');

    // Bracketed custom suffix
    expect(normalizeStreamName('ESPN FHD [CUSTOM]', { normalizationSettings })).toBe('ESPN');

    // Bare custom suffix
    expect(normalizeStreamName('ESPN 1080p CUSTOM', { normalizationSettings })).toBe('ESPN');
  });
});

describe('filterStreamsByTimezone', () => {
  const streams = [
    { name: 'HBO' },
    { name: 'HBO East' },
    { name: 'HBO West' },
    { name: 'ESPN' },
  ];

  it('returns all streams for "both"', () => {
    const filtered = filterStreamsByTimezone(streams, 'both');
    expect(filtered).toHaveLength(4);
  });

  it('filters to East and non-suffixed for "east"', () => {
    const filtered = filterStreamsByTimezone(streams, 'east');
    expect(filtered).toHaveLength(3);
    expect(filtered.some(s => s.name === 'HBO West')).toBe(false);
  });

  it('filters to West only for "west"', () => {
    const filtered = filterStreamsByTimezone(streams, 'west');
    expect(filtered).toHaveLength(1);
    expect(filtered[0].name).toBe('HBO West');
  });
});
