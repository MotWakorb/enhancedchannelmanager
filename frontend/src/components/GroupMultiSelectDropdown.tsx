/**
 * Shared multi-select dropdown for picking a subset of "groups" (channel
 * groups, normalization groups, ...) from a flat id/name list. Replaces the
 * unbounded inline `checkbox-group vertical` lists that used to render every
 * group with no scroll cap or search (bead enhancedchannelmanager-zi85o /
 * GH #677).
 *
 * Interaction pattern is deliberately the same as the proven group-filter
 * dropdowns in StreamsPane.tsx / ChannelsPane.tsx (collapsed "N selected"
 * button -> search + Select All/Clear All + checkbox list), and it reuses
 * their shared `.filter-dropdown-*` classes from `shared/common.css` rather
 * than inventing new ones (see docs/css_guidelines.md "Golden Rule").
 *
 * One deliberate difference from that toolbar idiom: this component renders
 * its menu through a portal at a `position: fixed` coordinate computed from
 * the trigger button (same technique as `CustomSelect.tsx`), instead of
 * `position: absolute` in normal flow. The toolbar dropdowns live in an
 * unclipped header row; this component is used inside scrollable modal
 * bodies (ActionEditor's action list, RuleBuilder's step panel), where an
 * absolutely-positioned menu would be clipped by the nearest `overflow:
 * auto` ancestor. Because the menu portals outside the trigger's DOM
 * subtree, the shared `useDropdown` hook's ref-containment outside-click
 * model doesn't apply here (a click inside the portaled menu is NOT a
 * descendant of the trigger wrapper) -- this component manages its own
 * open state and outside-click/Escape handling instead, following the same
 * approach `CustomSelect.tsx` already uses for its portaled menu.
 */
