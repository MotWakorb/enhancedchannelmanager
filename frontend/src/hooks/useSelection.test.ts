/**
 * Unit tests for useSelection hook.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSelection } from './useSelection';

describe('useSelection', () => {
  const mockItems = [
    { id: 1 },
    { id: 2 },
    { id: 3 },
    { id: 4 },
    { id: 5 },
  ];

  describe('initial state', () => {
    it('starts with empty selection', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      expect(result.current.selectedIds.size).toBe(0);
      expect(result.current.selectedCount).toBe(0);
    });
  });

  describe('handleSelect', () => {
    it('selects single item on click', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.handleSelect(1, { shiftKey: false, ctrlKey: false, metaKey: false } as React.MouseEvent);
      });

      expect(result.current.selectedIds.has(1)).toBe(true);
      expect(result.current.selectedCount).toBe(1);
    });

    it('replaces selection on regular click', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.handleSelect(1, { shiftKey: false, ctrlKey: false, metaKey: false } as React.MouseEvent);
      });
      act(() => {
        result.current.handleSelect(2, { shiftKey: false, ctrlKey: false, metaKey: false } as React.MouseEvent);
      });

      expect(result.current.selectedIds.has(1)).toBe(false);
      expect(result.current.selectedIds.has(2)).toBe(true);
      expect(result.current.selectedCount).toBe(1);
    });

    it('toggles selection on ctrl-click', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.handleSelect(1, { shiftKey: false, ctrlKey: true, metaKey: false } as React.MouseEvent);
      });
      act(() => {
        result.current.handleSelect(2, { shiftKey: false, ctrlKey: true, metaKey: false } as React.MouseEvent);
      });

      expect(result.current.selectedIds.has(1)).toBe(true);
      expect(result.current.selectedIds.has(2)).toBe(true);
      expect(result.current.selectedCount).toBe(2);

      // Toggle off
      act(() => {
        result.current.handleSelect(1, { shiftKey: false, ctrlKey: true, metaKey: false } as React.MouseEvent);
      });

      expect(result.current.selectedIds.has(1)).toBe(false);
      expect(result.current.selectedCount).toBe(1);
    });

    it('toggles selection on cmd-click (meta)', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.handleSelect(1, { shiftKey: false, ctrlKey: false, metaKey: true } as React.MouseEvent);
      });
      act(() => {
        result.current.handleSelect(2, { shiftKey: false, ctrlKey: false, metaKey: true } as React.MouseEvent);
      });

      expect(result.current.selectedCount).toBe(2);
    });

    it('selects range on shift-click', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      // First select item 1
      act(() => {
        result.current.handleSelect(1, { shiftKey: false, ctrlKey: false, metaKey: false } as React.MouseEvent);
      });

      // Then shift-click item 4
      act(() => {
        result.current.handleSelect(4, { shiftKey: true, ctrlKey: false, metaKey: false } as React.MouseEvent);
      });

      // Should select items 1, 2, 3, 4
      expect(result.current.selectedIds.has(1)).toBe(true);
      expect(result.current.selectedIds.has(2)).toBe(true);
      expect(result.current.selectedIds.has(3)).toBe(true);
      expect(result.current.selectedIds.has(4)).toBe(true);
      expect(result.current.selectedIds.has(5)).toBe(false);
      expect(result.current.selectedCount).toBe(4);
    });
  });

  describe('toggleSelect', () => {
    it('toggles individual item selection', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.toggleSelect(1);
      });

      expect(result.current.selectedIds.has(1)).toBe(true);

      act(() => {
        result.current.toggleSelect(1);
      });

      expect(result.current.selectedIds.has(1)).toBe(false);
    });
  });

  describe('selectAll', () => {
    it('selects all items', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.selectAll();
      });

      expect(result.current.selectedCount).toBe(5);
      mockItems.forEach(item => {
        expect(result.current.selectedIds.has(item.id)).toBe(true);
      });
    });
  });

  describe('clearSelection', () => {
    it('clears all selections', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.selectAll();
      });
      act(() => {
        result.current.clearSelection();
      });

      expect(result.current.selectedCount).toBe(0);
    });
  });

  describe('selectMultiple', () => {
    it('adds multiple items to selection', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.selectMultiple([1, 3, 5]);
      });

      expect(result.current.selectedIds.has(1)).toBe(true);
      expect(result.current.selectedIds.has(2)).toBe(false);
      expect(result.current.selectedIds.has(3)).toBe(true);
      expect(result.current.selectedIds.has(4)).toBe(false);
      expect(result.current.selectedIds.has(5)).toBe(true);
      expect(result.current.selectedCount).toBe(3);
    });
  });

  describe('deselectMultiple', () => {
    it('removes multiple items from selection', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.selectAll();
      });
      act(() => {
        result.current.deselectMultiple([2, 4]);
      });

      expect(result.current.selectedIds.has(1)).toBe(true);
      expect(result.current.selectedIds.has(2)).toBe(false);
      expect(result.current.selectedIds.has(3)).toBe(true);
      expect(result.current.selectedIds.has(4)).toBe(false);
      expect(result.current.selectedIds.has(5)).toBe(true);
      expect(result.current.selectedCount).toBe(3);
    });
  });

  describe('isSelected', () => {
    it('returns true for selected items', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.toggleSelect(1);
      });

      expect(result.current.isSelected(1)).toBe(true);
      expect(result.current.isSelected(2)).toBe(false);
    });
  });

  describe('getSelectedItems', () => {
    it('returns array of selected items', () => {
      const { result } = renderHook(() => useSelection(mockItems));

      act(() => {
        result.current.selectMultiple([1, 3]);
      });

      const selected = result.current.getSelectedItems();

      expect(selected).toHaveLength(2);
      expect(selected.some(item => item.id === 1)).toBe(true);
      expect(selected.some(item => item.id === 3)).toBe(true);
    });
  });

  describe('with string IDs', () => {
    const stringItems = [
      { id: 'a' },
      { id: 'b' },
      { id: 'c' },
    ];

    it('works with string IDs', () => {
      const { result } = renderHook(() => useSelection(stringItems));

      act(() => {
        result.current.toggleSelect('a');
        result.current.toggleSelect('c');
      });

      expect(result.current.isSelected('a')).toBe(true);
      expect(result.current.isSelected('b')).toBe(false);
      expect(result.current.isSelected('c')).toBe(true);
    });
  });
});
