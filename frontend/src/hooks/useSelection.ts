import { useState, useCallback } from 'react';

export interface SelectionState<T extends number | string> {
  selectedIds: Set<T>;
  lastSelectedId: T | null;
}

export function useSelection<T extends number | string>(items: { id: T }[]) {
  const [selection, setSelection] = useState<SelectionState<T>>({
    selectedIds: new Set(),
    lastSelectedId: null,
  });

  const handleSelect = useCallback(
    (id: T, event: React.MouseEvent) => {
      setSelection((prev) => {
        const newSelectedIds = new Set(prev.selectedIds);

        if (event.shiftKey && prev.lastSelectedId !== null) {
          // Shift-click: select range
          const itemIds = items.map((item) => item.id);
          const lastIndex = itemIds.indexOf(prev.lastSelectedId);
          const currentIndex = itemIds.indexOf(id);

          if (lastIndex !== -1 && currentIndex !== -1) {
            const start = Math.min(lastIndex, currentIndex);
            const end = Math.max(lastIndex, currentIndex);
            for (let i = start; i <= end; i++) {
              newSelectedIds.add(itemIds[i]);
            }
          }
        } else if (event.ctrlKey || event.metaKey) {
          // Ctrl/Cmd-click: toggle single item
          if (newSelectedIds.has(id)) {
            newSelectedIds.delete(id);
          } else {
            newSelectedIds.add(id);
          }
        } else {
          // Regular click: select only this item
          newSelectedIds.clear();
          newSelectedIds.add(id);
        }

        return {
          selectedIds: newSelectedIds,
          lastSelectedId: id,
        };
      });
    },
    [items]
  );

  const selectAll = useCallback(() => {
    setSelection({
      selectedIds: new Set(items.map((item) => item.id)),
      lastSelectedId: items.length > 0 ? items[items.length - 1].id : null,
    });
  }, [items]);

  const clearSelection = useCallback(() => {
    setSelection({
      selectedIds: new Set(),
      lastSelectedId: null,
    });
  }, []);

  // Toggle selection for a single item (used by checkbox clicks)
  const toggleSelect = useCallback((id: T) => {
    setSelection((prev) => {
      const newSelectedIds = new Set(prev.selectedIds);
      if (newSelectedIds.has(id)) {
        newSelectedIds.delete(id);
      } else {
        newSelectedIds.add(id);
      }
      return {
        selectedIds: newSelectedIds,
        lastSelectedId: id,
      };
    });
  }, []);

  const isSelected = useCallback(
    (id: T) => selection.selectedIds.has(id),
    [selection.selectedIds]
  );

  const getSelectedItems = useCallback(() => {
    return items.filter((item) => selection.selectedIds.has(item.id));
  }, [items, selection.selectedIds]);

  return {
    selectedIds: selection.selectedIds,
    selectedCount: selection.selectedIds.size,
    handleSelect,
    toggleSelect,
    selectAll,
    clearSelection,
    isSelected,
    getSelectedItems,
  };
}
