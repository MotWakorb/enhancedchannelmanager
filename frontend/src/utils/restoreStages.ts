/**
 * Canonical stage definitions for a DBAS Phase-2 restore / dry-run.
 *
 * A full restore is a multi-stage job whose ordering is the contract owned by
 * the backend orchestrator (`backend/dbas/restore_orchestrator.py` —
 * `default_importer_steps()` / the ADR-012 D-table hard sequence). The backend
 * task status endpoint (`/api/tasks/{id}`) exposes only the generic
 * `TaskProgress` shape (`backend/task_scheduler.py` — `TaskProgress.to_dict()`):
 *
 *     { total, current, percentage, status, current_item,
 *       success_count, failed_count, skipped_count, started_at }
 *
 * There is NO `stage_number` / `total_stages` field server-side — the grooming
 * guessed a richer shape that does not exist. The stage decomposition is a
 * FRONTEND concept: the future restore task sets `current_item` to the stage
 * key (a category name) as it advances, and this module maps that key back to a
 * stable "Stage N of M" position plus an operator-facing label.
 *
 * Keep `key` values aligned with the backend `EntityType` / phase keys
 * (`backend/dbas/restore_contracts.py`) so the `current_item` string the task
 * emits resolves here. Unknown keys fall through to a 0-index "preparing" state
 * rather than throwing — the UI must never crash on an unexpected stage string.
 */

/** Operating mode for the shared progress surface. */
export type RestoreProgressMode = 'restore' | 'dry-run';

/** One stage in the canonical restore sequence. */
export interface RestoreStage {
  /** Stable key — matches the `current_item` the backend task emits. */
  key: string;
  /** Operator-facing label shown in the stage indicator. */
  label: string;
}

/**
 * The canonical 13-stage restore sequence (ADR-012 D-table order).
 *
 * The order is the contract even where the matching importer is still a seam
 * (`backend/dbas/restore_orchestrator.py` — WIRED vs SEAM). When an importer
 * lands it keeps its position here.
 */
export const RESTORE_STAGES: readonly RestoreStage[] = [
  { key: 'preflight', label: 'Pre-flight validation' },
  { key: 'm3u_account', label: 'M3U accounts' },
  { key: 'epg_source', label: 'EPG sources' },
  { key: 'channel_group', label: 'Channel groups' },
  { key: 'channel_profile', label: 'Channel profiles' },
  { key: 'stream_profile', label: 'Stream profiles' },
  { key: 'user_agent', label: 'User agents' },
  { key: 'user', label: 'Users' },
  { key: 'channel', label: 'Channels' },
  { key: 'stream', label: 'Streams' },
  { key: 'logo', label: 'Logos' },
  { key: 'deferred', label: 'Applying deferred settings' },
  { key: 'finalize', label: 'Finalizing' },
] as const;

/** Total number of stages in the canonical sequence. */
export const TOTAL_RESTORE_STAGES = RESTORE_STAGES.length;

/**
 * Resolve a backend `current_item` string to its stage index (0-based).
 *
 * Matching is case-insensitive and tolerant of the `current_item` being either
 * the stage `key` or the human `label` (the task may emit either). Returns 0
 * (the pre-flight / "preparing" stage) for an empty or unrecognized value — the
 * UI degrades to "Stage 1 of N" rather than crashing on an unexpected string.
 */
export function stageIndexFromCurrentItem(currentItem: string | null | undefined): number {
  if (!currentItem) return 0;
  const needle = currentItem.trim().toLowerCase();
  const idx = RESTORE_STAGES.findIndex(
    (s) => s.key.toLowerCase() === needle || s.label.toLowerCase() === needle
  );
  return idx === -1 ? 0 : idx;
}

/**
 * The 1-based stage number for display ("Stage N of M").
 *
 * Always within `[1, TOTAL_RESTORE_STAGES]`.
 */
export function stageNumberFromCurrentItem(currentItem: string | null | undefined): number {
  return stageIndexFromCurrentItem(currentItem) + 1;
}

/** Resolve the operator-facing stage label for a `current_item` string. */
export function stageLabelFromCurrentItem(currentItem: string | null | undefined): string {
  return RESTORE_STAGES[stageIndexFromCurrentItem(currentItem)].label;
}
