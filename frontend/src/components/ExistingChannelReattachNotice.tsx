import type { ReattachPopulation, RestoreReport } from '../services/api';
import type { RestoreSummaryMode } from './RestoreCompleteSummary';
import './ExistingChannelReattachNotice.css';

/**
 * Says what a restore did (or would do) to channels it did NOT create (bead dfkbn).
 *
 * The number that matters here is `existing_channels`: channels the operator
 * already had, whose live guide link or logo was replaced by the archive's. That
 * is not recoverable: the rollback ledger compensates CREATES, so nothing undoes
 * a PATCH onto a channel the restore did not make.
 *
 * It renders on the DRY RUN too, from the same report fields, which is the whole
 * point: the operator has to be able to see the destructive count BEFORE the
 * apply, not read about it afterwards. A preview that could not predict this
 * would repeat bead dgnms, where a preview mispredicted a whole category.
 *
 * Silent when there is nothing to say (an empty destination creates every
 * channel, so both populations are zero and the mode never mattered).
 */
function PopulationRow({
  label,
  population,
  mode,
}: {
  label: string;
  population: ReattachPopulation;
  mode: RestoreSummaryMode;
}) {
  const preview = mode === 'dry-run';
  if (!population.existing_channels && !population.preserved_channels) return null;

  return (
    <li className="ecrn-row">
      <span className="ecrn-row-label">{label}</span>
      {population.existing_channels > 0 && (
        <span className="ecrn-row-detail ecrn-destructive">
          {preview ? 'Would replace on' : 'Replaced on'}{' '}
          <strong>{population.existing_channels}</strong>{' '}
          {population.existing_channels === 1 ? 'channel' : 'channels'} you already had
          <ChannelNames
            names={population.existing_channels_named}
            total={population.existing_channels}
          />
        </span>
      )}
      {population.preserved_channels > 0 && (
        <span className="ecrn-row-detail">
          {preview ? 'Would leave' : 'Left'} <strong>{population.preserved_channels}</strong>{' '}
          existing {population.preserved_channels === 1 ? 'channel' : 'channels'} untouched
          <ChannelNames
            names={population.preserved_channels_named}
            total={population.preserved_channels}
          />
        </span>
      )}
    </li>
  );
}

/**
 * The channel names behind a count, saying so when the list is short.
 *
 * The server caps each name list (REATTACH_NAMED_CHANNEL_CAP) so a
 * five-thousand-channel merge does not write ten thousand names into the task
 * record. Rendering the capped list with a full stop reads as the complete set,
 * which turns a deliberate cap into a wrong number on screen.
 */
function ChannelNames({ names, total }: { names: string[]; total: number }) {
  if (names.length === 0) return null;
  const remainder = total - names.length;
  return (
    <>
      : {names.join(', ')}
      {remainder > 0 && <>, and {remainder} more</>}
    </>
  );
}

export function ExistingChannelReattachNotice({
  report,
  mode,
}: {
  report: RestoreReport;
  mode?: RestoreSummaryMode;
}) {
  const effectiveMode: RestoreSummaryMode =
    mode ?? (report.is_dry_run ? 'dry-run' : 'applied');
  const preview = effectiveMode === 'dry-run';
  const epg = report.epg_link_reattach;
  const logo = report.logo_reattach;

  const touched = (epg?.existing_channels ?? 0) + (logo?.existing_channels ?? 0);
  const preserved = (epg?.preserved_channels ?? 0) + (logo?.preserved_channels ?? 0);
  if (!touched && !preserved) return null;

  return (
    <div
      className={`ecrn ${touched ? 'ecrn-warn' : ''}`}
      data-testid="existing-channel-reattach-notice"
      role={touched ? 'alert' : 'status'}
    >
      <span className="material-icons ecrn-icon" aria-hidden="true">
        {touched ? 'warning' : 'shield'}
      </span>
      <div className="ecrn-body">
        <span className="ecrn-title">
          {touched
            ? preview
              ? 'Channels you already have would be affected'
              : 'Channels you already had are affected'
            : preview
              ? 'Channels you already have would be left alone'
              : 'Channels you already had were left alone'}
        </span>
        <ul className="ecrn-list">
          {epg && <PopulationRow label="Guide data" population={epg} mode={effectiveMode} />}
          {logo && <PopulationRow label="Logos" population={logo} mode={effectiveMode} />}
        </ul>
        {touched > 0 && (
          <span className="ecrn-note">
            {preview
              ? // Names the control that IS on screen. The dry-run footer has a
                // "Back to options" button for exactly this; without it, this
                // sentence pointed at a picker the results step had unmounted.
                'Undoing a restore cannot bring these back. Use “Back to options” and choose “Keep their current guide data and logos” to leave them as they are.'
              : // Applied: there is nothing left to go back to, so say the only
                // thing that is actually true.
                'Undoing a restore cannot bring these back. To leave them as they are, run the restore again with “Keep their current guide data and logos” selected.'}
          </span>
        )}
      </div>
    </div>
  );
}
