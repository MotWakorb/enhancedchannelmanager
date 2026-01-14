import { useState, useCallback } from 'react';

export interface UseModalReturn {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
}

/**
 * Hook to manage simple boolean modal state
 *
 * Consolidates the pattern of `const [showModal, setShowModal] = useState(false)`
 * that appears 15+ times in ChannelsPane.
 *
 * Provides simple open/close/toggle functions for cleaner code.
 *
 * @param initialState - Optional initial state (defaults to false)
 * @returns Object containing isOpen state and open/close/toggle functions
 *
 * @example
 * // Replace this:
 * const [showModal, setShowModal] = useState(false);
 * <button onClick={() => setShowModal(true)}>Open</button>
 * <Modal isOpen={showModal} onClose={() => setShowModal(false)} />
 *
 * // With this:
 * const { isOpen, open, close } = useModal();
 * <button onClick={open}>Open</button>
 * <Modal isOpen={isOpen} onClose={close} />
 *
 * @example
 * // Multiple modals in same component:
 * const createModal = useModal();
 * const editModal = useModal();
 * const deleteModal = useModal();
 *
 * <button onClick={createModal.open}>Create</button>
 * <button onClick={editModal.open}>Edit</button>
 * <button onClick={deleteModal.open}>Delete</button>
 */
export function useModal(initialState: boolean = false): UseModalReturn {
  const [isOpen, setIsOpen] = useState(initialState);

  const open = useCallback(() => {
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
  }, []);

  const toggle = useCallback(() => {
    setIsOpen((prev) => !prev);
  }, []);

  return {
    isOpen,
    open,
    close,
    toggle,
  };
}
