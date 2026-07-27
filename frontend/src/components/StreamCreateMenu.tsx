import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './StreamCreateMenu.css';

/**
 * StreamCreateMenu — "Create in…" dropdown for the Streams pane selection
 * strip (bead enhancedchannelmanager-zwhw4).
 *
 * Replaces the hand-rolled document.body right-click context menu that used
 * to be the only surface for "Create channel(s) in group / in new group".
 * Follows the SelectionActionBar (bead 09x38.17) menu idioms: real React
 * buttons, a type-to-filter group chooser (the enabled-channel-group list
 * can run to thousands of entries, like the bar's Move-to-group chooser),
 * roving arrow-key focus, an aria-live match-count status, and an Escape
 * contract that closes the panel and restores focus to the trigger without
 * clearing the stream selection.
 *
 * The chooser lists the ENABLED channel groups only (same filter the old
 * context submenu applied); "Create in new group…" is pinned below the
 * filterable list so it stays reachable at any filter state.
 */

export interface StreamCreateMenuGroup {
  id: number;
  name: string;
}

interface StreamCreateMenuProps {
  /** Enabled channel groups, already filtered by the caller. */
  groups: StreamCreateMenuGroup[];
  /** Operator picked an existing channel group as the create target. */
  onCreateInGroup: (groupId: number) => void;
  /** Operator picked "Create in new group…". */
  onCreateInNewGroup: () => void;
}

