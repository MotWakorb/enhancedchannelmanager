import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SortMode } from '../types';
import './SelectionActionBar.css';

/**
 * Floating selection action bar (bead enhancedchannelmanager-09x38.17).
 *
 * Rendered across the bottom of the viewport (Gmail/Drive pattern) while at
 * least one channel is selected in Channel Manager edit mode. Fixed
 * positioning keeps pane widths untouched at every breakpoint — the ~620px
 * Channels pane can't host labeled buttons, which is what originally forced
 * everything into the header kebab.
 *
 * Top tier: Delete, Probe, Find Duplicates, Renumber, Assign EPG,
 * Merge (2+ selected only), a '⋮ More' overflow that opens upward, and Clear.
 * The More menu carries the Move / Selection / Profiles sections and is fully
 * keyboard-navigable (arrows, Home/End, Enter, Escape). Escape with no menu
 * open clears the selection (unless a modal owns the keypress).
 *
 * The Move-to-group submenu (bead hzzcv) has a type-to-filter input — the
 * live instance runs ~2,940 groups, too many to scroll — that focuses
 * automatically when the submenu opens. "Uncategorized" and "New group…"
 * are pinned outside the filter; only named groups are matched, using the
 * same case-insensitive substring semantics as the Channels pane's group
 * filter dropdown (bead mn8).
 */

export interface SelectionBarGroup {
  id: number;
  name: string;
}

export interface SelectionBarProfile {
  id: number;
  name: string;
}

export type SortEnabledCriteria = Record<
  'resolution' | 'bitrate' | 'framerate' | 'm3u_priority' | 'audio_channels' | 'custom_streams',
  boolean
>;

const DEFAULT_SORT_CRITERIA: SortEnabledCriteria = {
  resolution: true,
  bitrate: true,
  framerate: true,
  m3u_priority: false,
  audio_channels: false,
  custom_streams: false,
};

/** Sort-mode entries mirroring PaneToolbarMenu / SortDropdownButton labels. */
const SORT_MODE_ENTRIES: { mode: SortMode; label: string; icon: string; criterion: keyof SortEnabledCriteria | null }[] = [
  { mode: 'smart', label: 'Smart Sort', icon: 'auto_awesome', criterion: null },
  { mode: 'resolution', label: 'By Resolution', icon: 'aspect_ratio', criterion: 'resolution' },
  { mode: 'bitrate', label: 'By Bitrate', icon: 'speed', criterion: 'bitrate' },
  { mode: 'framerate', label: 'By Framerate', icon: 'slow_motion_video', criterion: 'framerate' },
  { mode: 'm3u_priority', label: 'By M3U Priority', icon: 'low_priority', criterion: 'm3u_priority' },
  { mode: 'audio_channels', label: 'By Audio Channels', icon: 'surround_sound', criterion: 'audio_channels' },
  { mode: 'custom_streams', label: 'By Custom Streams', icon: 'edit_note', criterion: 'custom_streams' },
];

type SubmenuId = 'move' | 'sort' | 'profiles';

interface SelectionActionBarProps {
  selectedCount: number;
  onDelete: () => void;
  onProbe: () => void;
  probing?: boolean;
  onFindDuplicates: () => void;
  onRenumber: () => void;
  onAssignEPG: () => void;
  assigningEPG?: boolean;
  /** Only rendered when selectedCount >= 2. */
  onMerge: () => void;
  onClear: () => void;
  // '⋮ More' menu — Move section
  groups: SelectionBarGroup[];
  onMoveToGroup: (groupId: number | null) => void;
  onNewGroup: () => void;
  // '⋮ More' menu — Selection section
  onNormalize: () => void;
  onSetLogoFromM3U: () => void;
  onSetLogoFromEPG: () => void;
  settingLogo?: boolean;
  sortEnabledCriteria?: SortEnabledCriteria;
  onSortStreams: (mode: SortMode) => void;
  sortingStreams?: boolean;
  onFetchGracenote: () => void;
  fetchingGracenote?: boolean;
  // '⋮ More' menu — Profiles section
  profiles: SelectionBarProfile[];
  onSetProfileVisibility: (profileId: number, enabled: boolean) => void;
}

