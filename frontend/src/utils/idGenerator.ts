/**
 * Generate a unique ID using timestamp and random string.
 * Used for generating IDs for operations, change records, and other transient items.
 */
export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}