import { useState, useRef, useEffect, useMemo, useId, useCallback, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import './GroupMultiSelectDropdown.css';

export interface GroupMultiSelectOption {
  id: number;
  name: string;
  /**
   * Renders the option in a muted style with a "(disabled)" suffix -- used
   * for normalization groups that are toggled off. Purely a visual cue; the
   * option remains selectable (selecting a disabled group is harmless, it
   * just has no effect until the group is re-enabled elsewhere).
   */
  inactive?: boolean;
}

export interface GroupMultiSelectDropdownProps {
  options: GroupMultiSelectOption[];
  selectedIds: number[];
  onChange: (ids: number[]) => void;
  /** Accessible name for the control -- used as the options list's
   *  `aria-label` and the search input's `aria-label`. */
  label: string;
  /** Collapsed-button text when nothing is selected. */
  placeholder?: string;
  /** Shown in place of the control when `options` is empty. */
  emptyMessage?: string;
  disabled?: boolean;
  /** Noun used in the "N selected" summary text. */
  itemLabelSingular?: string;
  itemLabelPlural?: string;
  /** Extra class on the outer wrapper, for host-specific width/layout. */
  className?: string;
}

const MENU_GAP_PX = 4;
const VIEWPORT_MARGIN_PX = 8;
const MENU_MAX_HEIGHT_PX = 300;

export function GroupMultiSelectDropdown({
  options,
  selectedIds,
  onChange,
  label,
  placeholder = 'None selected',
  emptyMessage = 'No groups available.',
  disabled = false,
  itemLabelSingular = 'group',
  itemLabelPlural = 'groups',
  className = '',
}: GroupMultiSelectDropdownProps) {
  const reactId = useId();
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [menuStyle, setMenuStyle] = useState({ top: 0, left: 0, width: 0, maxHeight: MENU_MAX_HEIGHT_PX });

  const wrapperRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const updateMenuPosition = useCallback(() => {
    if (!isOpen || !buttonRef.current) return;

    const rect = buttonRef.current.getBoundingClientRect();
    const spaceBelow = Math.max(0, window.innerHeight - rect.bottom - MENU_GAP_PX - VIEWPORT_MARGIN_PX);
    const spaceAbove = Math.max(0, rect.top - MENU_GAP_PX - VIEWPORT_MARGIN_PX);
    const openAbove = spaceBelow < MENU_MAX_HEIGHT_PX && spaceAbove > spaceBelow;
    const availableHeight = openAbove ? spaceAbove : spaceBelow;
    const maxHeight = Math.min(MENU_MAX_HEIGHT_PX, availableHeight);
    const top = openAbove
      ? Math.max(VIEWPORT_MARGIN_PX, rect.top - MENU_GAP_PX - maxHeight)
      : rect.bottom + MENU_GAP_PX;

    setMenuStyle({ top, left: rect.left, width: rect.width, maxHeight });
  }, [isOpen]);

  // The menu is portaled to document.body, so it must be positioned against
  // the browser viewport rather than the modal's scroll box. Layout effect
  // prevents a visible frame at the initial 0,0 position.
  useLayoutEffect(() => {
    updateMenuPosition();
  }, [updateMenuPosition]);

  useEffect(() => {
    if (!isOpen) return;

    window.addEventListener('resize', updateMenuPosition);
    // Capture scrolls from the modal body as well as the window itself.
    window.addEventListener('scroll', updateMenuPosition, true);
    return () => {
      window.removeEventListener('resize', updateMenuPosition);
      window.removeEventListener('scroll', updateMenuPosition, true);
    };
  }, [isOpen, updateMenuPosition]);

  useEffect(() => {
    if (isOpen) {
      searchInputRef.current?.focus();
    } else {
      setSearch('');
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const inWrapper = wrapperRef.current?.contains(target);
      const inMenu = menuRef.current?.contains(target);
      if (!inWrapper && !inMenu) setIsOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        // Capture phase + stopPropagation: this component is used inside
        // ModalOverlay-based editors (RuleBuilder, BulkRuleSettingsModal),
        // which register their OWN Escape handler on `document` (bubble
        // phase) to close/dirty-check the whole modal. Both listeners sit
        // on the same target, so a bubble-phase listener here would run
        // AFTER the modal's (it mounted first) and couldn't stop it --
        // Escape would close this menu AND pop the modal's "discard
        // changes?" prompt in one keystroke. Capturing first and stopping
        // propagation here means Escape closes just the open menu; a
        // second Escape (menu now closed, no capture listener registered)
        // reaches the modal as normal.
        event.stopPropagation();
        setIsOpen(false);
        buttonRef.current?.focus();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown, true);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown, true);
    };
  }, [isOpen]);

  const filteredOptions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return options;
    return options.filter(o => o.name.toLowerCase().includes(q));
  }, [options, search]);

  const toggleOption = (id: number) => {
    onChange(
      selectedIds.includes(id)
        ? selectedIds.filter(x => x !== id)
        : [...selectedIds, id],
    );
  };

  const handleSelectAll = () => {
    const visibleIds = filteredOptions.map(o => o.id);
    onChange([...new Set([...selectedIds, ...visibleIds])]);
  };

  const handleClearAll = () => {
    if (search.trim()) {
      const visibleIds = new Set(filteredOptions.map(o => o.id));
      onChange(selectedIds.filter(id => !visibleIds.has(id)));
    } else {
      onChange([]);
    }
  };

  if (options.length === 0) {
    return <span className="group-multiselect-empty">{emptyMessage}</span>;
  }

  const summaryText =
    selectedIds.length === 0
      ? placeholder
      : `${selectedIds.length} ${selectedIds.length === 1 ? itemLabelSingular : itemLabelPlural} selected`;

  const listboxId = `${reactId}-group-multiselect-options`;

  return (
    <div className={`group-multiselect-dropdown ${className}`} ref={wrapperRef}>
      <button
        ref={buttonRef}
        type="button"
        className="filter-dropdown-button"
        onClick={() => setIsOpen(prev => !prev)}
        disabled={disabled}
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listboxId : undefined}
        aria-label={label}
      >
        <span>{summaryText}</span>
        <span className="dropdown-arrow" aria-hidden="true">{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && createPortal(
        <div
          ref={menuRef}
          className="filter-dropdown-menu group-multiselect-menu"
          style={{
            position: 'fixed',
            top: menuStyle.top,
            left: menuStyle.left,
            width: menuStyle.width,
            maxHeight: menuStyle.maxHeight,
            // The shared in-flow dropdown rule adds margin-top: 4px. This
            // portaled menu already budgets its 4px gap in `top`; retaining
            // that margin would double the gap and defeat viewport clamping.
            marginTop: 0,
          }}
        >
          <div className="filter-dropdown-search">
            <span className="material-icons search-icon" aria-hidden="true">search</span>
            <input
              ref={searchInputRef}
              type="text"
              placeholder={`Search ${itemLabelPlural}...`}
              aria-label={`Search ${label}`}
              value={search}
              onChange={e => setSearch(e.target.value)}
              onClick={e => e.stopPropagation()}
            />
            {search && (
              <button
                type="button"
                className="clear-search"
                onClick={() => {
                  setSearch('');
                  searchInputRef.current?.focus();
                }}
                aria-label="Clear search"
                title="Clear search"
              >
                <span className="material-icons" aria-hidden="true">close</span>
              </button>
            )}
          </div>
          <div className="filter-dropdown-actions">
            <button type="button" className="filter-dropdown-action" onClick={handleSelectAll}>
              Select All{search ? ' Visible' : ''}
            </button>
            <button type="button" className="filter-dropdown-action" onClick={handleClearAll}>
              Clear{search ? ' Visible' : ' All'}
            </button>
          </div>
          <div className="filter-dropdown-options" id={listboxId} role="group" aria-label={label}>
            {filteredOptions.map(option => (
              <label key={option.id} className="filter-dropdown-option">
                <input
                  type="checkbox"
                  checked={selectedIds.includes(option.id)}
                  disabled={disabled}
                  onChange={() => toggleOption(option.id)}
                />
                <span className={`filter-option-name${option.inactive ? ' group-multiselect-option-inactive' : ''}`}>
                  {option.name}{option.inactive ? ' (disabled)' : ''}
                </span>
              </label>
            ))}
            {filteredOptions.length === 0 && (
              <div className="filter-dropdown-empty">No {itemLabelPlural} match &ldquo;{search}&rdquo;</div>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

export default GroupMultiSelectDropdown;
