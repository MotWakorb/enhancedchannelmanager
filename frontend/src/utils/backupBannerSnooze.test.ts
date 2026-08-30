/**
 * Tests for the backup-banner snooze rules (beads
 * enhancedchannelmanager-iij6s, enhancedchannelmanager-pui76 review round 2).
 *
 * These pin the three invariants named in `backupBannerSnooze.ts`, as
 * PROPERTIES rather than as the inputs that exposed them:
 *
 *   1. A stored snooze can never place the next warning further out than one
 *      snooze window from now. `1e308` is one instance of the property, not
 *      the specification — so the ceiling case is swept across magnitudes
 *      spanning twenty orders of a millisecond, and the backward clock is
 *      tested as the same rule seen from the other side.
 *   2. An observed enabled `dbas_backup` schedule voids a stored snooze, with
 *      no component mounted anywhere.
 *   3. Storage that throws never hides the warning, and never eats a click.
 *
 * Layer: pure logic over `localStorage`. The rendered-behaviour counterparts
 * are in `components/settings/BackupScheduleBanner.test.tsx`, and the seam
 * that makes invariant 2 fire in the real app is pinned in
 * `services/api.backupBannerSnooze.test.ts`.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  BACKUP_TASK_ID,
  LEGACY_DISMISS_KEY,
  SNOOZE_MS,
  SNOOZE_UNTIL_KEY,
  clearBackupBannerSnooze,
  discardLegacyBackupBannerDismissal,
  isBackupBannerSnoozed,
  startBackupBannerSnooze,
  voidBackupBannerSnoozeIfScheduled,
} from './backupBannerSnooze';

const NOW = 1_770_000_000_000;

describe('backupBannerSnooze', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  describe('invariant 1 — a stored snooze cannot outrun the snooze window', () => {
    it('honours a snooze inside the window', () => {
      localStorage.setItem(SNOOZE_UNTIL_KEY, String(NOW + 5 * 24 * 60 * 60 * 1000));
      expect(isBackupBannerSnoozed(NOW)).toBe(true);
    });

    it('honours a snooze that expires exactly one window from now', () => {
      // The largest value this code could itself have written. Excluding it
      // would quietly shorten every snooze by an instant.
      localStorage.setItem(SNOOZE_UNTIL_KEY, String(NOW + SNOOZE_MS));
      expect(isBackupBannerSnoozed(NOW)).toBe(true);
    });

    it.each([
      ['one millisecond past the ceiling', NOW + SNOOZE_MS + 1],
      ['a year out', NOW + 365 * 24 * 60 * 60 * 1000],
      ['the largest safe integer', Number.MAX_SAFE_INTEGER],
      ['the largest date JavaScript can represent', 8.64e15],
      ['1e21, which stringifies in exponential form', 1e21],
      ['1e308, the value the review demonstrated', 1e308],
      ['Number.MAX_VALUE', Number.MAX_VALUE],
    ])('treats a snooze %s as expired', (_label, until) => {
      localStorage.setItem(SNOOZE_UNTIL_KEY, String(until));
      expect(isBackupBannerSnoozed(NOW)).toBe(false);
    });

    it('treats a legitimate snooze as expired once the clock moves backwards past it', () => {
      // Same rule, seen from the other side: nothing about the stored value
      // changed, the clock did. A snooze taken now and then read from a clock
      // set two months earlier is more than one window away, so it is not a
      // snooze this code could have written for that clock.
      startBackupBannerSnooze(NOW);
      const twoMonthsEarlier = NOW - 60 * 24 * 60 * 60 * 1000;
      expect(isBackupBannerSnoozed(twoMonthsEarlier)).toBe(false);
      // ...and it comes straight back once the clock is right again.
      expect(isBackupBannerSnoozed(NOW)).toBe(true);
    });

    it.each([
      ['absent', null],
      ['unparseable', 'never'],
      ['empty', ''],
      ['a JSON object', '{"until":9999999999999}'],
      ['Infinity', 'Infinity'],
      ['negative', '-1'],
      ['already past', String(NOW - 1)],
      ['exactly now', String(NOW)],
    ])('treats a %s value as not snoozed', (_label, raw) => {
      if (raw !== null) localStorage.setItem(SNOOZE_UNTIL_KEY, raw);
      expect(isBackupBannerSnoozed(NOW)).toBe(false);
    });

    it('writes a snooze that its own reader accepts', () => {
      // The round trip is the point: a writer and a reader that disagree would
      // either silence the banner forever or make the button do nothing.
      expect(startBackupBannerSnooze(NOW)).toBe(true);
      expect(isBackupBannerSnoozed(NOW)).toBe(true);
      expect(isBackupBannerSnoozed(NOW + SNOOZE_MS)).toBe(false);
    });
  });

  describe('invariant 2 — an observed enabled backup schedule voids the snooze', () => {
    beforeEach(() => {
      startBackupBannerSnooze(NOW);
    });

    it('voids the snooze when the backup task has an enabled schedule', () => {
      voidBackupBannerSnoozeIfScheduled(BACKUP_TASK_ID, [
        { enabled: false },
        { enabled: true },
      ]);
      expect(localStorage.getItem(SNOOZE_UNTIL_KEY)).toBeNull();
    });

    const untouched: Array<[string, string, ReadonlyArray<{ enabled?: boolean }> | undefined]> = [
      ['every schedule is disabled', BACKUP_TASK_ID, [{ enabled: false }]],
      ['the task has no schedules', BACKUP_TASK_ID, []],
      ['schedules are missing entirely', BACKUP_TASK_ID, undefined],
      ['the enabled schedule belongs to another task', 'epg_refresh', [{ enabled: true }]],
    ];

    it.each(untouched)('leaves the snooze alone when %s', (_label, taskId, schedules) => {
      voidBackupBannerSnoozeIfScheduled(taskId, schedules);
      expect(localStorage.getItem(SNOOZE_UNTIL_KEY)).not.toBeNull();
    });

    it('clears explicitly too, and tolerates there being nothing to clear', () => {
      clearBackupBannerSnooze();
      expect(localStorage.getItem(SNOOZE_UNTIL_KEY)).toBeNull();
      expect(() => clearBackupBannerSnooze()).not.toThrow();
    });

    it('drops the legacy permanent-dismissal flag, which is never honoured', () => {
      localStorage.setItem(LEGACY_DISMISS_KEY, '1');
      discardLegacyBackupBannerDismissal();
      expect(localStorage.getItem(LEGACY_DISMISS_KEY)).toBeNull();
    });
  });

  describe('invariant 3 — storage failures never hide the warning or eat a click', () => {
    it('reports "not snoozed" when reading storage throws', () => {
      vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new DOMException('SecurityError');
      });
      expect(isBackupBannerSnoozed(NOW)).toBe(false);
    });

    it('reports a failed write rather than throwing, so the caller can still act', () => {
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException('QuotaExceededError');
      });
      expect(startBackupBannerSnooze(NOW)).toBe(false);
    });

    it('swallows a throwing removal on both keys', () => {
      vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
        throw new DOMException('SecurityError');
      });
      expect(() => clearBackupBannerSnooze()).not.toThrow();
      expect(() => discardLegacyBackupBannerDismissal()).not.toThrow();
      expect(() =>
        voidBackupBannerSnoozeIfScheduled(BACKUP_TASK_ID, [{ enabled: true }]),
      ).not.toThrow();
    });
  });
});
