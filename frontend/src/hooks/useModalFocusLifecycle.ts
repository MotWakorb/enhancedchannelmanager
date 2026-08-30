import { useEffect, type RefObject } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

interface ModalFocusLifecycleOptions {
  containerRef: RefObject<HTMLElement | null>;
  initialFocusRef?: RefObject<HTMLElement | null>;
  active?: boolean;
}

/**
 * Opt-in focus lifecycle for a semantic dialog rendered inside ModalOverlay.
 * ModalOverlay deliberately remains a neutral backdrop/Escape primitive.
 * The caller still controls Escape (including busy suppression) through its
 * onClose callback; this hook owns only initial focus, topmost Tab containment,
 * and stack-safe restoration to the element focused when the dialog opened.
 */
export function useModalFocusLifecycle({
  containerRef,
  initialFocusRef,
  active = true,
}: ModalFocusLifecycleOptions): void {
  useEffect(() => {
    if (!active) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    let tabIndexContainer: HTMLElement | null = null;
    const focusContainer = (container: HTMLElement) => {
      if (!container.hasAttribute('tabindex')) {
        container.setAttribute('tabindex', '-1');
        tabIndexContainer = container;
      }
      container.focus();
    };
    const eligibleControls = (container: HTMLElement) =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    const frame = window.requestAnimationFrame(() => {
      const container = containerRef.current;
      if (!container) return;
      const preferred = initialFocusRef?.current;
      const target = preferred && container.contains(preferred) && preferred.matches(FOCUSABLE_SELECTOR)
        ? preferred
        : eligibleControls(container)[0];
      if (target) target.focus();
      else focusContainer(container);
    });

    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const container = containerRef.current;
      const overlay = container?.closest<HTMLElement>('[data-modal-overlay]');
      const overlays = document.querySelectorAll('[data-modal-overlay]');
      if (!container || !overlay || overlay !== overlays[overlays.length - 1]) return;
      const focusable = eligibleControls(container);
      if (focusable.length === 0) {
        event.preventDefault();
        focusContainer(container);
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!container.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleTab);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener('keydown', handleTab);
      tabIndexContainer?.removeAttribute('tabindex');
      if (opener?.isConnected) opener.focus();
    };
  }, [active, containerRef, initialFocusRef]);
}