export function StreamCreateMenu({
  groups,
  onCreateInGroup,
  onCreateInNewGroup,
}: StreamCreateMenuProps) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const filterInputRef = useRef<HTMLInputElement>(null);

  const closeMenu = useCallback((refocusTrigger = false) => {
    setOpen(false);
    setFilter('');
    if (refocusTrigger) triggerRef.current?.focus();
  }, []);

  // Close when clicking outside the trigger + panel.
  useEffect(() => {
    if (!open) return;
    const handleMouseDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        closeMenu();
      }
    };
    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [open, closeMenu]);

  // Focus lands in the filter input the moment the panel opens (matching the
  // selection bar's Move-to-group chooser) so typing can start immediately.
  // With no groups to filter, the pinned "New group" action is focused instead.
  useEffect(() => {
    if (!open) return;
    const raf = requestAnimationFrame(() => {
      if (filterInputRef.current) {
        filterInputRef.current.focus();
      } else {
        panelRef.current
          ?.querySelector<HTMLButtonElement>('.stream-create-menu-item:not(:disabled)')
          ?.focus();
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [open]);

  // Case-insensitive substring match — same semantics as the selection bar's
  // Move-to-group filter (bead hzzcv) and the Channels pane group filter.
  const filterQuery = filter.toLowerCase();
  const filteredGroups = useMemo(
    () => groups.filter((group) => group.name.toLowerCase().includes(filterQuery)),
    [groups, filterQuery],
  );

  const getItems = (): HTMLButtonElement[] => {
    if (!panelRef.current) return [];
    return Array.from(
      panelRef.current.querySelectorAll<HTMLButtonElement>('.stream-create-menu-item:not(:disabled)'),
    );
  };

  const activate = (action: () => void) => {
    closeMenu();
    action();
  };

  /** Keyboard handling for the filter input. ArrowDown hands focus to the
   *  first (filtered) result. Escape closes the panel back to the trigger —
   *  it must not bubble to the document, where StreamsPane's selection-clear
   *  Escape shortcut lives. Other caret keys stop propagation so the panel's
   *  roving-focus handler doesn't hijack text editing. */
  const handleFilterKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowDown': {
        e.preventDefault();
        e.stopPropagation();
        getItems()[0]?.focus();
        break;
      }
      case 'ArrowUp':
      case 'ArrowLeft':
      case 'ArrowRight':
      case 'Home':
      case 'End':
        e.stopPropagation();
        break;
      case 'Escape':
        e.preventDefault();
        e.stopPropagation();
        closeMenu(true);
        break;
      default:
        break;
    }
  };

  /** Roving vertical focus across the panel's action buttons; ArrowUp from
   *  the first item returns to the filter input. */
  const handlePanelKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target === filterInputRef.current) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeMenu(true);
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;

    const items = getItems();
    if (items.length === 0) return;
    const activeIndex = items.indexOf(document.activeElement as HTMLButtonElement);
    if (activeIndex === -1) return;

    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Home') {
      items[0]?.focus();
    } else if (e.key === 'End') {
      items[items.length - 1]?.focus();
    } else if (e.key === 'ArrowUp') {
      (activeIndex === 0 ? filterInputRef.current ?? items[0] : items[activeIndex - 1])?.focus();
    } else {
      items[Math.min(activeIndex + 1, items.length - 1)]?.focus();
    }
  };

  /** Escape on the trigger itself (menu open, focus not yet in the panel)
   *  closes the panel without clearing the stream selection. */
  const handleRootKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && open) {
      e.preventDefault();
      e.stopPropagation();
      closeMenu(true);
    }
  };

  return (
    <div className="stream-create-menu" ref={rootRef} onKeyDown={handleRootKeyDown}>
      <button
        type="button"
        ref={triggerRef}
        className={`stream-create-menu-trigger ${open ? 'is-open' : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? 'stream-create-menu-panel' : undefined}
        title="Create channels from selected streams in a chosen group"
        onClick={() => (open ? closeMenu() : setOpen(true))}
      >
        <span className="material-icons" aria-hidden="true">playlist_add</span>
        <span>Create in…</span>
        <span className="material-icons stream-create-menu-arrow" aria-hidden="true">
          {open ? 'expand_less' : 'expand_more'}
        </span>
      </button>
      {open && (
        <div
          id="stream-create-menu-panel"
          ref={panelRef}
          className="stream-create-menu-panel"
          role="dialog"
          aria-label="Create channels in group"
          aria-modal="false"
          onKeyDown={handlePanelKeyDown}
        >
          {groups.length > 0 && (
            <>
              <div className="stream-create-menu-filter">
                <input
                  ref={filterInputRef}
                  type="text"
                  className="stream-create-menu-filter-input"
                  placeholder="Filter groups…"
                  aria-label="Filter groups"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  onKeyDown={handleFilterKeyDown}
                />
                {filter && (
                  <button
                    type="button"
                    className="stream-create-menu-filter-clear"
                    onClick={() => {
                      setFilter('');
                      filterInputRef.current?.focus();
                    }}
                    aria-label="Clear group filter"
                    title="Clear group filter"
                  >
                    <span className="material-icons" aria-hidden="true">close</span>
                  </button>
                )}
              </div>
              <div
                className="stream-create-menu-filter-status"
                role="status"
                aria-live="polite"
                aria-atomic="true"
              >
                {filter !== '' && (
                  filteredGroups.length === 0
                    ? 'No groups found'
                    : `${filteredGroups.length} ${filteredGroups.length === 1 ? 'group' : 'groups'} found`
                )}
              </div>
            </>
          )}
          {groups.length === 0 && (
            <div className="stream-create-menu-empty">No enabled channel groups</div>
          )}
          {filteredGroups.map((group) => (
            <button
              key={group.id}
              type="button"
              className="stream-create-menu-item"
              onClick={() => activate(() => onCreateInGroup(group.id))}
            >
              <span className="material-icons" aria-hidden="true">folder</span>
              <span>{group.name}</span>
            </button>
          ))}
          {filter !== '' && filteredGroups.length === 0 && (
            <div className="stream-create-menu-empty">
              No groups match &quot;{filter}&quot;
            </div>
          )}
          <div className="stream-create-menu-divider" />
          <button
            type="button"
            className="stream-create-menu-item"
            onClick={() => activate(onCreateInNewGroup)}
          >
            <span className="material-icons" aria-hidden="true">create_new_folder</span>
            <span>Create in new group…</span>
          </button>
        </div>
      )}
    </div>
  );
}
