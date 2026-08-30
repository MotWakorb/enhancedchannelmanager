import { useState, useCallback } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { invalidateServerData } from '../../hooks/useServerDataInvalidation';

/**
 * Take the standard DBAS backup artifact in one click (bead
 * enhancedchannelmanager-pui76).
 *
 * The capability was never missing — `dbas_backup` has been registered and
 * runnable from Settings → Scheduled Tasks → Run Now, and the sibling
 * EncryptedBackupCard on this same page already calls it. What was missing was
 * a control on the page where backups live that produces the PLAIN artifact:
 * of the seven action buttons here, the only one that produced a DBAS artifact
 * demanded a 12-character passphrase and an unrecoverable-loss acknowledgement,
 * while the page's own "Which one do I need?" helper recommends the DBAS format
 * for disaster recovery.
 *
 * NAMED FOR WHAT IT PRODUCES, not for the action (PO decision D3 on the bead).
 * After bead gi4zn the standard artifact is credential-redacted: no provider
 * credentials, no provider usernames, no ECM accounts, no journal history or
 * telemetry. Credential-redacted, NOT sanitised — free text the operator typed
 * is carried through untouched, which is why the description below stops short
 * of calling the artifact safe to hand to anyone (see the comment on it).
 * A card called "Backup" with a button called "Back Up Now" would
 * let an operator believe they hold a complete backup and discover otherwise at
 * the worst possible moment — a restore. So the card is named for its contents
 * and states the omission above the button rather than in a doc.
 *
 * `runTask` is called with NO parameters on purpose. The encryption transients
 * (`passphrase`, `include_credentials`, `acknowledge_unrecoverable`) are what
 * turn this into a different artifact; their absence is what makes this the
 * standard one the copy describes.
 */
export function ConfigurationBackupCard() {
  const notifications = useNotifications();
  const [busy, setBusy] = useState(false);

  const handleCreate = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.runTask('dbas_backup');
      if (result.success) {
        // Saved Backups sits above this card and fetched itself on mount, so
        // without this it keeps showing the previous artifact and the operator
        // concludes the backup failed and takes another (bead 5z7c9,
        // instance 3 — the same seam the encrypted card had to fix).
        invalidateServerData('saved-backups');
        notifications.success(
          result.message || 'Configuration backup created',
          'Configuration Backup',
        );
      } else {
        notifications.error(
          result.error || result.message || 'Configuration backup failed',
          'Configuration Backup',
        );
      }
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Configuration backup failed',
        'Configuration Backup',
      );
    } finally {
      setBusy(false);
    }
  }, [notifications]);

  return (
    <div className="backup-card" data-testid="configuration-backup-card">
      <div className="backup-card-header">
        <span className="material-icons">backup</span>
        <h3>Configuration Backup</h3>
      </div>
      {/* WHAT THIS PARAGRAPH IS ALLOWED TO CLAIM (bead
          enhancedchannelmanager-pui76, review round 2). It used to end "so it
          is safe to attach to a support ticket or copy to a machine you do not
          control" — an absolute the producer does not support. The artifact's
          redaction is credential-shaped: it replaces credential- and
          identity-named keys, and credentials carried inside URL values. Free
          text is not touched, and the artifact carries plenty of it — the
          `description` on every auto-creation rule is literally "user notes
          about this rule" (backend/models.py). An operator who put a support
          token, a customer's email or an internal hostname in a rule note
          ships it. So the copy states the guarantee it has (no working
          credentials, no accounts) and hands the disclosure judgement back to
          the operator, who is the only party who knows what they typed.
          Redacting the notes instead would destroy legitimate content. */}
      <p className="backup-card-description">
        Save this instance&apos;s configuration to the server as a .zip artifact — the same
        artifact a scheduled backup produces. It appears in Saved Backups above when it finishes.
        Provider sign-ins, credentials embedded in URLs, and ECM accounts are replaced with
        placeholders. Other text you entered — including source names and the notes on your rules
        — may still travel verbatim, so check what is in the artifact before sending it to anyone
        outside your control.
      </p>
      <div className="backup-sensitive-warning">
        <span className="material-icons">info</span>
        <span>
          <strong>Configuration only — no credentials, no accounts.</strong> Your M3U and EPG
          provider sign-ins are not in this file, and neither are your ECM login accounts.
          Restoring it brings your configuration back but not your access: on a fresh install you
          re-enter every provider credential by hand. To carry them to another install, use{' '}
          <strong>Encrypted Backup (Migration)</strong> below with{' '}
          <strong>Include credentials</strong>.
        </span>
      </div>
      {busy ? (
        <div className="backup-loading">
          <span className="material-icons spinning">sync</span>
          Building configuration backup...
        </div>
      ) : (
        <button className="btn-primary backup-download-btn" onClick={handleCreate}>
          <span className="material-icons">backup</span>
          Create Configuration Backup
        </button>
      )}
    </div>
  );
}
