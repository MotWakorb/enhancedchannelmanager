import { memo } from 'react';

export interface EditModeRestoredBadgeProps {
  /**
   * When the restored ledger was last written, or null when this session's
   * staged work was made here and now.
   */
  restoredFrom: number | null;
}

/**
 * Standing marker that this Edit Mode session's staged work came back from a
 * session that died (epic enhancedchannelmanager-r93hq).
 *
 * The restore dialog is the loud signal, but it is a moment. The header's
 * change count reads identically for restored work and for work just made, and
 * the difference decides whether an operator trusts Apply — so the marker
 * stays for as long as the restored session does.
 */
export const EditModeRestoredBadge = memo(function EditModeRestoredBadge({
  restoredFrom,
}: EditModeRestoredBadgeProps) {
  if (restoredFrom === null) return null;

  const staged = new Date(restoredFrom);
  return (
    <span
      className="edit-mode-restored-badge"
      data-testid="edit-mode-restored-badge"
      title={`Restored from your previous session. Last staged ${staged.toLocaleString()}.`}
    >
      <span className="material-icons" aria-hidden="true">history</span>
      Restored
    </span>
  );
});
