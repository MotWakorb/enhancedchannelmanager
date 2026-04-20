import { useState, useCallback } from 'react';
import { ModalOverlay } from './ModalOverlay';
import * as api from '../services/api';
import type { BackupValidation, BackupRestoreResult } from '../services/api';
import './ModalBase.css';
import './BackupRestoreModal.css';

interface BackupRestoreModalProps {
  onClose: () => void;
}

type Step = 'upload' | 'select' | 'restoring' | 'results';

export function BackupRestoreModal({ onClose }: BackupRestoreModalProps) {
  const [step, setStep] = useState<Step>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [validation, setValidation] = useState<BackupValidation | null>(null);
  const [selectedSections, setSelectedSections] = useState<Set<string>>(new Set());
  const [restoreResult, setRestoreResult] = useState<BackupRestoreResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleFile = useCallback(async (selectedFile: File) => {
    if (!selectedFile.name.match(/\.ya?ml$/i)) {
      setError('Please select a .yaml or .yml file');
      return;
    }

    setFile(selectedFile);
    setError(null);

    try {
      const result = await api.validateBackup(selectedFile);
      setValidation(result);
      // Pre-select all available sections
      const available = new Set(
        result.sections.filter((s) => s.available).map((s) => s.key)
      );
      setSelectedSections(available);
      setStep('select');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to validate file');
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDragging(false);
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile) handleFile(droppedFile);
    },
    [handleFile]
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragging(false);
  }, []);

  const handlePickFile = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.yaml,.yml';
    input.onchange = () => {
      const f = input.files?.[0];
      if (f) handleFile(f);
    };
    input.click();
  }, [handleFile]);

  const toggleSection = useCallback((key: string) => {
    setSelectedSections((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    if (!validation) return;
    setSelectedSections(
      new Set(validation.sections.filter((s) => s.available).map((s) => s.key))
    );
  }, [validation]);

  const selectNone = useCallback(() => {
    setSelectedSections(new Set());
  }, []);

  const handleRestore = useCallback(async () => {
    if (!file || selectedSections.size === 0) return;
    setStep('restoring');
    setError(null);

    try {
      const result = await api.restoreBackupYaml(file, Array.from(selectedSections));
      setRestoreResult(result);
      setStep('results');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Restore failed');
      setStep('select');
    }
  }, [file, selectedSections]);

  const canClose = step !== 'restoring';

  return (
    <ModalOverlay onClose={canClose ? onClose : () => {}}>
      <div className="modal-container modal-md backup-restore-modal-container">
        <div className="modal-header">
          <h3 className="modal-title">Restore from YAML Export</h3>
          {canClose && (
            <button className="modal-close-btn" onClick={onClose}>
              <span className="material-icons">close</span>
            </button>
          )}
        </div>

        <div className="modal-body">
          {error && (
            <div className="modal-error-banner">
              <span className="material-icons">error</span>
              {error}
            </div>
          )}

          {step === 'upload' && (
            <div
              className={`brm-dropzone ${isDragging ? 'is-dragging' : ''}`}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onClick={handlePickFile}
            >
              <span className="material-icons brm-dropzone-icon">upload_file</span>
              <p className="brm-dropzone-text">
                Drag & drop a YAML export file here, or click to browse
              </p>
              <p className="brm-dropzone-hint">.yaml or .yml files</p>
            </div>
          )}

          {step === 'select' && validation && (
            <>
              <div className="brm-file-info">
                <span className="material-icons">description</span>
                <div>
                  <div className="brm-file-name">{file?.name}</div>
                  <div className="brm-file-meta">
                    ECM v{validation.version}
                    {validation.exported_at && (
                      <> &middot; {new Date(validation.exported_at).toLocaleString()}</>
                    )}
                  </div>
                </div>
              </div>

              <div className="brm-section-controls">
                <span className="brm-section-label">Select sections to restore:</span>
                <div className="brm-select-actions">
                  <button className="brm-link-btn" onClick={selectAll}>
                    Select all
                  </button>
                  <button className="brm-link-btn" onClick={selectNone}>
                    Select none
                  </button>
                </div>
              </div>

              <div className="brm-section-list">
                {validation.sections.map((section) => (
                  <label
                    key={section.key}
                    className={`brm-section-item ${!section.available ? 'is-disabled' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedSections.has(section.key)}
                      disabled={!section.available}
                      onChange={() => toggleSection(section.key)}
                    />
                    <span className="brm-section-name">{section.label}</span>
                    <span className="brm-section-count">
                      {section.available ? `${section.item_count} items` : 'empty'}
                    </span>
                  </label>
                ))}
              </div>
            </>
          )}

          {step === 'restoring' && (
            <div className="modal-loading">
              <span className="material-icons">sync</span>
              <p>Restoring selected sections...</p>
            </div>
          )}

          {step === 'results' && restoreResult && (
            <div className="brm-results">
              <div
                className={`brm-results-banner ${restoreResult.success ? 'is-success' : 'is-partial'}`}
              >
                <span className="material-icons">
                  {restoreResult.success ? 'check_circle' : 'warning'}
                </span>
                <span>
                  {restoreResult.success
                    ? `Successfully restored ${restoreResult.sections_restored.length} section(s)`
                    : `Restored ${restoreResult.sections_restored.length} section(s), ${restoreResult.sections_failed.length} failed`}
                </span>
              </div>

              {restoreResult.sections_restored.length > 0 && (
                <div className="brm-result-group">
                  <h4>Restored</h4>
                  {restoreResult.sections_restored.map((key) => (
                    <div key={key} className="brm-result-item is-success">
                      <span className="material-icons">check</span>
                      {SECTION_LABELS[key] || key}
                    </div>
                  ))}
                </div>
              )}

              {restoreResult.sections_failed.length > 0 && (
                <div className="brm-result-group">
                  <h4>Failed</h4>
                  {restoreResult.sections_failed.map((key) => (
                    <div key={key} className="brm-result-item is-error">
                      <span className="material-icons">close</span>
                      {SECTION_LABELS[key] || key}
                    </div>
                  ))}
                </div>
              )}

              {restoreResult.warnings.length > 0 && (
                <div className="brm-result-group">
                  <h4>Warnings</h4>
                  {restoreResult.warnings.map((w, i) => (
                    <div key={i} className="brm-result-item is-warning">
                      <span className="material-icons">info</span>
                      {w}
                    </div>
                  ))}
                </div>
              )}

              {restoreResult.errors.length > 0 && (
                <div className="brm-result-group">
                  <h4>Errors</h4>
                  {restoreResult.errors.map((e, i) => (
                    <div key={i} className="brm-result-item is-error">
                      <span className="material-icons">error</span>
                      {e}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          {step === 'select' && (
            <>
              <button className="modal-btn-secondary" onClick={onClose}>
                Cancel
              </button>
              <button
                className="modal-btn-primary"
                disabled={selectedSections.size === 0}
                onClick={handleRestore}
              >
                Restore {selectedSections.size} section{selectedSections.size !== 1 ? 's' : ''}
              </button>
            </>
          )}
          {step === 'results' && (
            <button className="modal-btn-primary" onClick={onClose}>
              Done
            </button>
          )}
          {step === 'upload' && (
            <button className="modal-btn-secondary" onClick={onClose}>
              Cancel
            </button>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
}

const SECTION_LABELS: Record<string, string> = {
  settings: 'Settings',
  scheduled_tasks: 'Scheduled Tasks',
  task_schedules: 'Task Schedules',
  normalization_rule_groups: 'Normalization Rules',
  tag_groups: 'Tag Groups',
  auto_creation_rules: 'Auto-Creation Rules',
  ffmpeg_profiles: 'FFmpeg Profiles',
  dummy_epg_profiles: 'Dummy EPG Profiles',
};
