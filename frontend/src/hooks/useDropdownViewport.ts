import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { calculateDropdownPlacement, type DropdownPlacement } from '../utils/dropdownViewport';

export interface AnchoredDropdownPosition {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  placement: DropdownPlacement;
}

interface UseDropdownViewportOptions {
  isOpen: boolean;
  triggerRef: RefObject<HTMLElement | null>;
  desiredHeight: number;
  minimumUsableHeight: number;
  onPlacementUnavailable: () => void;
}

const INITIAL_POSITION: AnchoredDropdownPosition = {
  top: 0,
  left: 0,
  width: 0,
  maxHeight: 0,
  placement: 'below',
};

export function useDropdownViewport({
  isOpen,
  triggerRef,
  desiredHeight,
  minimumUsableHeight,
  onPlacementUnavailable,
}: UseDropdownViewportOptions): AnchoredDropdownPosition {
  const [position, setPosition] = useState(INITIAL_POSITION);
  const animationFrameRef = useRef<number | null>(null);

  const updatePosition = useCallback(() => {
    if (!isOpen || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const vertical = calculateDropdownPlacement(
      rect,
      window.innerHeight,
      desiredHeight,
      minimumUsableHeight,
    );
    if (!vertical) {
      onPlacementUnavailable();
      return;
    }

    const next: AnchoredDropdownPosition = {
      ...vertical,
      left: rect.left,
      width: rect.width,
    };
    setPosition(previous => (
      previous.top === next.top &&
      previous.left === next.left &&
      previous.width === next.width &&
      previous.maxHeight === next.maxHeight &&
      previous.placement === next.placement
        ? previous
        : next
    ));
  }, [desiredHeight, isOpen, minimumUsableHeight, onPlacementUnavailable, triggerRef]);

  const schedulePositionUpdate = useCallback(() => {
    if (animationFrameRef.current !== null) return;
    animationFrameRef.current = window.requestAnimationFrame(() => {
      animationFrameRef.current = null;
      updatePosition();
    });
  }, [updatePosition]);

  useLayoutEffect(() => {
    updatePosition();
  }, [updatePosition]);

  useEffect(() => {
    if (!isOpen) return;
    window.addEventListener('resize', schedulePositionUpdate);
    window.addEventListener('scroll', schedulePositionUpdate, true);
    return () => {
      window.removeEventListener('resize', schedulePositionUpdate);
      window.removeEventListener('scroll', schedulePositionUpdate, true);
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
    };
  }, [isOpen, schedulePositionUpdate]);

  return position;
}
