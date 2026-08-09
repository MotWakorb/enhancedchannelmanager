/**
 * CredentialReentryNotice — the restore-complete "re-enter these credentials"
 * action item (bead 6pilh).
 *
 * A STANDARD (redact-by-default) DBAS artifact carries the `***REDACTED***`
 * placeholder in place of every credential. The restore importers refuse to
 * write that placeholder into the destination, so the M3U account / EPG source
 * is created with its `password` left UNSET. Everything else about the restore
 * looks perfect: the outcome is `success`, every category count matches the
 * preview, and nothing in the per-entity breakdown hints that the instance will
 * not fetch a single stream until the operator types the credential back in.
 *
 * That is what this notice exists to say. Without it the failure surfaces only
 * as an authentication error in the provider log, long after the operator has
 * closed the restore screen believing it worked.
 *
 * Contract: `report.credentials_needing_reentry` (aggregate count) gates the
 * notice, and `report.credential_reentry_details` enumerates which entities and
 * which FIELD NAMES. Both are optional — a report produced before the fields
 * existed simply renders nothing. Same aggregate-plus-drill-down shape as
 * {@link LogoMissBanner}.
 *
 * On a DRY-RUN the copy switches to future tense: the preview of a redacted
 * artifact was previously byte-identical to a credential-bearing one, so this
 * is also the only pre-apply signal of which variant is loaded.
 *
 * Colorblind-safe (WCAG 1.4.1): meaning is carried by the icon and the words
 * "need"/"re-enter" in the copy, not by colour alone. `role="alert"` so
 * assistive tech announces it.
 */
import type { RestoreReport } from '../services/api';
import type { RestoreSummaryMode } from './RestoreCompleteSummary';
import './CredentialReentryNotice.css';

interface CredentialReentryNoticeProps {
  /** The restore report; `credentials_needing_reentry` gates the notice. */
  report: RestoreReport;
  /** `dry-run` switches the copy to future tense ("will need"). */
  mode: RestoreSummaryMode;
}

/** Plain-language count phrase — singular vs. plural, past vs. future tense. */
function countCopy(count: number, isDryRun: boolean): string {
  const isOne = count === 1;
  const noun = isOne ? 'account' : 'accounts';
  if (isDryRun) {
    // "will need" is invariant; only the noun inflects.
    return `${count} ${noun} will need credentials re-entered after this restore`;
  }
  // The applied phrasing has a finite verb and a pronoun, and both have to agree
  // with the subject: "1 account NEED credentials … before THEY will work" was
  // what a single-account restore rendered (drill run 2026-08-08-run17).
  const verb = isOne ? 'needs' : 'need';
  const pronoun = isOne ? 'it' : 'they';
  return `${count} ${noun} ${verb} credentials re-entered before ${pronoun} will work`;
}

export function CredentialReentryNotice({ report, mode }: CredentialReentryNoticeProps) {
  const count = report.credentials_needing_reentry ?? 0;

  // Never fire on a credential-bearing (encrypted + include_credentials)
  // restore, or on a report from a build that predates the field.
  if (count <= 0) {
    return null;
  }

  const details = report.credential_reentry_details ?? [];

  return (
    <div className="credential-reentry-notice" data-testid="credential-reentry-notice" role="alert">
      <span
        className="material-icons credential-reentry-icon"
        data-testid="credential-reentry-icon"
        aria-hidden="true"
      >
        key_off
      </span>
      <div className="credential-reentry-text">
        <span className="credential-reentry-title">{countCopy(count, mode === 'dry-run')}</span>
        <span className="credential-reentry-detail">
          This backup was created without credentials (the default), so they could not be
          restored. The entities below exist but will not authenticate until you enter the
          credential again in Dispatcharr.
        </span>
        {details.length > 0 && (
          <ul className="credential-reentry-list" data-testid="credential-reentry-list">
            {details.map((detail, index) => (
              <li
                className="credential-reentry-row"
                data-testid="credential-reentry-row"
                key={`${detail.entity_type}-${detail.destination_id ?? detail.source_export_id ?? index}`}
              >
                <span className="credential-reentry-label">
                  {detail.label?.trim() || 'Unnamed entity'}
                </span>
                {typeof detail.destination_id === 'number' && (
                  <span className="credential-reentry-id">(id {detail.destination_id})</span>
                )}
                <span className="credential-reentry-fields">
                  {' — '}
                  {detail.fields.join(', ')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
