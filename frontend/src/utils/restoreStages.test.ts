/**
 * Tests for the restore stage definitions and the current_item -> stage mapper.
 */
import { describe, it, expect } from 'vitest';
import {
  RESTORE_STAGES,
  TOTAL_RESTORE_STAGES,
  stageIndexFromCurrentItem,
  stageNumberFromCurrentItem,
  stageLabelFromCurrentItem,
} from './restoreStages';

describe('restoreStages', () => {
  it('defines a 13-stage sequence', () => {
    expect(TOTAL_RESTORE_STAGES).toBe(13);
    expect(RESTORE_STAGES).toHaveLength(13);
  });

  it('maps a known backend entity key to its 0-based stage index', () => {
    // m3u_account is the second stage (index 1) after preflight (index 0).
    expect(stageIndexFromCurrentItem('m3u_account')).toBe(1);
    expect(stageIndexFromCurrentItem('channel')).toBe(8);
  });

  it('maps a stage key to a 1-based stage number for display', () => {
    expect(stageNumberFromCurrentItem('m3u_account')).toBe(2);
    expect(stageNumberFromCurrentItem('channel')).toBe(9);
  });

  it('resolves the operator-facing label for a stage key', () => {
    expect(stageLabelFromCurrentItem('channel')).toBe('Channels');
    expect(stageLabelFromCurrentItem('epg_source')).toBe('EPG sources');
  });

  it('matches the human label as well as the key (case-insensitive)', () => {
    expect(stageIndexFromCurrentItem('Channels')).toBe(8);
    expect(stageIndexFromCurrentItem('M3U accounts')).toBe(1);
  });

  it('falls back to stage 1 for an empty or unknown current_item (never throws)', () => {
    expect(stageIndexFromCurrentItem('')).toBe(0);
    expect(stageIndexFromCurrentItem(null)).toBe(0);
    expect(stageIndexFromCurrentItem(undefined)).toBe(0);
    expect(stageNumberFromCurrentItem('not_a_real_stage')).toBe(1);
    expect(stageLabelFromCurrentItem('not_a_real_stage')).toBe('Pre-flight validation');
  });
});
