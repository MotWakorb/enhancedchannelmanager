import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import './OverflowMenu.css';

export interface OverflowMenuItem {
  /** Visible label — also used as the React key, so keep it unique per menu. */
  label: string;
  /** Material Icons glyph name (e.g. `workspaces`). */
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  /** Optional tooltip / title attribute for the item. */
  title?: string;
}

interface OverflowMenuProps {
  items: OverflowMenuItem[];
  /** aria-label + tooltip for the kebab trigger. */
  label?: string;
  /** Material Icons glyph for the trigger button. Defaults to the kebab (⋮). */
  icon?: string;
}

/**
 * Generic kebab (⋮) "more actions" menu — the collapse-to-kebab half of the
 * shared header/toolbar overflow policy (bead 09x38.2; see
 * docs/css_guidelines.md § "Header / toolbar overflow policy"). Use it to move
 * setup/admin actions out of a crowded header row so the primary actions and
 * the title stay un-squeezed at narrow widths.
 *
 * It is the generalized form of ChannelsPane's PaneToolbarMenu — same visual
 * pattern (portal-rendered fixed dropdown, right-aligned under the trigger),
 * minus that component's bespoke submenus/loading states. Prefer this for new
 * header overflow menus; PaneToolbarMenu stays specialized to ChannelsPane.
 */
export function OverflowMenu({ items, label = 'More actions', icon = 'more_vert' }: OverflowMenuProps) {
  const [open, setOpen] = useState(false);
  const [focusEdge, setFocusEdge] = useState<'first' | 'last'>('first');
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        btnRef.current && !btnRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  useEffect(() => {
    if (!open || !dropdownRef.current) return;
    const enabledItems = [...dropdownRef.current.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')];
    (focusEdge === 'last' ? enabledItems[enabledItems.length - 1] : enabledItems[0])?.focus();
  }, [focusEdge, open]);

  const closeAndRestoreFocus = () => {
    setOpen(false);
    btnRef.current?.focus();
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="overflow-menu-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        title={label}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          setPosition({ top: rect.bottom + 2, left: rect.right });
          setFocusEdge(event.key === 'ArrowUp' ? 'last' : 'first');
          setOpen(true);
        }}
        onClick={(e) => {
          e.stopPropagation();
          if (open) {
            setOpen(false);
            return;
          }
          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
          setPosition({ top: rect.bottom + 2, left: rect.right });
          setOpen(true);
        }}
      >
        <span className="material-icons" aria-hidden="true">{icon}</span>
      </button>
      {open && position && createPortal(
        <div
          ref={dropdownRef}
          className="overflow-menu-dropdown"
          role="menu"
          style={{ top: position.top, left: position.left }}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(event) => {
            const enabledItems = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')];
            const currentIndex = enabledItems.indexOf(document.activeElement as HTMLButtonElement);
            let nextIndex: number | null = null;
            if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % enabledItems.length;
            if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + enabledItems.length) % enabledItems.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = enabledItems.length - 1;
            if (event.key === 'Escape') {
              event.preventDefault();
              closeAndRestoreFocus();
              return;
            }
            if (nextIndex !== null && enabledItems.length > 0) {
              event.preventDefault();
              enabledItems[nextIndex]?.focus();
            }
          }}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className="overflow-menu-item"
              disabled={item.disabled}
              title={item.title}
              onClick={() => { setOpen(false); item.onClick(); }}
            >
              <span className="material-icons" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}
