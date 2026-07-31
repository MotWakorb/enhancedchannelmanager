/**
 * The section-rail anchor id for one scheduled task (bead de6u1) — the id a
 * shared `#settings/scheduled-tasks?section=…` link names.
 *
 * Kept out of ScheduledTasksSection.tsx so that component file only exports a
 * component (react-refresh/only-export-components), and so the id scheme is
 * addressable on its own: it is a URL contract, not a rendering detail.
 * Same reasoning as taskPillState.ts.
 */

/**
 * Derived from `task_id`, NEVER from `task_name`. The name is a display string
 * a rename can rewrite — `backend/tasks/dbas_sync.py` builds
 * "Cross-Instance Sync: <target>" from a user-editable target name — while the
 * id is the task registry key. Left underived, StickySectionNav slugs the
 * anchor from the label, so renaming a sync target silently moved the anchor
 * and killed every link already shared.
 *
 * `_` becomes `-` because `useHashRoute.parseHash` keeps a `?section=` value
 * only when it matches `/^[a-z0-9-]+$/`, and silently rewrites the hash
 * WITHOUT the query otherwise: a raw snake_case task_id renders a perfectly
 * good anchor that no URL can reach. Every backend task_id is `[a-z0-9_]+`
 * (class constants in `backend/tasks/*.py`, plus `dbas_sync_<int>` from
 * `sync_task_id_for`), so no task_id contains a `-` and the mapping cannot
 * collide. If that convention ever changes, this is the one place to fix.
 *
 * The `settings-scheduled-tasks-` prefix is written out rather than composed
 * from StickySectionNav's `routeKey`: a pinned id must not follow the route
 * slug around, or it is not pinned.
 */
export function sectionIdForTask(taskId: string): string {
  return `settings-scheduled-tasks-section-${taskId.replace(/_/g, '-')}`;
}
