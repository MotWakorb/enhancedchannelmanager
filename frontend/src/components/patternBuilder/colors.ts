/**
 * Color palette for annotation highlights in the Pattern Builder.
 * 10 distinct colors that work well on dark theme backgrounds.
 */

export interface AnnotationColor {
  /** Full-opacity color for borders, chips, text. */
  full: string;
  /** 30% opacity for background highlights. */
  bg: string;
}

const PALETTE: AnnotationColor[] = [
  { full: '#646cff', bg: 'rgba(100, 108, 255, 0.3)' },   // Blue
  { full: '#10b981', bg: 'rgba(16, 185, 129, 0.3)' },     // Green
  { full: '#f59e0b', bg: 'rgba(245, 158, 11, 0.3)' },     // Amber
  { full: '#ef4444', bg: 'rgba(239, 68, 68, 0.3)' },      // Red
  { full: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.3)' },     // Purple
  { full: '#ec4899', bg: 'rgba(236, 72, 153, 0.3)' },     // Pink
  { full: '#06b6d4', bg: 'rgba(6, 182, 212, 0.3)' },      // Cyan
  { full: '#f97316', bg: 'rgba(249, 115, 22, 0.3)' },     // Orange
  { full: '#14b8a6', bg: 'rgba(20, 184, 166, 0.3)' },     // Teal
  { full: '#a78bfa', bg: 'rgba(167, 139, 250, 0.3)' },    // Lavender
];

/** Map of variable name → stable color index (persists across renders). */
const variableColorMap = new Map<string, number>();

/** Get the color for a variable name. Assigns a new color if first encounter. */
export function getVariableColor(variableName: string): AnnotationColor {
  let idx = variableColorMap.get(variableName);
  if (idx === undefined) {
    idx = variableColorMap.size % PALETTE.length;
    variableColorMap.set(variableName, idx);
  }
  return PALETTE[idx];
}

/** Reset color assignments (e.g., when switching profiles). */
export function resetVariableColors(): void {
  variableColorMap.clear();
}

/** Get the full palette (for previews / legends). */
export function getPalette(): AnnotationColor[] {
  return PALETTE;
}
