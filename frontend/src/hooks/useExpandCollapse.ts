import { useState, useCallback, useMemo } from 'react';

export interface UseExpandCollapseReturn<TId extends string | number> {
  /**
   * Set of expanded item IDs
   */
  expandedIds: Set<TId>;

  /**
   * Check if item is expanded
   */
  isExpanded: (id: TId) => boolean;

  /**
   * Toggle expansion state of single item
   */
  toggleExpand: (id: TId) => void;

  /**
   * Expand all items
   */
  expandAll: (allIds: TId[]) => void;

  /**
   * Collapse all items
   */
  collapseAll: () => void;

  /**
   * Whether all items are expanded
   */
  allExpanded: (allIds: TId[]) => boolean;

  /**
   * Whether all items are collapsed
   */
  allCollapsed: boolean;
}

/**
 * Hook to manage expand/collapse state using Set-based approach (efficient)
 *
 * Consolidates expand/collapse functionality from:
 * - ChannelsPane (was object-based: {[groupId: number]: boolean})
 * - StreamsPane (already Set-based: Set<string>)
 *
 * Set-based approach is more efficient than object-based:
 * - O(1) add/delete/has operations
 * - No need to iterate object keys
 * - Smaller memory footprint for sparse data
 *
 * @param initialExpanded - Optional initial set of expanded IDs
 * @returns Object containing state and handler functions
 *
 * @example
 * // ChannelsPane: group IDs (numbers)
 * const {
 *   expandedIds,
 *   isExpanded,
 *   toggleExpand,
 *   expandAll,
 *   collapseAll,
 * } = useExpandCollapse<number>();
 *
 * // Usage in JSX:
 * <div onClick={() => toggleExpand(group.id)}>
 *   <span>{isExpanded(group.id) ? '▼' : '▶'}</span>
 *   {group.name}
 * </div>
 * {isExpanded(group.id) && (
 *   <div>...group content...</div>
 * )}
 *
 * @example
 * // StreamsPane: group names (strings)
 * const {
 *   expandedIds,
 *   isExpanded,
 *   toggleExpand,
 * } = useExpandCollapse<string>();
 */
export function useExpandCollapse<TId extends string | number>(
  initialExpanded?: Set<TId>
): UseExpandCollapseReturn<TId> {
  const [expandedIds, setExpandedIds] = useState<Set<TId>>(initialExpanded || new Set());

  // Check if item is expanded
  const isExpanded = useCallback(
    (id: TId): boolean => {
      return expandedIds.has(id);
    },
    [expandedIds]
  );

  // Toggle expansion state of single item
  const toggleExpand = useCallback((id: TId) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // Expand all items
  const expandAll = useCallback((allIds: TId[]) => {
    setExpandedIds(new Set(allIds));
  }, []);

  // Collapse all items
  const collapseAll = useCallback(() => {
    setExpandedIds(new Set());
  }, []);

  // Check if all items are expanded
  const allExpanded = useCallback(
    (allIds: TId[]): boolean => {
      return allIds.every((id) => expandedIds.has(id));
    },
    [expandedIds]
  );

  // Check if all items are collapsed
  const allCollapsed = useMemo(() => {
    return expandedIds.size === 0;
  }, [expandedIds]);

  return {
    expandedIds,
    isExpanded,
    toggleExpand,
    expandAll,
    collapseAll,
    allExpanded,
    allCollapsed,
  };
}
