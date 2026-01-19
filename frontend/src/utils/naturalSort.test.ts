/**
 * Unit tests for naturalSort utility.
 */
import { describe, it, expect } from 'vitest';
import { naturalCompare } from './naturalSort';

describe('naturalCompare', () => {
  it('sorts strings without numbers alphabetically', () => {
    expect(naturalCompare('abc', 'xyz')).toBeLessThan(0);
    expect(naturalCompare('xyz', 'abc')).toBeGreaterThan(0);
    expect(naturalCompare('abc', 'abc')).toBe(0);
  });

  it('sorts strings with single-digit numbers correctly', () => {
    expect(naturalCompare('channel 2', 'channel 10')).toBeLessThan(0);
    expect(naturalCompare('channel 10', 'channel 2')).toBeGreaterThan(0);
  });

  it('sorts strings with multi-digit numbers correctly', () => {
    expect(naturalCompare('ABC 2', 'ABC 10')).toBeLessThan(0);
    expect(naturalCompare('ABC 100', 'ABC 20')).toBeGreaterThan(0);
    expect(naturalCompare('ABC 200', 'ABC 1000')).toBeLessThan(0);
  });

  it('handles numbers at the start of strings', () => {
    expect(naturalCompare('1 First', '2 Second')).toBeLessThan(0);
    expect(naturalCompare('10 Tenth', '2 Second')).toBeGreaterThan(0);
    expect(naturalCompare('100 Hundred', '20 Twenty')).toBeGreaterThan(0);
  });

  it('handles mixed alphanumeric content', () => {
    const items = ['item10', 'item2', 'item1', 'item20', 'item3'];
    const sorted = [...items].sort(naturalCompare);
    expect(sorted).toEqual(['item1', 'item2', 'item3', 'item10', 'item20']);
  });

  it('handles channel number prefixes', () => {
    const channels = ['100 | ESPN', '20 | CNN', '5 | HBO', '200 | NBC'];
    const sorted = [...channels].sort(naturalCompare);
    expect(sorted).toEqual(['5 | HBO', '20 | CNN', '100 | ESPN', '200 | NBC']);
  });

  it('is case-insensitive by default', () => {
    expect(naturalCompare('ABC', 'abc')).toBe(0);
    expect(naturalCompare('Channel A', 'channel a')).toBe(0);
  });

  it('handles decimal numbers in context', () => {
    const channels = ['2.1 | ABC', '2.10 | DEF', '2.2 | GHI'];
    const sorted = [...channels].sort(naturalCompare);
    expect(sorted).toEqual(['2.1 | ABC', '2.2 | GHI', '2.10 | DEF']);
  });

  it('handles empty strings', () => {
    expect(naturalCompare('', 'abc')).toBeLessThan(0);
    expect(naturalCompare('abc', '')).toBeGreaterThan(0);
    expect(naturalCompare('', '')).toBe(0);
  });

  it('handles leading zeros correctly', () => {
    expect(naturalCompare('file001', 'file01')).toBeLessThan(0);
    expect(naturalCompare('file01', 'file1')).toBeLessThan(0);
  });

  it('sorts channel names naturally', () => {
    const channels = ['ESPN', 'ESPN2', 'ESPN10', 'ESPNU', 'ESPN+'];
    const sorted = [...channels].sort(naturalCompare);
    // Should sort with numbers in numeric order
    expect(sorted.indexOf('ESPN2')).toBeLessThan(sorted.indexOf('ESPN10'));
  });
});
