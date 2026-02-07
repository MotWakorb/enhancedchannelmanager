import { useState, useCallback, memo } from 'react';
import { importChannelsFromCSV, parseCSVPreview, CSVImportResult, CSVPreviewResult } from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './CSVImportModal.css';

interface CSVImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

type ImportState = 'idle' | 'previewing' | 'importing' | 'success' | 'error';

export const CSVImportModal = memo(function CSVImportModal({
  isOpen,
  onClose,
  onSuccess,
}: CSVImportModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CSVPreviewResult | null>(null);
  const [importResult, setImportResult] = useState<CSVImportResult | null>(null);
  const [importState, setImportState] = useState<ImportState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const resetState = useCallback(() => {
    setFile(null);
    setPreview(null);
    setImportResult(null);
    setImportState('idle');
    setError(null);
    setIsDragging(false);
  }, []);

  const handleClose = useCallback(() => {
    resetState();
    onClose();
  }, [resetState, onClose]);

  const handleFile = useCallback(async (selectedFile: File) => {
    setFile(selectedFile);
    setError(null);
    setImportState('previewing');

    try {
      const content = await selectedFile.text();
      const result = await parseCSVPreview(content);
      setPreview(result);
      setImportState('idle');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to parse CSV');
      setImportState('error');
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile && droppedFile.type === 'text/csv') {
      handleFile(droppedFile);
    }
  }, [handleFile]);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      handleFile(selectedFile);
    }
  }, [handleFile]);

  const handleImport = useCallback(async () => {
    if (!file) return;

    setImportState('importing');
    setError(null);

    try {
      console.log('[CSVImportModal] Starting import...');
      const result = await importChannelsFromCSV(file);
      console.log('[CSVImportModal] Import result:', result);
      setImportResult(result);
      setImportState('success');
      // Refresh channels if any were created, regardless of errors
      if (result.channels_created > 0 || result.groups_created > 0) {
        console.log('[CSVImportModal] Calling onSuccess callback...');
        onSuccess();
      } else {
        console.log('[CSVImportModal] No channels/groups created, skipping refresh');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
      setImportState('error');
    }
  }, [file, onSuccess]);

  if (!isOpen) return null;

  const hasValidationErrors = preview && preview.errors.length > 0;
  const canImport = file && preview && preview.rows.length > 0 && importState !== 'importing';

  return (
    <ModalOverlay onClose={handleClose} className="modal-overlay csv-import-modal" data-testid="csv-import-modal">
      <div className="modal-container modal-lg">
        <div className="modal-header">
          <h2>
            <span className="material-icons">upload_file</span>
            Import Channels from CSV
          </h2>
          <button className="modal-close-btn" onClick={handleClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {/* Error Banner */}
          {error && (
            <div className="modal-error-banner" data-testid="csv-errors">
              <span className="material-icons">error</span>
              <span>{error}</span>
            </div>
          )}

          {/* Success State */}
          {importState === 'success' && importResult && (
            <div className="csv-import-success success-message" data-testid="csv-success">
              <span className="material-icons">check_circle</span>
              <div className="success-details">
                <strong>Import Completed!</strong>
                <p>
                  Created {importResult.channels_created} channel{importResult.channels_created !== 1 ? 's' : ''}
                  {importResult.groups_created > 0 && (
                    <> and {importResult.groups_created} new group{importResult.groups_created !== 1 ? 's' : ''}</>
                  )}
                  {importResult.streams_linked > 0 && (
                    <>, linked {importResult.streams_linked} stream{importResult.streams_linked !== 1 ? 's' : ''}</>
                  )}
                </p>
                {importResult.warnings.length > 0 && (
                  <div className="success-warnings">
                    <span className="material-icons">warning</span>
                    {importResult.warnings.length} warning{importResult.warnings.length !== 1 ? 's' : ''}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* File Upload Dropzone */}
          {importState !== 'success' && (
            <div
              className={`csv-dropzone dropzone file-upload-area ${isDragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              data-testid="csv-dropzone"
            >
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={handleFileInput}
                className="csv-file-input"
                id="csv-file-input"
              />
              <label htmlFor="csv-file-input" className="dropzone-content">
                <span className="material-icons dropzone-icon">
                  {file ? 'description' : 'cloud_upload'}
                </span>
                {file ? (
                  <div className="file-info">
                    <span className="file-name">{file.name}</span>
                    <span className="file-size">({(file.size / 1024).toFixed(1)} KB)</span>
                  </div>
                ) : (
                  <>
                    <span className="dropzone-text">Drop CSV file here or click to browse</span>
                    <span className="dropzone-hint">Supports .csv files</span>
                  </>
                )}
              </label>
            </div>
          )}

          {/* Loading State */}
          {importState === 'previewing' && (
            <div className="modal-loading loading" data-testid="csv-progress">
              <span className="material-icons modal-spinning-ccw">sync</span>
              <span>Parsing CSV...</span>
            </div>
          )}

          {/* Importing State */}
          {importState === 'importing' && (
            <div className="modal-loading loading progress" data-testid="csv-progress">
              <span className="material-icons modal-spinning-ccw">sync</span>
              <span>Importing channels...</span>
            </div>
          )}

          {/* Preview Table */}
          {preview && preview.rows.length > 0 && importState !== 'success' && (
            <div className="csv-preview" data-testid="csv-preview-table">
              <div className="preview-header">
                <span className="material-icons">table_chart</span>
                <span>Preview: {preview.rows.length} channel{preview.rows.length !== 1 ? 's' : ''} to import</span>
              </div>
              <div className="preview-table-wrapper">
                <table className="csv-preview-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Channel #</th>
                      <th>Group</th>
                      <th>TVG ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.slice(0, 10).map((row, index) => (
                      <tr key={index} data-testid="csv-preview-row">
                        <td>{row.name || <span className="empty-value">-</span>}</td>
                        <td>{row.channel_number || <span className="empty-value">Auto</span>}</td>
                        <td>{row.group_name || <span className="empty-value">-</span>}</td>
                        <td>{row.tvg_id || <span className="empty-value">-</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {preview.rows.length > 10 && (
                  <div className="preview-more">
                    ...and {preview.rows.length - 10} more
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Validation Errors */}
          {hasValidationErrors && importState !== 'success' && (
            <div className="csv-validation-errors validation-errors" data-testid="csv-errors">
              <div className="errors-header">
                <span className="material-icons">warning</span>
                <span>{preview.errors.length} validation error{preview.errors.length !== 1 ? 's' : ''}</span>
              </div>
              <ul className="errors-list">
                {preview.errors.slice(0, 5).map((err, index) => (
                  <li key={index} data-testid="csv-error-item">
                    <strong>Row {err.row}:</strong> {err.error}
                  </li>
                ))}
                {preview.errors.length > 5 && (
                  <li className="more-errors">
                    ...and {preview.errors.length - 5} more error{preview.errors.length - 5 !== 1 ? 's' : ''}
                  </li>
                )}
              </ul>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-secondary"
            onClick={handleClose}
          >
            {importState === 'success' ? 'Close' : 'Cancel'}
          </button>
          {importState !== 'success' && (
            <button
              type="button"
              className="modal-btn modal-btn-primary primary"
              onClick={handleImport}
              disabled={!canImport}
              data-testid="csv-import-submit"
            >
              <span className="material-icons">upload</span>
              Import {preview?.rows.length || 0} Channel{(preview?.rows.length || 0) !== 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
});
