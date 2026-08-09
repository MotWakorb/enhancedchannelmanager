/**
 * Resolving the group a channel falls back to when its own group is deleted
 * (bead enhancedchannelmanager-ayfn9).
 *
 * The resolution is by NAME. The live drill instance carries the group at id 1
 * (of 378 groups), and so does a fresh Dispatcharr 0.28.2 install — but that is
 * a default, not a contract, and a fallback to "id 1" would silently move an
 * operator's channels into whatever group happened to be created first.
 */
import { describe, it, expect } from 'vitest';
import {
  UNGROUPED_TARGET_GROUP_NAME,
  findUngroupedTargetGroup,
} from './ungroupedTargetGroup';

describe('findUngroupedTargetGroup', () => {
  it('matches the baseline group exactly as Dispatcharr names it', () => {
    const groups = [{ id: 7, name: UNGROUPED_TARGET_GROUP_NAME }];
    expect(findUngroupedTargetGroup(groups)).toBe(groups[0]);
  });

  it('matches case-insensitively and ignores surrounding whitespace', () => {
    const groups = [{ id: 9, name: '  DEFAULT group ' }];
    expect(findUngroupedTargetGroup(groups)).toBe(groups[0]);
  });

  it('returns undefined when no group carries the name', () => {
    expect(findUngroupedTargetGroup([{ id: 1, name: 'Ungrouped' }])).toBeUndefined();
  });

  it('never falls back to id 1', () => {
    expect(findUngroupedTargetGroup([{ id: 1, name: 'Something Else' }])).toBeUndefined();
  });

  it('returns undefined for an empty group list', () => {
    expect(findUngroupedTargetGroup([])).toBeUndefined();
  });

  it('preserves the caller group shape so the id is usable', () => {
    const found = findUngroupedTargetGroup([
      { id: 907, name: 'Default Group', channel_count: 4 },
    ]);
    expect(found?.id).toBe(907);
    expect(found?.channel_count).toBe(4);
  });
});
