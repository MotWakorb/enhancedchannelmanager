/**
 * EPG Program Utilities
 *
 * Shared helpers for working with EPG grid programs (Dispatcharr
 * /api/epg/grid payload). Used by the Guide tab timeline and the Stats
 * tab "Currently Showing" display.
 */

import type { EPGProgram } from '../types';

// Helper to get program start time (handles both start_time and start field names)
export function getProgramStart(program: EPGProgram): Date {
  return new Date(program.start_time || program.start || '');
}

// Helper to get program end time (handles both end_time and stop field names)
export function getProgramEnd(program: EPGProgram): Date {
  return new Date(program.end_time || program.stop || '');
}

/**
 * Group grid programs by tvg_id, sorted by start time within each group.
 * Dummy EPG sources emit program.tvg_id = channel.uuid, so channel-UUID
 * lookups also go through this map.
 */
export function buildProgramsByTvgId(programs: EPGProgram[]): Map<string, EPGProgram[]> {
  const map = new Map<string, EPGProgram[]>();
  programs.forEach(program => {
    if (program.tvg_id) {
      const existing = map.get(program.tvg_id) || [];
      existing.push(program);
      map.set(program.tvg_id, existing);
    }
  });
  map.forEach((progs) => {
    progs.sort((a, b) => getProgramStart(a).getTime() - getProgramStart(b).getTime());
  });
  return map;
}

/**
 * Find the program airing at `now` for a channel.
 *
 * Matching follows the Guide tab's precedence: the tvg_id resolved via the
 * channel's epg_data_id (or the channel's own tvg_id as fallback) first,
 * then the channel UUID (dummy EPG sources key programs by channel UUID).
 */
export function findCurrentProgram(
  programsByTvgId: Map<string, EPGProgram[]>,
  tvgId: string | null,
  channelUuid: string | null,
  now: Date,
): EPGProgram | null {
  let programs: EPGProgram[] = [];
  if (tvgId) {
    programs = programsByTvgId.get(tvgId) || [];
  }
  if (programs.length === 0 && channelUuid) {
    programs = programsByTvgId.get(channelUuid) || [];
  }
  return programs.find(p => now >= getProgramStart(p) && now < getProgramEnd(p)) ?? null;
}
