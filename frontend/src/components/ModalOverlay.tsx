import { useEffect, useRef, type ReactNode } from 'react';

interface ModalOverlayProps {
  onClose: () => void;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  role?: string;
  'aria-modal'?: boolean | 'true' | 'false';
  'aria-labelledby'?: string;
  'data-testid'?: string;
  /**
   * Optional backdrop click handler. The overlay intentionally does NOT close on
   * backdrop click by default (see the component note); callers that explicitly
   * want a backdrop click to do something (e.g. accept a default + dismiss)
   * opt in by passing this. The dialog content should stopPropagation so only
   * true backdrop clicks fire it.
   */
  onClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
}

/**
 * Shared modal overlay wrapper.
 *
 * - Does NOT close when clicking the backdrop (click-outside disabled).
 * - Closes on Escape key press.
 * - When multiple overlays are stacked, only the topmost one responds to Escape.
 */
export function ModalOverlay({ onClose, children, className, ...rest }: ModalOverlayProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // Only the topmost overlay should respond
      const allOverlays = document.querySelectorAll('[data-modal-overlay]');
      const last = allOverlays[allOverlays.length - 1];
      if (overlayRef.current === last) {
        onClose();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div ref={overlayRef} className={className || 'modal-overlay'} data-modal-overlay {...rest}>
      {children}
    </div>
  );
}
