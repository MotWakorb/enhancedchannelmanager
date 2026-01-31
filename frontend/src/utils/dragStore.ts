/**
 * Simple module-level store for drag data.
 * Workaround for browsers/environments that clear dataTransfer.types
 * during cross-component drag operations.
 */

export interface StreamDragData {
  type: 'stream' | 'streamGroup';
  streamIds: number[];
  streamNames?: string[];
  groupNames?: string[];
}

let currentDragData: StreamDragData | null = null;

export function setStreamDragData(data: StreamDragData): void {
  currentDragData = data;
  console.warn('[DRAG-STORE] Set drag data:', data);
}

export function getStreamDragData(): StreamDragData | null {
  return currentDragData;
}

export function clearStreamDragData(): void {
  console.warn('[DRAG-STORE] Cleared drag data');
  currentDragData = null;
}

export function hasStreamDragData(): boolean {
  return currentDragData !== null;
}
