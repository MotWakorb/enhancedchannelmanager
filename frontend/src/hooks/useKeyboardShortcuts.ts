import { useEffect } from 'react';

export interface UseKeyboardShortcutsOptions {
  /**
   * Callback for Ctrl+A / Cmd+A select all shortcut
   * Only triggers when not focused on input/textarea
   */
  onSelectAll?: () => void;

  /**
   * Callback for Escape key to clear selection
   * Only triggers when context menu is not visible
   */
  onClearSelection?: () => void;

  /**
   * Context menu state (if visible, Escape will close it instead of clearing selection)
   */
  contextMenu?: { visible: boolean } | null;

  /**
   * Callback to close context menu when Escape is pressed
   */
  onCloseContextMenu?: () => void;
}

/**
 * Hook to manage common keyboard shortcuts for selection interfaces
 *
 * Consolidates keyboard shortcut handling from StreamsPane (currently only used there,
 * but designed to benefit ChannelsPane and other selection interfaces).
 *
 * Handles:
 * - Ctrl+A / Cmd+A: Select all items (prevented when input/textarea is focused)
 * - Escape: Clear selection or close context menu
 *
 * @param options - Configuration object with callbacks
 *
 * @example
 * const { contextMenu, hideContextMenu } = useContextMenu();
 * const { selectAll, clearSelection } = useSelection();
 *
 * useKeyboardShortcuts({
 *   onSelectAll: selectAll,
 *   onClearSelection: clearSelection,
 *   contextMenu,
 *   onCloseContextMenu: hideContextMenu,
 * });
 */
export function useKeyboardShortcuts(options: UseKeyboardShortcutsOptions): void {
  const {
    onSelectAll,
    onClearSelection,
    contextMenu,
    onCloseContextMenu,
  } = options;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl/Cmd+A to select all
      if ((e.ctrlKey || e.metaKey) && e.key === 'a' && onSelectAll) {
        const activeElement = document.activeElement;
        // Don't select all if user is typing in an input or textarea
        if (activeElement?.tagName !== 'INPUT' && activeElement?.tagName !== 'TEXTAREA') {
          e.preventDefault();
          onSelectAll();
        }
      }

      // Escape to clear selection or close context menu
      if (e.key === 'Escape') {
        if (contextMenu && onCloseContextMenu) {
          onCloseContextMenu();
        } else if (onClearSelection) {
          onClearSelection();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onSelectAll, onClearSelection, contextMenu, onCloseContextMenu]);
}
