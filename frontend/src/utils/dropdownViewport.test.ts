import { describe, expect, it } from 'vitest';
import { calculateDropdownPlacement } from './dropdownViewport';

const rect = (top: number, bottom: number): DOMRect => ({
  x: 24,
  y: top,
  top,
  right: 244,
  bottom,
  left: 24,
  width: 220,
  height: bottom - top,
  toJSON: () => ({}),
} as DOMRect);

describe('calculateDropdownPlacement', () => {
  it('uses a deterministic fallback when the layout engine cannot measure the trigger', () => {
    const unmeasuredRect = {
      x: 0, y: 0, top: 0, right: 0, bottom: 0, left: 0, width: 0, height: 0,
      toJSON: () => ({}),
    } as DOMRect;
    expect(calculateDropdownPlacement(unmeasuredRect, 800, 300, 124)).toEqual({
      top: 4,
      maxHeight: 300,
      placement: 'below',
    });
  });

  it('opens below when the desired menu fits', () => {
    expect(calculateDropdownPlacement(rect(100, 136), 800, 300, 124)).toEqual({
      top: 140,
      maxHeight: 300,
      placement: 'below',
    });
  });

  it('opens above when that is the larger usable side', () => {
    expect(calculateDropdownPlacement(rect(500, 536), 600, 300, 124)).toEqual({
      top: 196,
      maxHeight: 300,
      placement: 'above',
    });
  });

  it('uses the viewport when neither side preserves the menu chrome plus one option', () => {
    expect(calculateDropdownPlacement(rect(100, 136), 220, 300, 124)).toEqual({
      top: 8,
      maxHeight: 204,
      placement: 'viewport',
    });
  });

  it('clamps to the larger anchored side when it can preserve the usable minimum', () => {
    expect(calculateDropdownPlacement(rect(180, 216), 360, 300, 124)).toEqual({
      top: 8,
      maxHeight: 168,
      placement: 'above',
    });
  });

  it('returns null when the trigger is fully above or below the viewport', () => {
    expect(calculateDropdownPlacement(rect(-80, -20), 600, 300, 124)).toBeNull();
    expect(calculateDropdownPlacement(rect(620, 656), 600, 300, 124)).toBeNull();
  });

  it('returns null when the viewport itself cannot preserve a usable menu', () => {
    expect(calculateDropdownPlacement(rect(18, 28), 40, 300, 124)).toBeNull();
  });
});
