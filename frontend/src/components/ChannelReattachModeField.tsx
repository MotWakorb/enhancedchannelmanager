import type { ChannelReattachMode } from '../services/api';

/**
 * Picks what a DBAS restore does to channels it did NOT create (bead dfkbn).
 *
 * Shared by both restore entry points (DbasRestoreModal, the upload path, and
 * DbasRestoreSavedModal, the saved-file path) so the two can never offer the
 * operator different wording or a different default for the same decision.
 *
 * The copy deliberately never says "preserve" or "overwrite": those are the wire
 * values, and an operator deciding whether to keep their own guide links should
 * not have to map an enum onto a consequence. The visible text is what happens
 * to their channels.
 *
 * "grouping" joined "guide data and logos" in bead r1ei7, when the mode was
 * widened to cover a channel's CHANNEL GROUP as well. It is the plain word for
 * "which group each channel is in", and it avoids colliding with channel
 * PROFILES, which the surrounding UI also calls a kind of grouping. The label
 * is quoted verbatim by ExistingChannelReattachNotice's remedy copy — change it
 * in both places or that sentence points at a control that does not exist.
 *
 * Default is keep-mine. In the disaster-recovery case the target is empty, every
 * channel is created, and the two options do exactly the same thing, so the safe
 * default costs that case nothing. It only differs when restoring into a live,
 * populated install, which is precisely where the destructive option should be
 * something the operator reaches for on purpose.
 */
export function ChannelReattachModeField({
  value,
  onChange,
  disabled = false,
  idPrefix = 'crm',
}: {
  value: ChannelReattachMode;
  onChange: (mode: ChannelReattachMode) => void;
  disabled?: boolean;
  idPrefix?: string;
}) {
  return (
    // The <legend> is NOT a normal flex item in Chromium, so the flex column
    // that lays the options out lives on an inner <div> rather than on the
    // <fieldset> itself. jsdom cannot see layout, so this is a correctness
    // decision the test suite structurally cannot make for us.
    <fieldset className="crm-fieldset" disabled={disabled}>
      <legend className="crm-legend">Channels that already exist here</legend>
      <p className="crm-help">
        Some channels in this backup may already exist on this install. This does not
        affect channels the restore creates.
      </p>
      <div className="dbr-mode crm-options">
      <label
        className={`dbr-mode-option ${value === 'preserve' ? 'is-active' : ''}`}
        htmlFor={`${idPrefix}-preserve`}
      >
        <input
          id={`${idPrefix}-preserve`}
          type="radio"
          name={`${idPrefix}-channel-reattach-mode`}
          checked={value === 'preserve'}
          onChange={() => onChange('preserve')}
        />
        <span>
          <strong>Keep their current guide data, logos, and grouping</strong>
          <span className="dbr-mode-hint">
            Leaves channels you already have exactly as they are. The results still
            list any channel the backup puts in a different group. Recommended.
          </span>
        </span>
      </label>
      <label
        className={`dbr-mode-option ${value === 'overwrite' ? 'is-active' : ''}`}
        htmlFor={`${idPrefix}-overwrite`}
      >
        <input
          id={`${idPrefix}-overwrite`}
          type="radio"
          name={`${idPrefix}-channel-reattach-mode`}
          checked={value === 'overwrite'}
          onChange={() => onChange('overwrite')}
        />
        <span>
          <strong>Replace their guide data, logos, and grouping with the backup&apos;s</strong>
          <span className="dbr-mode-hint">
            Any guide link or logo you set on those channels yourself is lost, and
            channels move into the groups the backup puts them in. Undo cannot bring
            any of it back.
          </span>
        </span>
      </label>
      </div>
    </fieldset>
  );
}
