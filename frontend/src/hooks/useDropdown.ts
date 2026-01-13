import { useState, useRef, useEffect, useCallback } from 'react';

export interface UseDropdownReturn {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
  dropdownRef: React.RefObject<HTMLDivElement>;
  toggle: () => void;
  open: () => void;
  close: () => void;
}

/**
 * Hook to manage dropdown state with click-outside detection
 *
 * Consolidates duplicate dropdown logic from ChannelsPane and StreamsPane.
 * Manages isOpen state, provides dropdownRef for the dropdown element,
 * and handles automatic closing when clicking outside.
 *
 * @returns Object containing isOpen state, setIsOpen function, dropdownRef, and helper functions
 *
 * @example
 * const { isOpen, dropdownRef, toggle, close } = useDropdown();
 *
 * <button onClick={toggle}>Toggle Dropdown</button>
 * {isOpen && (
 *   <div ref={dropdownRef}>
 *     <button onClick={close}>Close</button>
 *   </div>
 * )}
 */
export function useDropdown(): UseDropdownReturn {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const toggle = useCallback(() => {
    setIsOpen((prev) => !prev);
  }, []);

  const open = useCallback(() => {
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
  }, []);

  return {
    isOpen,
    setIsOpen,
    dropdownRef,
    toggle,
    open,
    close,
  };
}
