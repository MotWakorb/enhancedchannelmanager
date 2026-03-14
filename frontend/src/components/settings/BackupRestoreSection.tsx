import { useState, useRef } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import './BackupRestoreSection.css';

interface Props {
  isAdmin: boolean;
}

export function BackupRestoreSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [downloading, setDownloading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<api.RestoreResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isAdmin) {
    return (
      <div className="backup-restore-no-access">
        <span className="material-icons">lock</span>
        Only administrators can manage backups.
      </div>
    );
  }

  const handleDownloadBackup = async () => {
    setDownloading(true);
    try {
      const response = await fetch(api.getBackupDownloadUrl());
      if (!response.ok) {
        throw new Error('Failed to create backup');
      }
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition');
      const filename = disposition?.match(/filename="(.+)"/)?.[1] || 'ecm-backup.zip';

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      notifications.success('Backup downloaded successfully');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to create backup', 'Backup Failed');
    } finally {
      setDownloading(false);
    }
  };

  const handleRestore = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      notifications.error('Please select a backup file', 'No File Selected');
      return;
    }

    if (!file.name.endsWith('.zip')) {
      notifications.error('Please select a .zip backup file', 'Invalid File');
      return;
    }

    setRestoring(true);
    setRestoreResult(null);

    try {
      const result = await api.restoreBackup(file);
      setRestoreResult(result);
      notifications.success(`Restored ${result.restored_files.length} files from backup`);

      // Reload after short delay so user can see the result
      setTimeout(() => {
        window.location.reload();
      }, 3000);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Restore failed', 'Restore Failed');
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div className="backup-restore-section">
      <h2 className="settings-page-header">Backup & Restore</h2>

      {/* Create Backup */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">cloud_download</span>
          <h3>Create Backup</h3>
        </div>
        <p className="backup-card-description">
          Download a backup of all ECM configuration including settings, database, uploaded logos, TLS certificates, and M3U files.
        </p>
        {downloading ? (
          <div className="backup-loading">
            <span className="material-icons">sync</span>
            Creating backup...
          </div>
        ) : (
          <button className="btn-primary backup-download-btn" onClick={handleDownloadBackup}>
            <span className="material-icons">download</span>
            Download Backup
          </button>
        )}
      </div>

      {/* Restore */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">cloud_upload</span>
          <h3>Restore from Backup</h3>
        </div>
        <p className="backup-card-description">
          Upload a previously created ECM backup to restore your configuration.
        </p>

        <div className="restore-warning">
          <span className="material-icons">warning</span>
          <span>
            Restoring from a backup will replace all current settings, database records, and uploaded files.
            The page will reload automatically after restore completes.
          </span>
        </div>

        <div className="restore-file-input">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            disabled={restoring}
          />
          {restoring ? (
            <div className="backup-loading">
              <span className="material-icons">sync</span>
              Restoring...
            </div>
          ) : (
            <button className="btn-primary" onClick={handleRestore}>
              Restore
            </button>
          )}
        </div>

        {restoreResult && (
          <div className="restore-result">
            <div className="restore-result-header">
              <span className="material-icons">check_circle</span>
              Restore Complete
            </div>
            <div className="restore-result-details">
              <strong>Backup version:</strong> {restoreResult.backup_version}<br />
              <strong>Backup date:</strong> {restoreResult.backup_date}<br />
              <strong>Files restored:</strong> {restoreResult.restored_files.length}<br />
              Reloading page...
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
