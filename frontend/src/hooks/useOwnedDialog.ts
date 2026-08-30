import { useId, useRef } from 'react';
import { useModalFocusLifecycle } from './useModalFocusLifecycle';

/** Caller-owned identity and focus lifecycle for a single semantic dialog. */
export function useOwnedDialog(active = true) {
  const titleId = `${useId()}-title`;
  const containerRef = useRef<HTMLDivElement>(null);
  useModalFocusLifecycle({ containerRef, active });
  return { titleId, containerRef };
}
