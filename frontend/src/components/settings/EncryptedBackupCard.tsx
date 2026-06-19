import { useState, useCallback } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import './EncryptedBackupCard.css';

// Mirrors the API/primitive minimum (artifact_crypto.MIN_PASSPHRASE_LENGTH).
// The backend re-enforces this; the UI just gives early, friendly feedback.
const MIN_PASSPHRASE_LENGTH = 12;

/**
 * Create an opt-in, whole-artifact passphrase-encrypted DBAS backup
 * (ADR-012 D12 / bead u81kh). Triggers the `dbas_backup` task with the
 * encryption run-parameters; the artifact is written to the server's backups
 * directory (and uploaded to any configured cloud target).
 *
 * The lost-passphrase hard gate (checklist 34) is structural here: the
 * "Create" button stays disabled until the operator ticks
 * `acknowledge_unrecoverable` — not a tooltip. The backend independently
 * refuses a build without that ack and a < 12-char passphrase, so this is
 * defence-in-depth UX, not the only guard.
 */
export function EncryptedBackupCard() {
  const notifications = useNotifications();
  const [passphrase, setPassphrase] = useState('');
  const [confirmPassphrase, setConfirmPassphrase] = useState('');
  const [includeCredentials, setIncludeCredentials] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);

  const tooShort = passphrase.length > 0 && passphrase.length < MIN_PASSPHRASE_LENGTH;
  const mismatch = confirmPassphrase.length > 0 && passphrase !== confirmPassphrase;
  const canSubmit =
    passphrase.length >= MIN_PASSPHRASE_LENGTH &&
    passphrase === confirmPassphrase &&
    acknowledged &&
    !busy;

  const handleCreate = useCallback(async () => {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const result = await api.runTask('dbas_backup', undefined, {
        passphrase,
        include_credentials: includeCredentials,
        acknowledge_unrecoverable: acknowledged,
      });
      if (result.success) {
        notifications.success(result.message || 'Encrypted backup created', 'Encrypted Backup');
      } else {
        notifications.error(result.error || result.message || 'Encrypted backup failed', 'Encrypted Backup');
      }
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Encrypted backup failed',
        'Encrypted Backup',
      );
    } finally {
      // Always clear the passphrase from component state after the attempt —
      // it should never linger in memory longer than the request.
      setBusy(false);
      setPassphrase('');
      setConfirmPassphrase('');
      setAcknowledged(false);
    }
  }, [canSubmit, passphrase, includeCredentials, acknowledged, notifications]);

  return (
    <div className="backup-card encrypted-backup-card">
      <div className="backup-card-header">
        <span className="material-icons">enhanced_encryption</span>
        <h3>Encrypted Backup (Migration)</h3>
      </div>
      <p className="backup-card-description">
        Create a whole-artifact passphrase-encrypted backup. Use this when you need to carry
        credentials to another install, or to store a backup somewhere you do not control.
        The artifact is written to the server&apos;s backups directory.
      </p>

      <div className="ebc-field">
        <label htmlFor="ebc-passphrase">Passphrase</label>
        <input
          id="ebc-passphrase"
          type="password"
          autoComplete="new-password"
          value={passphrase}
          disabled={busy}
          onChange={(e) => setPassphrase(e.target.value)}
          placeholder={`At least ${MIN_PASSPHRASE_LENGTH} characters`}
        />
        {tooShort && (
          <span className="ebc-hint is-error">
            Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters.
          </span>
        )}
      </div>

      <div className="ebc-field">
        <label htmlFor="ebc-confirm">Confirm passphrase</label>
        <input
          id="ebc-confirm"
          type="password"
          autoComplete="new-password"
          value={confirmPassphrase}
          disabled={busy}
          onChange={(e) => setConfirmPassphrase(e.target.value)}
        />
        {mismatch && <span className="ebc-hint is-error">Passphrases do not match.</span>}
      </div>

      <label className="ebc-checkbox">
        <input
          type="checkbox"
          checked={includeCredentials}
          disabled={busy}
          onChange={(e) => setIncludeCredentials(e.target.checked)}
        />
        <span>
          <strong>Include credentials for migration.</strong> Carry the migration credentials
          (SMTP password, source credentials) inside the encrypted artifact so you do not have to
          re-enter them on the target. Leave unchecked to encrypt a still-redacted backup.
        </span>
      </label>

      <div className="ebc-unrecoverable-warning">
        <span className="material-icons">report</span>
        <span>
          If you lose this passphrase, the backup is <strong>permanently unrecoverable</strong>.
          There is no recovery and no backdoor. Store the passphrase separately from the artifact.
        </span>
      </div>

      <label className="ebc-checkbox ebc-ack">
        <input
          type="checkbox"
          checked={acknowledged}
          disabled={busy}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        <span>I understand that a lost passphrase means this backup cannot be recovered.</span>
      </label>

      {busy ? (
        <div className="backup-loading">
          <span className="material-icons spinning">sync</span>
          Creating encrypted backup...
        </div>
      ) : (
        <button className="btn-primary backup-download-btn" onClick={handleCreate} disabled={!canSubmit}>
          <span className="material-icons">lock</span>
          Create Encrypted Backup
        </button>
      )}
    </div>
  );
}
