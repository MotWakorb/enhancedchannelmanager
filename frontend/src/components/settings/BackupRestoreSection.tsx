import { useState, useEffect, useRef, useCallback } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { BackupRestoreModal } from '../BackupRestoreModal';
import { DbasRestoreModal } from '../DbasRestoreModal';
import { DbasRestoreSavedModal } from '../DbasRestoreSavedModal';
import { TypeToConfirmDialog } from '../TypeToConfirmDialog';
import { EncryptedBackupCard } from './EncryptedBackupCard';
import { SyncTargetsCard } from './SyncTargetsCard';
import { CloudTargetsCard } from './CloudTargetsCard';
import { BackupScheduleBanner } from './BackupScheduleBanner';
import { OutboundPolicyCard } from './OutboundPolicyCard';
import { getDateLocale } from '../../utils/formatting';
import './BackupRestoreSection.css';

interface Props {
  isAdmin: boolean;
}

export function BackupRestoreSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [downloading, setDownloading] = useState(false);
  const [exportingYaml, setExportingYaml] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<api.RestoreResult | null>(null);
  const [showRestoreModal, setShowRestoreModal] = useState(false);
  const [showDbasRestoreModal, setShowDbasRestoreModal] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Export section selection
  const [exportSections, setExportSections] = useState<{key: string; label: string}[]>([]);
  const [selectedExportSections, setSelectedExportSections] = useState<Set<string>>(new Set());

  // Saved backups
  const [savedBackups, setSavedBackups] = useState<api.SavedBackup[]>([]);
  const [loadingSaved, setLoadingSaved] = useState(false);
  const [deletingFile, setDeletingFile] = useState<string | null>(null);

  // Restore-from-saved (bead rzhid): legacy full-ZIP restore-saved confirm
  // dialog target, and DBAS-format restore-dbas-saved modal target. GET
  // /backup/saved can't tell the two .zip formats apart (same naming
  // convention) — both actions are offered on every saved .zip row, and the
  // operator picks the one matching how the file was produced.
  const [restoringLegacySaved, setRestoringLegacySaved] = useState<string | null>(null);
  const [legacySavedBusy, setLegacySavedBusy] = useState(false);
  const [dbasSavedTarget, setDbasSavedTarget] = useState<string | null>(null);

  const loadSavedBackups = useCallback(async () => {
    setLoadingSaved(true);
    try {
      const backups = await api.listSavedBackups();
      setSavedBackups(backups);
    } catch {
      // silent
    } finally {
      setLoadingSaved(false);
    }
  }, []);

  // Load export sections and saved backups on mount
  useEffect(() => {
    if (!isAdmin) return;
    api.getExportSections().then((sections) => {
      setExportSections(sections);
      setSelectedExportSections(new Set(sections.map(s => s.key)));
    }).catch(() => {});
    loadSavedBackups();
  }, [isAdmin, loadSavedBackups]);

  if (!isAdmin) {
    return (
      <div className="backup-restore-no-access">
        <span className="material-icons">lock</span>
        Only administrators can manage backups.
      </div>
    );
  }

  const allExportSelected = selectedExportSections.size === exportSections.length;

  const toggleExportSection = (key: string) => {
    setSelectedExportSections(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

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

  const handleExportYaml = async () => {
    setExportingYaml(true);
    try {
      const sections = allExportSelected ? undefined : Array.from(selectedExportSections);
      const blob = await api.exportBackup(sections);
      const now = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `ecm-export-${now}.yaml`;

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      notifications.success('YAML export downloaded successfully');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Export failed', 'Export Failed');
    } finally {
      setExportingYaml(false);
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

      setTimeout(() => {
        window.location.reload();
      }, 3000);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Restore failed', 'Restore Failed');
    } finally {
      setRestoring(false);
    }
  };

  const handleConfirmLegacyRestore = async () => {
    if (!restoringLegacySaved) return;
    setLegacySavedBusy(true);
    try {
      const result = await api.restoreSavedBackup(restoringLegacySaved);
      notifications.success(`Restored ${result.restored_files.length} files from ${restoringLegacySaved}`);
      setRestoringLegacySaved(null);
      setTimeout(() => {
        window.location.reload();
      }, 3000);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Restore failed', 'Restore Failed');
    } finally {
      setLegacySavedBusy(false);
    }
  };

  const handleDeleteSaved = async (filename: string) => {
    setDeletingFile(filename);
    try {
      await api.deleteSavedBackup(filename);
      setSavedBackups(prev => prev.filter(b => b.filename !== filename));
      notifications.success('Backup deleted');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Delete failed', 'Delete Failed');
    } finally {
      setDeletingFile(null);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="backup-restore-section">
      <h2 className="settings-page-header">Backup & Restore</h2>

      {/* One-time "Backups are not scheduled yet" setup nudge (bead ikv8z).
          Scheduled DBAS backup ships OFF by default, so surface the unscheduled
          state prominently to prevent an operator silently keeping zero backups. */}
      <BackupScheduleBanner />

      {/* Where backups can be sent (relocated from the removed Administration
          → Security page, bead 09x38.12; original setting is bead nngkg). */}
      <OutboundPolicyCard />

      {/* YAML Export (config only) */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">description</span>
          <h3>Export Configuration (YAML)</h3>
        </div>
        <p className="backup-card-description">
          Export ECM configuration as a single YAML file. Choose which sections to include.
        </p>
        <div className="backup-sensitive-warning">
          <span className="material-icons">info</span>
          <span>Sensitive data (passwords, API keys) are redacted in the export.</span>
        </div>

        {exportSections.length > 0 && (
          <>
            <div className="export-section-controls">
              <span className="export-section-label">Sections to export:</span>
              <div className="brm-select-actions">
                <button
                  className="brm-link-btn"
                  onClick={() => setSelectedExportSections(new Set(exportSections.map(s => s.key)))}
                >
                  Select all
                </button>
                <button className="brm-link-btn" onClick={() => setSelectedExportSections(new Set())}>
                  Select none
                </button>
              </div>
            </div>
            <div className="export-section-list">
              {exportSections.map((section) => (
                <label key={section.key} className="brm-section-item">
                  <input
                    type="checkbox"
                    checked={selectedExportSections.has(section.key)}
                    onChange={() => toggleExportSection(section.key)}
                  />
                  <span className="brm-section-name">{section.label}</span>
                </label>
              ))}
            </div>
          </>
        )}

        {exportingYaml ? (
          <div className="backup-loading">
            <span className="material-icons spinning">sync</span>
            Exporting...
          </div>
        ) : (
          <button
            className="btn-primary backup-download-btn"
            onClick={handleExportYaml}
            disabled={selectedExportSections.size === 0}
          >
            <span className="material-icons">download</span>
            Export YAML
            {!allExportSelected && selectedExportSections.size > 0 && (
              <span className="export-count-badge">
                {selectedExportSections.size}/{exportSections.length}
              </span>
            )}
          </button>
        )}
      </div>

      {/* Selective Restore from YAML */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">settings_backup_restore</span>
          <h3>Restore from YAML Export</h3>
        </div>
        <p className="backup-card-description">
          Upload a previously exported YAML file and choose which sections to restore.
          Each section is restored independently — you can pick just what you need.
        </p>
        <button className="btn-primary" onClick={() => setShowRestoreModal(true)}>
          <span className="material-icons">upload_file</span>
          Restore from YAML...
        </button>
      </div>

      {/* Saved Backups */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">folder</span>
          <h3>Saved Backups</h3>
        </div>
        <p className="backup-card-description">
          YAML backups saved on the server by the scheduled backup task.
        </p>
        {loadingSaved ? (
          <div className="backup-loading">
            <span className="material-icons spinning">sync</span>
            Loading...
          </div>
        ) : savedBackups.length === 0 ? (
          <div className="saved-backups-empty">
            No saved backups. Enable the YAML Backup scheduled task to create automatic backups.
          </div>
        ) : (
          <div className="saved-backups-list">
            {savedBackups.map((backup) => (
              <div key={backup.filename} className="saved-backup-item">
                <div className="saved-backup-info">
                  <span className="material-icons">description</span>
                  <div>
                    <div className="saved-backup-name">{backup.filename}</div>
                    <div className="saved-backup-meta">
                      {new Date(backup.created_at).toLocaleString(getDateLocale())} &middot; {formatBytes(backup.size_bytes)}
                    </div>
                  </div>
                </div>
                <div className="saved-backup-actions">
                  {backup.type === 'zip' && (
                    <>
                      <button
                        className="btn-secondary saved-backup-btn"
                        onClick={() => setRestoringLegacySaved(backup.filename)}
                        aria-label="Restore as legacy full backup"
                        title="Restore as legacy full backup (pre-v0.18.0 format)"
                      >
                        <span className="material-icons" aria-hidden="true">settings_backup_restore</span>
                      </button>
                      <button
                        className="btn-secondary saved-backup-btn"
                        onClick={() => setDbasSavedTarget(backup.filename)}
                        aria-label="Restore as DBAS backup"
                        title="Restore as DBAS backup (v0.18.0+ format, dry-run first)"
                      >
                        <span className="material-icons" aria-hidden="true">restore</span>
                      </button>
                    </>
                  )}
                  <a
                    href={api.getSavedBackupDownloadUrl(backup.filename)}
                    className="btn-secondary saved-backup-btn"
                    download
                  >
                    <span className="material-icons">download</span>
                  </a>
                  <button
                    className="btn-secondary saved-backup-btn saved-backup-delete"
                    onClick={() => handleDeleteSaved(backup.filename)}
                    disabled={deletingFile === backup.filename}
                    aria-label={deletingFile === backup.filename ? 'Deleting backup…' : 'Delete backup'}
                    title={deletingFile === backup.filename ? 'Deleting backup…' : 'Delete backup'}
                  >
                    <span className="material-icons" aria-hidden="true">
                      {deletingFile === backup.filename ? 'hourglass_empty' : 'delete'}
                    </span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Separator */}
      <div className="backup-section-divider">
        <span>Full System Backup</span>
      </div>

      {/* Full ZIP Backup */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">cloud_download</span>
          <h3>Create Full Backup</h3>
        </div>
        <p className="backup-card-description">
          Download a full backup including settings, database, uploaded logos, TLS certificates, and M3U files.
        </p>
        <div className="backup-sensitive-warning warning-level">
          <span className="material-icons">warning</span>
          <span>This backup contains sensitive data including passwords and certificates.</span>
        </div>
        {downloading ? (
          <div className="backup-loading">
            <span className="material-icons spinning">sync</span>
            Creating backup...
          </div>
        ) : (
          <button className="btn-primary backup-download-btn" onClick={handleDownloadBackup}>
            <span className="material-icons">download</span>
            Download Full Backup
          </button>
        )}
      </div>

      {/* Encrypted Backup (Migration) — ADR-012 D12 / u81kh */}
      <EncryptedBackupCard />

      {/* Cross-Instance Sync — epic i39wu / nnl9s */}
      <SyncTargetsCard />

      {/* Cloud upload destinations for DBAS backup — relocated from the removed Export tab (vrrxv / 1w428) */}
      <div className="backup-card">
        <CloudTargetsCard />
      </div>

      {/* DBAS artifact restore (.zip, incl. encrypted) — bead 7euap */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">restore</span>
          <h3>Restore DBAS Backup</h3>
        </div>
        <p className="backup-card-description">
          Restore a v0.18.0 backup artifact (.zip) — the format produced by scheduled backups and
          the Encrypted Backup card. Preview the changes first (dry run), then apply. Encrypted
          artifacts prompt for the passphrase.
        </p>
        <button className="btn-primary" onClick={() => setShowDbasRestoreModal(true)}>
          <span className="material-icons">upload_file</span>
          Restore from artifact...
        </button>
      </div>

      {/* Full ZIP Restore */}
      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">cloud_upload</span>
          <h3>Restore Full Backup</h3>
        </div>
        <p className="backup-card-description">
          Upload a previously created ECM backup (.zip) to restore your entire configuration.
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
              <span className="material-icons spinning">sync</span>
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

      {showRestoreModal && (
        <BackupRestoreModal onClose={() => setShowRestoreModal(false)} />
      )}

      {showDbasRestoreModal && (
        <DbasRestoreModal onClose={() => setShowDbasRestoreModal(false)} />
      )}

      {restoringLegacySaved && (
        <TypeToConfirmDialog
          title="Restore Saved Backup"
          message={
            <>
              This replaces all current settings, database records, and uploaded files with the
              contents of <strong>{restoringLegacySaved}</strong>. The page reloads automatically
              once the restore completes. This cannot be undone.
            </>
          }
          confirmText={restoringLegacySaved}
          confirmLabel="Restore this backup"
          busy={legacySavedBusy}
          onCancel={() => setRestoringLegacySaved(null)}
          onConfirm={handleConfirmLegacyRestore}
        />
      )}

      {dbasSavedTarget && (
        <DbasRestoreSavedModal
          filename={dbasSavedTarget}
          onClose={() => setDbasSavedTarget(null)}
        />
      )}
    </div>
  );
}
