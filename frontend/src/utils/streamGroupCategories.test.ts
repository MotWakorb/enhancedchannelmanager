import { describe, it, expect } from 'vitest';
import { extractGroupCategory, categorizeStreamGroups, OTHER_CATEGORY } from './streamGroupCategories';

describe('extractGroupCategory', () => {
  it('extracts the trimmed token before a pipe with surrounding spaces', () => {
    expect(extractGroupCategory('CA | Documentary')).toBe('CA');
  });

  it('extracts the trimmed token before a pipe with no leading space', () => {
    expect(extractGroupCategory('CA| CANADA HD/RAW 60FPS')).toBe('CA');
  });

  it('extracts the trimmed token before a colon delimiter', () => {
    expect(extractGroupCategory('UK: SPORTS [1080p]')).toBe('UK');
  });

  it('unifies pipe and colon conventions for the same literal prefix', () => {
    // "UK | Sports", "UK| PSF PPV", and "UK: SPORTS [1080p]" are three
    // distinct naming conventions seen live for the same provider prefix.
    expect(extractGroupCategory('UK | Sports')).toBe('UK');
    expect(extractGroupCategory('UK| PSF PPV')).toBe('UK');
    expect(extractGroupCategory('UK: SPORTS [1080p]')).toBe('UK');
  });

  it('does NOT merge semantically-similar but textually distinct prefixes', () => {
    // US / USA / CA / CAN are left as separate categories -- derived purely
    // from the literal text, never a hardcoded country list.
    expect(extractGroupCategory('US| ABC HD/RAW 60FPS')).toBe('US');
    expect(extractGroupCategory('USA: ESPN PLUS [1080p]')).toBe('USA');
    expect(extractGroupCategory('CA | Documentary')).toBe('CA');
    expect(extractGroupCategory('CAN: ENGLISH [1080p]')).toBe('CAN');
  });

  it('returns Other for a name with no delimiter', () => {
    expect(extractGroupCategory('Default Group')).toBe(OTHER_CATEGORY);
  });

  it('returns Other when the delimiter is the first character (empty prefix)', () => {
    expect(extractGroupCategory('| Foo')).toBe(OTHER_CATEGORY);
  });

  it('uses whichever delimiter occurs earliest when both are present', () => {
    expect(extractGroupCategory('CA: Something | Else')).toBe('CA');
    expect(extractGroupCategory('Sports | Region: East')).toBe('Sports');
  });
});

describe('categorizeStreamGroups', () => {
  it('buckets groups by derived category and preserves input order within a bucket', () => {
    const groups = [
      { name: 'CA | Documentary' },
      { name: 'US | News' },
      { name: 'CA | Kids' },
    ];
    const result = categorizeStreamGroups(groups);
    const ca = result.find((c) => c.category === 'CA');
    expect(ca?.groups.map((g) => g.name)).toEqual(['CA | Documentary', 'CA | Kids']);
  });

  it('reports the correct group count per category', () => {
    const groups = [
      { name: 'CA | Documentary' },
      { name: 'CA | Kids' },
      { name: 'CA | News' },
      { name: 'US | News' },
    ];
    const result = categorizeStreamGroups(groups);
    expect(result.find((c) => c.category === 'CA')?.groups.length).toBe(3);
    expect(result.find((c) => c.category === 'US')?.groups.length).toBe(1);
  });

  it('sorts categories naturally with Other always last', () => {
    const groups = [
      { name: 'Default Group' },
      { name: 'US | News' },
      { name: 'CA | Kids' },
      { name: 'UK | Sports' },
    ];
    const result = categorizeStreamGroups(groups);
    expect(result.map((c) => c.category)).toEqual(['CA', 'UK', 'US', OTHER_CATEGORY]);
  });

  it('returns an empty array for an empty input', () => {
    expect(categorizeStreamGroups([])).toEqual([]);
  });

  it('matches the real live /api/stream-groups category distribution (field-value survey)', () => {
    // Captured live from a running instance 2026-07-17 (bead 09x38.5) --
    // subset covering every naming convention actually seen in production:
    // "X | Y", "X| Y", "X: Y [tag]", and no-delimiter names.
    const liveSample = [
      { name: 'CA | Documentary 📺' },
      { name: 'CA | Entertainment' },
      { name: 'CAN: ENGLISH [1080p]' },
      { name: 'CAN: KIDS [1080p]' },
      { name: 'CA| CANADA ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ' },
      { name: 'CA| DOCUMENTARY EN' },
      { name: 'Default Group' },
      { name: 'Live Pay-Per View 🎟️' },
      { name: 'Radio | Sirius XM' },
      { name: 'Sports | Big Ten+' },
      { name: 'Sports | FloSports' },
      { name: 'UK | Documentary' },
      { name: 'UK: DOCUMENTARY [1080p]' },
      { name: 'UK| PSF PPV' },
      { name: 'US | Entertainment' },
      { name: 'USA | BIG10+' },
      { name: 'USA: ABC NETWORK [1080p]' },
      { name: 'US| ABC ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ' },
    ];
    const result = categorizeStreamGroups(liveSample);
    const categoryNames = result.map((c) => c.category);
    expect(categoryNames).toEqual(['CA', 'CAN', 'Radio', 'Sports', 'UK', 'US', 'USA', OTHER_CATEGORY]);
    expect(result.find((c) => c.category === 'CA')?.groups.length).toBe(4);
    expect(result.find((c) => c.category === 'UK')?.groups.length).toBe(3);
    expect(result.find((c) => c.category === 'US')?.groups.length).toBe(2);
    expect(result.find((c) => c.category === OTHER_CATEGORY)?.groups.length).toBe(2);
  });
});