export function SelectionActionBar({
  selectedCount,
  onDelete,
  onProbe,
  probing = false,
  onFindDuplicates,
  onRenumber,
  onAssignEPG,
  assigningEPG = false,
  onMerge,
  onClear,
  groups,
  onMoveToGroup,
  onNewGroup,
  onNormalize,
  onSetLogoFromM3U,
  onSetLogoFromEPG,
  settingLogo = false,
  sortEnabledCriteria = DEFAULT_SORT_CRITERIA,
  onSortStreams,
  sortingStreams = false,
  onFetchGracenote,
  fetchingGracenote = false,
  profiles,
  onSetProfileVisibility,
}: SelectionActionBarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [openSubmenu, setOpenSubmenu] = useState<SubmenuId | null>(null);
  // Type-to-filter for the Move-to-group submenu (bead hzzcv) — the live
  // instance has ~2,940 groups, so scrolling to a target is impractical.
  const [moveFilter, setMoveFilter] = useState('');
  const moveFilterInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const moreBtnRef = useRef<HTMLButtonElement>(null);
  // Ref mirror so the document-level Escape listener sees current state
  // without re-subscribing on every open/close.
  const menuOpenRef = useRef(menuOpen);
  menuOpenRef.current = menuOpen;

  const closeMenu = useCallback((refocusTrigger = false) => {
    setMenuOpen(false);
    setOpenSubmenu(null);
    if (refocusTrigger) moreBtnRef.current?.focus();
  }, []);

  // Escape: close the open menu, else clear the selection. Ignored while a
  // modal is open (the modal owns Escape) or while typing in a field.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        return;
      }
      if (document.querySelector('.modal-overlay')) return;
      if (menuOpenRef.current) {
        closeMenu(true);
      } else {
        onClear();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [closeMenu, onClear]);

  // Close the More menu when clicking outside it.
  useEffect(() => {
    if (!menuOpen) return;
    const handleMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        menuRef.current && !menuRef.current.contains(target) &&
        moreBtnRef.current && !moreBtnRef.current.contains(target)
      ) {
        closeMenu();
      }
    };
    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [menuOpen, closeMenu]);

  // Focus the first menu item when the menu opens.
  useEffect(() => {
    if (!menuOpen) return;
    const first = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)');
    first?.focus();
  }, [menuOpen]);

  // Move-to-group submenu: reset the filter whenever it closes, so reopening
  // starts from the full list rather than a stale filter.
  useEffect(() => {
    if (openSubmenu !== 'move') {
      setMoveFilter('');
    }
  }, [openSubmenu]);

  // Move-to-group submenu: focus lands in the filter input the moment it
  // opens — whether opened by click or by ArrowRight — so typing can start
  // immediately. (The other submenus keep focusing their first menu item,
  // handled in the ArrowRight case below.)
  useEffect(() => {
    if (openSubmenu !== 'move') return;
    const raf = requestAnimationFrame(() => moveFilterInputRef.current?.focus());
    return () => cancelAnimationFrame(raf);
  }, [openSubmenu]);

  const getMenuItems = (): HTMLButtonElement[] => {
    if (!menuRef.current) return [];
    return Array.from(
      menuRef.current.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)'),
    );
  };

  const moveFocus = (delta: 1 | -1) => {
    const items = getMenuItems();
    if (items.length === 0) return;
    const active = document.activeElement as HTMLButtonElement | null;
    const idx = active ? items.indexOf(active) : -1;
    const next = idx === -1
      ? (delta === 1 ? 0 : items.length - 1)
      : (idx + delta + items.length) % items.length;
    items[next]?.focus();
  };

  /** Arrow/Home/End keyboard navigation for the More menu. Escape is handled
   *  by the document-level listener so bar-level and menu-level behavior
   *  can't double-fire. */
  const handleMenuKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        moveFocus(1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        moveFocus(-1);
        break;
      case 'Home': {
        e.preventDefault();
        getMenuItems()[0]?.focus();
        break;
      }
      case 'End': {
        e.preventDefault();
        const items = getMenuItems();
        items[items.length - 1]?.focus();
        break;
      }
      case 'ArrowRight': {
        const target = e.target as HTMLElement;
        const submenuId = target.getAttribute('data-submenu-trigger') as SubmenuId | null;
        if (submenuId) {
          e.preventDefault();
          setOpenSubmenu(submenuId);
          // Focus lands on the first submenu item after it renders. Move's
          // filter input is focused instead, by the dedicated effect above.
          if (submenuId !== 'move') {
            requestAnimationFrame(() => {
              const sub = menuRef.current?.querySelector<HTMLButtonElement>(
                '.selection-bar-submenu [role="menuitem"]:not(:disabled)',
              );
              sub?.focus();
            });
          }
        }
        break;
      }
      case 'ArrowLeft': {
        if (openSubmenu) {
          e.preventDefault();
          closeSubmenuToTrigger(openSubmenu);
        }
        break;
      }
      default:
        break;
    }
  };

  const toggleSubmenu = (id: SubmenuId) => {
    setOpenSubmenu((prev) => (prev === id ? null : id));
  };

  /** Closes the given submenu and returns focus to its trigger button. */
  const closeSubmenuToTrigger = (id: SubmenuId) => {
    const trigger = menuRef.current?.querySelector<HTMLButtonElement>(`[data-submenu-trigger="${id}"]`);
    setOpenSubmenu(null);
    trigger?.focus();
  };

  /** Keyboard handling for the Move-to-group filter input. ArrowDown hands
   *  focus off to the (filtered) results list. ArrowUp/Left/Right/Home/End
   *  stop propagation so the menu's roving-focus and submenu-toggle handlers
   *  don't hijack normal text-caret movement inside the field. Escape closes
   *  the submenu only — never the whole menu, never the selection — matching
   *  the bar's existing "submenu first" Escape contract. */
  const handleMoveFilterKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowDown': {
        e.preventDefault();
        e.stopPropagation();
        const first = menuRef.current?.querySelector<HTMLButtonElement>(
          '#selection-bar-move-chooser .selection-bar-menu-item--sub:not(:disabled)',
        );
        first?.focus();
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
        closeSubmenuToTrigger('move');
        break;
      default:
        break;
    }
  };

  /** Keep vertical roving focus inside the Move-to-group chooser. The filter
   * input naturally precedes the first result; navigation clamps at the final
   * action while preserving normal text-editing keys in the input. */
  const handleMoveSubmenuKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target === moveFilterInputRef.current) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeSubmenuToTrigger('move');
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;

    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLButtonElement>('.selection-bar-menu-item--sub:not(:disabled)'),
    );
    const clearButton = e.currentTarget.querySelector<HTMLButtonElement>('.selection-bar-move-filter-clear');
    if (e.target === clearButton) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'ArrowDown') {
        items[0]?.focus();
      } else if (e.key === 'End') {
        items[items.length - 1]?.focus();
      } else {
        moveFilterInputRef.current?.focus();
      }
      return;
    }
    const activeIndex = items.indexOf(document.activeElement as HTMLButtonElement);
    if (activeIndex === -1 || items.length === 0) return;

    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Home') {
      items[0]?.focus();
    } else if (e.key === 'End') {
      items[items.length - 1]?.focus();
    } else if (e.key === 'ArrowUp') {
      (activeIndex === 0 ? moveFilterInputRef.current : items[activeIndex - 1])?.focus();
    } else {
      items[Math.min(activeIndex + 1, items.length - 1)]?.focus();
    }
  };

  const activate = (action: () => void) => {
    closeMenu();
    action();
  };

  if (selectedCount === 0) return null;

  const sortModes = SORT_MODE_ENTRIES.filter(
    (entry) => entry.criterion === null || sortEnabledCriteria[entry.criterion],
  );

  // Move-to-group filtering matches the Channels pane's searchable group
  // filter dropdown (bead mn8, ChannelsPane.tsx ~L6882/6898): case-insensitive
  // substring match on the raw (untrimmed) query. "Uncategorized" and "New
  // group…" are pinned outside the filter — they're fixed actions, not
  // filterable named groups — so they stay reachable at any filter state.
  const moveFilterQuery = moveFilter.toLowerCase();
  const filteredMoveGroups = groups.filter((group) => group.name.toLowerCase().includes(moveFilterQuery));

  const submenuTrigger = (id: SubmenuId, icon: string, label: string, loading = false) => (
    <button
      type="button"
      role="menuitem"
      className={`selection-bar-menu-item selection-bar-menu-item--submenu ${openSubmenu === id ? 'is-open' : ''}`}
      aria-haspopup={id === 'move' ? 'dialog' : 'menu'}
      aria-expanded={openSubmenu === id}
      aria-controls={id === 'move' ? 'selection-bar-move-chooser' : undefined}
      data-submenu-trigger={id}
      disabled={loading}
      onClick={() => toggleSubmenu(id)}
    >
      <span className={`material-icons ${loading ? 'spinning' : ''}`} aria-hidden="true">
        {loading ? 'sync' : icon}
      </span>
      <span>{label}</span>
      <span className="material-icons selection-bar-submenu-arrow" aria-hidden="true">
        {openSubmenu === id ? 'expand_less' : 'expand_more'}
      </span>
    </button>
  );

  return createPortal(
    <div className="selection-action-bar" role="toolbar" aria-label="Selection actions">
      <span className="selection-action-bar-count" data-testid="selection-bar-count">
        {selectedCount} selected
      </span>
      <button
        type="button"
        className="selection-bar-btn selection-bar-btn--danger"
        onClick={onDelete}
        title="Delete selected channels"
      >
        <span className="material-icons" aria-hidden="true">delete</span>
        <span>Delete</span>
      </button>
      <button
        type="button"
        className="selection-bar-btn"
        onClick={onProbe}
        disabled={probing}
        title="Probe streams of selected channels"
      >
        <span className={`material-icons ${probing ? 'spinning' : ''}`} aria-hidden="true">
          {probing ? 'sync' : 'speed'}
        </span>
        <span>{probing ? 'Probing…' : 'Probe'}</span>
      </button>
      <button
        type="button"
        className="selection-bar-btn"
        onClick={onFindDuplicates}
        title="Find duplicate channels in selection"
      >
        <span className="material-icons" aria-hidden="true">manage_search</span>
        <span>Find Duplicates</span>
      </button>
      <button
        type="button"
        className="selection-bar-btn"
        onClick={onRenumber}
        title="Renumber selected channels"
      >
        <span className="material-icons" aria-hidden="true">tag</span>
        <span>Renumber</span>
      </button>
      <button
        type="button"
        className="selection-bar-btn"
        onClick={onAssignEPG}
        disabled={assigningEPG}
        title="Assign EPG to selected channels"
      >
        <span className={`material-icons ${assigningEPG ? 'spinning' : ''}`} aria-hidden="true">
          {assigningEPG ? 'sync' : 'live_tv'}
        </span>
        <span>Assign EPG</span>
      </button>
      {selectedCount >= 2 && (
        <button
          type="button"
          className="selection-bar-btn"
          onClick={onMerge}
          title="Merge selected channels into one"
        >
          <span className="material-icons" aria-hidden="true">call_merge</span>
          <span>Merge</span>
        </button>
      )}

      <div className="selection-bar-more">
        <button
          type="button"
          ref={moreBtnRef}
          className={`selection-bar-btn selection-bar-btn--more ${menuOpen ? 'is-open' : ''}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="More selection actions"
          title="More selection actions"
          onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
        >
          <span className="material-icons" aria-hidden="true">more_vert</span>
          <span>More</span>
        </button>
        {menuOpen && (
          <div
            ref={menuRef}
            className="selection-bar-menu"
            role="menu"
            aria-label="More selection actions"
            onKeyDown={handleMenuKeyDown}
          >
            {/* Move section */}
            <div className="selection-bar-menu-section-label">Move</div>
            {submenuTrigger('move', 'drive_file_move', 'Move to group')}
            {openSubmenu === 'move' && (
              <div
                id="selection-bar-move-chooser"
                className="selection-bar-submenu"
                role="dialog"
                aria-label="Move to group"
                aria-modal="false"
                onKeyDown={handleMoveSubmenuKeyDown}
              >
                <div className="selection-bar-move-filter">
                  <input
                    ref={moveFilterInputRef}
                    type="text"
                    className="selection-bar-move-filter-input"
                    placeholder="Filter groups…"
                    aria-label="Filter groups"
                    value={moveFilter}
                    onChange={(e) => setMoveFilter(e.target.value)}
                    onKeyDown={handleMoveFilterKeyDown}
                  />
                  {moveFilter && (
                    <button
                      type="button"
                      className="selection-bar-move-filter-clear"
                      onClick={() => {
                        setMoveFilter('');
                        moveFilterInputRef.current?.focus();
                      }}
                      aria-label="Clear group filter"
                      title="Clear group filter"
                    >
                      <span className="material-icons" aria-hidden="true">close</span>
                    </button>
                  )}
                </div>
                <div
                  className="selection-bar-move-filter-status"
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                >
                  {moveFilter !== '' && (
                    filteredMoveGroups.length === 0
                      ? 'No groups found'
                      : `${filteredMoveGroups.length} ${filteredMoveGroups.length === 1 ? 'group' : 'groups'} found`
                  )}
                </div>
                <button
                  type="button"
                  className="selection-bar-menu-item selection-bar-menu-item--sub"
                  onClick={() => activate(() => onMoveToGroup(null))}
                >
                  <span className="material-icons" aria-hidden="true">folder_off</span>
                  <span>Uncategorized</span>
                </button>
                {filteredMoveGroups.map((group) => (
                  <button
                    key={group.id}
                    type="button"
                    className="selection-bar-menu-item selection-bar-menu-item--sub"
                    onClick={() => activate(() => onMoveToGroup(group.id))}
                  >
                    <span className="material-icons" aria-hidden="true">folder</span>
                    <span>{group.name}</span>
                  </button>
                ))}
                {moveFilter !== '' && filteredMoveGroups.length === 0 && (
                  <div className="selection-bar-move-filter-empty">
                    No groups match &quot;{moveFilter}&quot;
                  </div>
                )}
                <div className="selection-bar-menu-divider" />
                <button
                  type="button"
                  className="selection-bar-menu-item selection-bar-menu-item--sub"
                  onClick={() => activate(onNewGroup)}
                >
                  <span className="material-icons" aria-hidden="true">create_new_folder</span>
                  <span>New group…</span>
                </button>
              </div>
            )}

            {/* Selection section */}
            <div className="selection-bar-menu-divider" />
            <div className="selection-bar-menu-section-label">Selection</div>
            <button
              type="button"
              role="menuitem"
              className="selection-bar-menu-item"
              onClick={() => activate(onNormalize)}
            >
              <span className="material-icons" aria-hidden="true">text_format</span>
              <span>Normalize Names</span>
            </button>
            <button
              type="button"
              role="menuitem"
              className="selection-bar-menu-item"
              disabled={settingLogo}
              onClick={() => activate(onSetLogoFromM3U)}
            >
              <span className={`material-icons ${settingLogo ? 'spinning' : ''}`} aria-hidden="true">
                {settingLogo ? 'sync' : 'image'}
              </span>
              <span>Set Logo from M3U</span>
            </button>
            <button
              type="button"
              role="menuitem"
              className="selection-bar-menu-item"
              disabled={settingLogo}
              onClick={() => activate(onSetLogoFromEPG)}
            >
              <span className={`material-icons ${settingLogo ? 'spinning' : ''}`} aria-hidden="true">
                {settingLogo ? 'sync' : 'image'}
              </span>
              <span>Set Logo from EPG</span>
            </button>
            {submenuTrigger('sort', 'sort', sortingStreams ? 'Sorting…' : 'Sort Streams', sortingStreams)}
            {openSubmenu === 'sort' && (
              <div className="selection-bar-submenu" role="menu" aria-label="Sort streams">
                {sortModes.map((entry) => (
                  <button
                    key={entry.mode}
                    type="button"
                    role="menuitem"
                    className="selection-bar-menu-item selection-bar-menu-item--sub"
                    onClick={() => activate(() => onSortStreams(entry.mode))}
                  >
                    <span className="material-icons" aria-hidden="true">{entry.icon}</span>
                    <span>{entry.label}</span>
                  </button>
                ))}
              </div>
            )}
            <button
              type="button"
              role="menuitem"
              className="selection-bar-menu-item"
              disabled={fetchingGracenote}
              onClick={() => activate(onFetchGracenote)}
            >
              <span className={`material-icons ${fetchingGracenote ? 'spinning' : ''}`} aria-hidden="true">
                {fetchingGracenote ? 'sync' : 'confirmation_number'}
              </span>
              <span>Fetch Gracenote IDs</span>
            </button>

            {/* Profiles section */}
            {profiles.length > 0 && (
              <>
                <div className="selection-bar-menu-divider" />
                <div className="selection-bar-menu-section-label">Profiles</div>
                {submenuTrigger('profiles', 'visibility', 'Profile visibility')}
                {openSubmenu === 'profiles' && (
                  <div className="selection-bar-submenu" role="menu" aria-label="Profile visibility">
                    {profiles.map((profile) => (
                      <div key={profile.id} className="selection-bar-profile-row">
                        <span className="selection-bar-profile-name" title={profile.name}>{profile.name}</span>
                        <button
                          type="button"
                          role="menuitem"
                          className="selection-bar-profile-btn"
                          aria-label={`Enable selected channels in profile ${profile.name}`}
                          title={`Enable in ${profile.name}`}
                          onClick={() => activate(() => onSetProfileVisibility(profile.id, true))}
                        >
                          <span className="material-icons" aria-hidden="true">visibility</span>
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className="selection-bar-profile-btn"
                          aria-label={`Disable selected channels in profile ${profile.name}`}
                          title={`Disable in ${profile.name}`}
                          onClick={() => activate(() => onSetProfileVisibility(profile.id, false))}
                        >
                          <span className="material-icons" aria-hidden="true">visibility_off</span>
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <button
        type="button"
        className="selection-bar-btn selection-bar-btn--clear"
        onClick={onClear}
        aria-label="Clear selection"
        title="Clear selection (Esc)"
      >
        <span className="material-icons" aria-hidden="true">close</span>
        <span>Clear</span>
      </button>
    </div>,
    document.body,
  );
}
