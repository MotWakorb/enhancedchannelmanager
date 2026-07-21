export type DropdownPlacement = 'below' | 'above' | 'viewport';

export interface DropdownVerticalPlacement {
  top: number;
  maxHeight: number;
  placement: DropdownPlacement;
}

const DEFAULT_GAP_PX = 4;
const DEFAULT_VIEWPORT_MARGIN_PX = 8;

/**
 * Place a portaled dropdown inside the viewport while preserving enough
 * height for its fixed chrome plus at least one usable option.
 *
 * When a short viewport is split by the trigger so neither anchored side can
 * preserve that minimum, the menu uses the full inset viewport. This may
 * overlap the trigger, but keeps the control operable instead of rendering a
 * search/actions shell with no reachable options.
 */
export function calculateDropdownPlacement(
  triggerRect: DOMRect,
  viewportHeight: number,
  desiredHeight: number,
  minimumUsableHeight: number,
  gap = DEFAULT_GAP_PX,
  viewportMargin = DEFAULT_VIEWPORT_MARGIN_PX,
): DropdownVerticalPlacement | null {
  // DOM implementations without layout (SSR and jsdom) report an all-zero
  // rectangle. Keep a deterministic placement so consumers can still render;
  // a real, rendered trigger has measurable width and height.
  if (
    triggerRect.top === 0
    && triggerRect.bottom === 0
    && triggerRect.left === 0
    && triggerRect.right === 0
    && triggerRect.width === 0
    && triggerRect.height === 0
  ) {
    return {
      top: gap,
      maxHeight: Math.min(desiredHeight, Math.max(0, viewportHeight - gap - viewportMargin)),
      placement: 'below',
    };
  }

  if (triggerRect.bottom <= 0 || triggerRect.top >= viewportHeight) return null;

  const spaceBelow = Math.max(0, viewportHeight - triggerRect.bottom - gap - viewportMargin);
  const spaceAbove = Math.max(0, triggerRect.top - gap - viewportMargin);

  if (spaceBelow >= desiredHeight) {
    return { top: triggerRect.bottom + gap, maxHeight: desiredHeight, placement: 'below' };
  }
  if (spaceAbove >= desiredHeight) {
    return {
      top: triggerRect.top - gap - desiredHeight,
      maxHeight: desiredHeight,
      placement: 'above',
    };
  }

  const largerPlacement = spaceAbove > spaceBelow ? 'above' : 'below';
  const largerSpace = Math.max(spaceAbove, spaceBelow);
  if (largerSpace >= minimumUsableHeight) {
    return largerPlacement === 'above'
      ? {
          top: Math.max(viewportMargin, triggerRect.top - gap - largerSpace),
          maxHeight: largerSpace,
          placement: 'above',
        }
      : {
          top: triggerRect.bottom + gap,
          maxHeight: largerSpace,
          placement: 'below',
        };
  }

  const insetViewportHeight = Math.max(0, viewportHeight - (viewportMargin * 2));
  if (insetViewportHeight < minimumUsableHeight) return null;
  return {
    top: viewportMargin,
    maxHeight: Math.min(desiredHeight, insetViewportHeight),
    placement: 'viewport',
  };
}
