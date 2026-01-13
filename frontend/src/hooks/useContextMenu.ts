import { useState, useEffect, useCallback } from 'react';

export interface ContextMenuState<T = Record<string, unknown>> {
  visible: boolean;
  x: number;
  y: number;
  metadata: T;
}

export interface UseContextMenuReturn<T = Record<string, unknown>> {
  contextMenu: ContextMenuState<T> | null;
  showContextMenu: (x: number, y: number, metadata: T) => void;
  hideContextMenu: () => void;
}

/**
 * Hook to manage context menu state with click-outside and keyboard handling
 *
 * Consolidates duplicate context menu implementations from ChannelsPane and StreamsPane.
 * Manages context menu visibility, position, and metadata (e.g., selected IDs).
 * Automatically handles click-outside detection and Escape key to close the menu.
 *
 * @returns Object containing contextMenu state and functions to show/hide it
 *
 * @example
 * interface MyMenuMetadata {
 *   itemIds: number[];
 * }
 *
 * const { contextMenu, showContextMenu, hideContextMenu } = useContextMenu<MyMenuMetadata>();
 *
 * const handleRightClick = (e: React.MouseEvent, ids: number[]) => {
 *   e.preventDefault();
 *   showContextMenu(e.clientX, e.clientY, { itemIds: ids });
 * };
 *
 * {contextMenu && (
 *   <div style={{ top: contextMenu.y, left: contextMenu.x }}>
 *     <button onClick={hideContextMenu}>Close</button>
 *     {contextMenu.metadata.itemIds.map(...)}
 *   </div>
 * )}
 */
export function useContextMenu<T = Record<string, unknown>>(): UseContextMenuReturn<T> {
  const [contextMenu, setContextMenu] = useState<ContextMenuState<T> | null>(null);

  const showContextMenu = useCallback((x: number, y: number, metadata: T) => {
    setContextMenu({
      visible: true,
      x,
      y,
      metadata,
    });
  }, []);

  const hideContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  // Close context menu when clicking outside or pressing Escape
  useEffect(() => {
    if (!contextMenu) return;

    const handleClickOutside = () => {
      setContextMenu(null);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setContextMenu(null);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [contextMenu]);

  return {
    contextMenu,
    showContextMenu,
    hideContextMenu,
  };
}
