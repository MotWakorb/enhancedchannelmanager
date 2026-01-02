/**
 * Natural sort comparison for strings containing numbers.
 * Sorts "ABC 2" before "ABC 10" instead of lexicographic order.
 */
export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
}
