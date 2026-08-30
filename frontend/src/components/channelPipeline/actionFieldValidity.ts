/**
 * Makes an action field's REFUSED entry visible to the rule's save seam
 * (bead `enhancedchannelmanager-ay3iq`).
 *
 * The problem this exists to solve. `ActionEditor` validates several fields
 * against the operator's raw TEXT, which it has to hold in component state
 * because the `Action` object cannot represent an entry the rule refuses: a
 * refused Create Channel start stores no `channel_number` at all, and a refused
 * Sort Group start stores no `starting_number`. `RuleBuilder.validate()` sees
 * only the `Action` objects, so it read those actions as valid and saved them.
 * The operator was shown a red error, pressed Save (or Enter) anyway, and the
 * rule was stored with the field simply absent: automatic numbering instead of
 * the start they typed, or the group's current lowest instead of it. Different
 * behaviour from the one they asked for, arrived at silently. That is the
 * exact defect class the field-level refusal was added to close.
 *
 * The mechanism. A field reports its own rejection message here; the builder
 * holds the registry and refuses to save while anything is in it. Deliberately
 * a registry rather than a check for these two fields in `validate()`:
 *
 *   - It is the field, not the builder, that knows whether its entry is
 *     acceptable, and the field already computes that message to render it.
 *     Restating the rule in the builder would be two copies to keep in step.
 *   - Any future `ActionEditor` field with the same shape (text held locally,
 *     action left empty when the entry is refused) becomes save-blocking by
 *     adding one `useReportedFieldError` call, with no change here or in
 *     `RuleBuilder`. `OrderNumberInput` and `max_streams_per_channel` are the
 *     same shape and are the likely next callers.
 *
 * What is reported is the message the field is RENDERING, keyed by that field's
 * DOM id, so the two cannot disagree: if the operator can see a red error, the
 * save is blocked, and if they cannot, it is not. A field that stops rendering
 * (its action changed type, or Channel Numbering went back to Auto) reports
 * `null` or unmounts, releasing its entry. Otherwise a refusal could outlive its
 * own field and block a save with nothing left on screen to fix.
 *
 * Consumers outside a provider (`ActionEditor` is also rendered standalone in
 * tests) get a no-op, so reporting is always safe to call.
 *
 * Tests: `ActionEditor.startingNumber.test.tsx`.
 */
import { createContext, useContext, useEffect } from 'react';

export interface ActionFieldValidityRegistry {
  /**
   * Record `fieldId` as carrying a refused entry, or clear it when `message`
   * is `null`. Called with whatever the field is currently rendering.
   */
  report: (fieldId: string, message: string | null) => void;
  /** Drop `fieldId` entirely: the field is gone, so it can refuse nothing. */
  release: (fieldId: string) => void;
}

export const ActionFieldValidityContext = createContext<ActionFieldValidityRegistry | null>(null);

/**
 * Report this field's current rejection message (or `null` when the entry is
 * acceptable, or when the field is not rendered at all) to the enclosing rule
 * form, which will refuse to save while any field is refusing its entry.
 *
 * `fieldId` must be the input's DOM id: the form uses it to focus the offending
 * field when a save is blocked, and it is unique per editor instance because it
 * is built from the editor's `useId`.
 */
export function useReportedFieldError(fieldId: string, message: string | null): void {
  const registry = useContext(ActionFieldValidityContext);
  useEffect(() => {
    if (!registry) return;
    registry.report(fieldId, message);
    // Runs before the next report and on unmount, so a field that disappears
    // never leaves a refusal behind it.
    return () => registry.release(fieldId);
  }, [registry, fieldId, message]);
}
