import type { ReactNode } from 'react'

/**
 * How the harness drives a dialog that is inline JSX inside a bigger
 * component rather than an exported component of its own.
 *
 * The harness never reaches into a component's state — it clicks the real
 * controls, so what ends up on screen is the same DOM an operator would get.
 * That is the whole point: measure the component, not an approximation.
 */
export type OpenStep =
  | {
      kind: 'click'
      /** Visible text to match (case-insensitive substring) on a clickable. */
      text?: string
      /** CSS selector. Combined with `text` it narrows the candidate set. */
      selector?: string
      /** Pick the nth match (default 0). */
      nth?: number
    }
  | { kind: 'wait'; ms: number }

export interface DialogRenderer {
  /** The React tree to mount. For 'host' dialogs this is the host component. */
  render: () => ReactNode
  /** Steps run after mount to bring the dialog on screen. */
  open?: OpenStep[]
  /**
   * A selector that must match once the dialog is up. The harness reports
   * `status: 'not-rendered'` when it does not, so a dialog that silently
   * failed to open is never mistaken for one that was measured.
   * Defaults to `.modal-container, [role="dialog"], [role="alertdialog"]`.
   */
  expect?: string
}
