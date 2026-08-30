/**
 * The handoff that carries "edit this task's schedule" from the Notification
 * Center to the Scheduled Tasks editor.
 *
 * It travels by two carriers at once, and they are not interchangeable:
 *
 * - a `CustomEvent` on `window`, which App.tsx listens for to change route. It
 *   is fire-and-forget: a listener that is not mounted never sees it.
 * - a `sessionStorage` entry, which survives the route change and the lazy
 *   mount of the Settings tree. `SettingsTab` reads it in a mount-only effect
 *   to force its active page, and `ScheduledTasksSection` reads it once the
 *   task list arrives, opens the editor, and removes it.
 *
 * The stored entry is therefore an intent with a lifetime, not a message. Bead
 * `enhancedchannelmanager-6fi7p`: routing the event through the Edit Mode exit
 * guard means the navigation can be DEFERRED and then cancelled, and a
 * cancelled intent that is left in storage hijacks the operator's next visit to
 * any Settings page. Whoever defers the navigation owns clearing the entry.
 *
 * Both carriers deliberately share one spelling — one intent, two carriers —
 * but they are named separately so each call site says which one it means.
 */
export const OPEN_TASK_EDITOR_EVENT = 'ecm:open-task-editor';

/** @see {@link OPEN_TASK_EDITOR_EVENT} — the same intent, on the storage carrier. */
export const OPEN_TASK_EDITOR_STORAGE_KEY = OPEN_TASK_EDITOR_EVENT;

/** What the Notification Center stores under {@link OPEN_TASK_EDITOR_STORAGE_KEY}. */
export interface OpenTaskEditorIntent {
  taskId: string;
}
